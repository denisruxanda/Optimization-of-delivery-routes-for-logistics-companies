import networkx as nx
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from graph_builder import build_graph, get_distance, get_duration, get_path

def detect_step_type(city, requests):
    for i, r in enumerate(requests):
        if city == r["pickup"]:
            return "pickup"
        if city == r["delivery"]:
            return "delivery"
    return "intermediar"

def detect_command(city, requests):
    for i, r in enumerate(requests):
        if city == r["pickup"] or city == r["delivery"]:
            return i + 1
    return None

def solve_vrp(start_city, pd_requests, coords, vehicle_profiles, routing_mode, allow_split=True, src_map=None):
    # PATCH: vehicle_count = len(vehicle_profiles) dacă ai profile_virtual deja "expandat"
    vehicle_count = len(vehicle_profiles)
    capacities = [vp['capacitate'] for vp in vehicle_profiles]
    pickups = [r['pickup'] for r in pd_requests]
    deliveries = [r['delivery'] for r in pd_requests]
    cities = [start_city] + list(dict.fromkeys(pickups + deliveries))
    idx = {city: i for i, city in enumerate(cities)}

    G = build_graph(coords)
    dist_matrix = [[0]*len(cities) for _ in range(len(cities))]
    time_matrix = [[0]*len(cities) for _ in range(len(cities))]

    for i in range(len(cities)):
        for j in range(len(cities)):
            if i == j:
                dist = 0
                dur = 0
            else:
                city_i = cities[i]
                city_j = cities[j]
                dist = get_distance(G, city_i, city_j)
                dur = get_duration(G, city_i, city_j)
            dist_matrix[i][j] = int(round(dist))
            time_matrix[i][j] = int(round(dur))

    # Build node list: depot, then all pickups, then all deliveries
    node_list = []
    node_types = []  # 'depot', 'pickup', 'delivery'
    node_cities = [] # city name for each node
    order_indices = [] # -1 for depot, otherwise order index

    # 0: depot
    node_list.append(start_city)
    node_types.append('depot')
    node_cities.append(start_city)
    order_indices.append(-1)

    # For each order, add pickup and delivery nodes
    for i, order in enumerate(pd_requests):
        # Pickup node
        node_list.append(order['pickup'])
        node_types.append('pickup')
        node_cities.append(order['pickup'])
        order_indices.append(i)
        # Delivery node
        node_list.append(order['delivery'])
        node_types.append('delivery')
        node_cities.append(order['delivery'])
        order_indices.append(i)

    N = len(node_list)
    manager = pywrapcp.RoutingIndexManager(N, vehicle_count, 0)
    routing = pywrapcp.RoutingModel(manager)

    if routing_mode == "Timp minim":
        def time_callback(from_index, to_index):
            return int(time_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)] * 3600)
        time_cb = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(time_cb)
    else:
        def distance_callback(from_index, to_index):
            return int(dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)] * 1000)
        dist_cb = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(dist_cb)

    # Capacitate
    demands = [0] * len(node_list)
    for i, t in enumerate(node_types):
        if t == 'pickup':
            demands[i] = pd_requests[order_indices[i]]['demand']
        elif t == 'delivery':
            demands[i] = -pd_requests[order_indices[i]]['demand']

    def demand_callback(from_index):
        return demands[manager.IndexToNode(from_index)]
    demand_cb = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb, 0, capacities, True, 'Capacity'
    )
    cap_dim = routing.GetDimensionOrDie('Capacity')

    # Pickup & delivery
    for i, order in enumerate(pd_requests):
        pickup_idx = 1 + 2*i      # pickup node index in node_list
        delivery_idx = 1 + 2*i+1  # delivery node index in node_list
        routing.AddPickupAndDelivery(manager.NodeToIndex(pickup_idx), manager.NodeToIndex(delivery_idx))
        routing.solver().Add(routing.VehicleVar(manager.NodeToIndex(pickup_idx)) == routing.VehicleVar(manager.NodeToIndex(delivery_idx)))
        routing.solver().Add(cap_dim.CumulVar(manager.NodeToIndex(pickup_idx)) <= cap_dim.CumulVar(manager.NodeToIndex(delivery_idx)))

    # TIME DIMENSION PE BAZĂ DE GRAFI
    service_time = [2 * 3600] * len(node_list)
    def full_time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(time_matrix[from_node][to_node] * 3600 + service_time[from_node])
    full_time_cb = routing.RegisterTransitCallback(full_time_callback)
    routing.AddDimension(
        full_time_cb,
        0,
        int(999 * 3600),
        True,
        'Time'
    )
    time_dim = routing.GetDimensionOrDie('Time')
    # Set time windows for each node
    for i, t in enumerate(node_types):
        index = manager.NodeToIndex(i)
        if t == 'depot' or t == 'pickup':
            # Depot and pickup: wide time window
            time_dim.CumulVar(index).SetRange(0, int(999 * 3600))
        elif t == 'delivery':
            # Set time window for delivery
            order = pd_requests[order_indices[i]]
            time_dim.CumulVar(index).SetRange(0, int(order['time_limit_hrs'] * 3600))
        else:
            # All other cities: wide time window
            time_dim.CumulVar(index).SetRange(0, int(999 * 3600))

    # After adding the 'Time' dimension
    time_dim.SetGlobalSpanCostCoefficient(100)  # Tune this value

    # Parametri solver
    params = pywrapcp.DefaultRoutingSearchParameters()
    if routing_mode == "Număr minim de vehicule":
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    elif routing_mode == "Timp minim":
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.seconds = 20
    else:
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.time_limit.seconds = 10

    solution = routing.SolveWithParameters(params)
    if not solution:
        return None

    route_data = []
    polylines = []
    total_cost = 0.0

    for vid in range(vehicle_count):
        index = routing.Start(vid)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue
        route_seq = []
        while not routing.IsEnd(index):
            node_id = manager.IndexToNode(index)
            route_seq.append(node_list[node_id])
            index = solution.Value(routing.NextVar(index))
        route_seq.append(start_city)

        # Construcție traseu cu TOATE orașele intermediare din graf
        full_path = []
        for i in range(len(route_seq) - 1):
            segment = get_path(G, route_seq[i], route_seq[i+1])
            if i > 0:
                segment = segment[1:]
            full_path += segment

        polylines.append([coords[c]['coords'] for c in full_path])

        steps = []
        picked_up = set()
        delivered = set()
        orders_onboard = set()
        current_capacity = 0

        # Plecare din depot
        steps.append({'tip': 'plecare', 'oras': start_city, 'distanta': 0, 'durata': 0, 'comanda': None})

        for i in range(1, len(full_path)):
            a = full_path[i - 1]
            b = full_path[i]
            d = get_distance(G, a, b)
            dur = get_duration(G, a, b)
            step_type = "intermediar"
            comanda_id = None

            # Caută întâi pickup
            for j, order in enumerate(pd_requests):
                if b == order['pickup'] and j not in picked_up:
                    if current_capacity + order['demand'] <= capacities[vid]:
                        step_type = "pickup"
                        comanda_id = j + 1
                        picked_up.add(j)
                        orders_onboard.add(j)
                        current_capacity += order['demand']
                        break

            # Apoi delivery (doar dacă s-a făcut pickup deja)
            for j, order in enumerate(pd_requests):
                if b == order['delivery'] and j in orders_onboard and j not in delivered:
                    step_type = "delivery"
                    comanda_id = j + 1
                    delivered.add(j)
                    orders_onboard.remove(j)
                    current_capacity -= order['demand']
                    break

            # Dacă nu e nici pickup nici delivery pentru vehiculul curent, e tranzit/intermediar
            steps.append({
                'tip': step_type,
                'oras': b,
                'distanta': d,
                'durata': dur,
                'comanda': comanda_id
            })

        # -- Return to depot la final (dacă nu e deja acolo)
        if len(full_path) > 1:
            a = full_path[-2]
            b = full_path[-1]
            d = get_distance(G, a, b)
            dur = get_duration(G, a, b)
        else:
            d = 0
            dur = 0
        steps.append({'tip': 'intoarcere', 'oras': start_city, 'distanta': d, 'durata': dur, 'comanda': None})

    for i, city in enumerate(node_list):
        index = manager.NodeToIndex(i)
        print(f"City: {city}, Time window: [{time_dim.CumulVar(index).Min() / 3600}, {time_dim.CumulVar(index).Max() / 3600}]")

    return route_data, round(total_cost, 2), polylines