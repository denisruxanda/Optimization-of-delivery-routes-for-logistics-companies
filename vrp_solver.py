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

def solve_vrp(start_city, pd_requests, coords, vehicle_profiles, routing_mode, src_map=None):
    # PATCH: vehicle_count = len(vehicle_profiles) dacă ai profile_virtual deja "expandat"
    vehicle_count = len(vehicle_profiles)
    capacities = [vp['capacitate'] for vp in vehicle_profiles]
    pickups = [r['pickup'] for r in pd_requests]
    deliveries = [r['delivery'] for r in pd_requests]
    
    # Check if we need to force multiple vehicle usage
    total_demand = sum(r['demand'] for r in pd_requests)
    max_capacity = max(capacities)
    
    # If total demand exceeds max capacity, manually assign orders to vehicles
    if total_demand > max_capacity:
        print(f"DEBUG: Total demand ({total_demand}kg) exceeds max capacity ({max_capacity}kg). Using manual assignment.")
        
        # Sort vehicles by capacity (largest first)
        vehicle_capacities = [(i, cap) for i, cap in enumerate(capacities)]
        vehicle_capacities.sort(key=lambda x: x[1], reverse=True)
        
        # Sort orders by demand (largest first)
        orders = [(i, r) for i, r in enumerate(pd_requests)]
        orders.sort(key=lambda x: x[1]['demand'], reverse=True)
        
        # Assign orders to vehicles
        vehicle_assignments = [[] for _ in range(vehicle_count)]
        vehicle_loads = [0] * vehicle_count
        
        for order_idx, order in orders:
            assigned = False
            for veh_idx, veh_cap in vehicle_capacities:
                if vehicle_loads[veh_idx] + order['demand'] <= veh_cap:
                    vehicle_assignments[veh_idx].append(order_idx)
                    vehicle_loads[veh_idx] += order['demand']
                    assigned = True
                    break
            
            if not assigned:
                print(f"DEBUG: Cannot assign order {order_idx} to any vehicle!")
                return None
        
        print(f"DEBUG: Vehicle assignments: {vehicle_assignments}")
        print(f"DEBUG: Vehicle loads: {vehicle_loads}")
        
        # Now solve separate VRP problems for each vehicle
        all_routes = []
        all_polylines = []
        total_cost = 0.0
        
        for veh_idx, order_indices in enumerate(vehicle_assignments):
            if not order_indices:  # Skip unused vehicles
                continue
                
            # Create sub-problem for this vehicle
            sub_requests = [pd_requests[i] for i in order_indices]
            sub_vehicle_profiles = [vehicle_profiles[veh_idx]]
            sub_src_map = [src_map[veh_idx]] if src_map else None
            
            # Solve sub-problem
            sub_result = solve_vrp_single_vehicle(
                start_city, sub_requests, coords, sub_vehicle_profiles[0], 
                routing_mode, sub_src_map
            )
            
            if sub_result:
                sub_routes, sub_cost, sub_polylines = sub_result
                all_routes.extend(sub_routes)
                all_polylines.extend(sub_polylines)
                total_cost += sub_cost
            else:
                print(f"DEBUG: Failed to solve sub-problem for vehicle {veh_idx}")
                return None
        
        return all_routes, total_cost, all_polylines
    
    # Original solver for when capacity constraints are not violated
    return solve_vrp_original(start_city, pd_requests, coords, vehicle_profiles, routing_mode, src_map)

def solve_vrp_single_vehicle(start_city, pd_requests, coords, vehicle_profile, routing_mode, src_map=None):
    """Solve VRP for a single vehicle with multiple orders."""
    # Create a single vehicle profile list
    vehicle_profiles = [vehicle_profile]
    src_map_list = [src_map] if src_map else None
    
    # Use the original solver with single vehicle
    return solve_vrp_original(start_city, pd_requests, coords, vehicle_profiles, routing_mode, src_map_list)

def solve_vrp_original(start_city, pd_requests, coords, vehicle_profiles, routing_mode, src_map=None):
    # PATCH: vehicle_count = len(vehicle_profiles) dacă ai profile_virtual deja "expandat"
    vehicle_count = len(vehicle_profiles)
    capacities = [vp['capacitate'] for vp in vehicle_profiles]
    pickups = [r['pickup'] for r in pd_requests]
    deliveries = [r['delivery'] for r in pd_requests]
    
    # Create unique cities list, ensuring depot is first
    cities = [start_city]
    for city in pickups + deliveries:
        if city not in cities:
            cities.append(city)
    
    idx = {city: i for i, city in enumerate(cities)}

    G = build_graph(coords)
    dist_matrix = [
        [get_distance(G, a, b) if a != b else 0 for b in cities]
        for a in cities
    ]
    time_matrix = [
        [get_duration(G, a, b) if a != b else 0 for b in cities]
        for a in cities
    ]

    manager = pywrapcp.RoutingIndexManager(len(cities), vehicle_count, idx[start_city])
    routing = pywrapcp.RoutingModel(manager)

    # Use appropriate cost function based on mode
    if routing_mode == "Fast":
        # Fast mode: minimize total delivery time and delays
        def time_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            # Focus on minimizing delivery time - no distance penalty
            return int(time_matrix[from_node][to_node] * 3600)
        time_cb = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(time_cb)
    else:  # Economic mode
        # Economic mode: minimize distance and vehicle usage while avoiding delays
        def distance_callback(from_index, to_index):
            from_node = manager.IndexToNode(from_index)
            to_node = manager.IndexToNode(to_index)
            # Focus on minimizing distance and resource usage
            return int(dist_matrix[from_node][to_node] * 1000)
        dist_cb = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(dist_cb)

    # Capacitate
    demands = [0] * len(cities)
    for r in pd_requests:
        pickup_idx = idx[r['pickup']]
        delivery_idx = idx[r['delivery']]
        demands[pickup_idx] += r['demand']
        demands[delivery_idx] -= r['demand']

    # Debug demand calculation
    print(f"DEBUG: Demands array: {demands}")
    print(f"DEBUG: Cities: {cities}")
    print(f"DEBUG: Pickup demands: {[demands[idx[r['pickup']]] for r in pd_requests]}")
    print(f"DEBUG: Total pickup demand: {sum(demands[idx[r['pickup']]] for r in pd_requests)}")

    # RE-ENABLE CAPACITY CONSTRAINTS
    def demand_callback(from_index):
        return demands[manager.IndexToNode(from_index)]
    demand_cb = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb, 0, capacities, True, 'Capacity'
    )
    cap_dim = routing.GetDimensionOrDie('Capacity')

    # Add constraint to force multiple vehicles when needed
    total_demand = sum(r['demand'] for r in pd_requests)
    max_capacity = max(capacities)
    
    # Add a penalty for not using vehicles to encourage multiple vehicle usage
    if total_demand > max_capacity:
        # Add a fixed cost for each vehicle to encourage using multiple vehicles
        for vid in range(vehicle_count):
            routing.SetFixedCostOfVehicle(1000, vid)  # Small fixed cost per vehicle
    
    # Add a constraint to ensure capacity is not exceeded at any point
    for vid in range(vehicle_count):
        routing.solver().Add(cap_dim.CumulVar(routing.End(vid)) <= capacities[vid])
    
    # Remove the problematic IfThenElse constraint - let the basic capacity constraints handle it

    # Pickup & delivery
    # TEMPORARILY DISABLE PICKUP & DELIVERY FOR MULTIPLE ORDERS
    if len(pd_requests) <= 3:  # Only enable for small number of orders
        # RE-ENABLE PICKUP & DELIVERY CONSTRAINTS (simplified)
        for r in pd_requests:
            p_idx = manager.NodeToIndex(idx[r['pickup']])
            d_idx = manager.NodeToIndex(idx[r['delivery']])
            routing.AddPickupAndDelivery(p_idx, d_idx)
            routing.solver().Add(routing.VehicleVar(p_idx) == routing.VehicleVar(d_idx))
            # Add capacity constraint for pickup and delivery
            routing.solver().Add(cap_dim.CumulVar(p_idx) <= cap_dim.CumulVar(d_idx))
            # Ensure capacity is not exceeded at pickup
            routing.solver().Add(cap_dim.CumulVar(p_idx) <= max_capacity)

    # TIME DIMENSION PE BAZĂ DE GRAFI
    # TEMPORARILY DISABLE TIME DIMENSION
    # service_time = [2 * 3600] * len(cities)
    # def full_time_callback(from_index, to_index):
    #     from_node = manager.IndexToNode(from_index)
    #     to_node = manager.IndexToNode(to_index)
    #     return int(time_matrix[from_node][to_node] * 3600 + service_time[from_node])
    # full_time_cb = routing.RegisterTransitCallback(full_time_callback)
    # routing.AddDimension(
    #     full_time_cb,
    #     0,
    #     int(999 * 3600),  # maxim permis
    #     True,
    #     'Time'
    # )

    # Parametri solver - Proper routing modes
    params = pywrapcp.DefaultRoutingSearchParameters()
    if routing_mode == "Economic":
        # Economic mode: minimize distance and use fewer vehicles
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        params.time_limit.seconds = 30
        # Focus on minimizing vehicles and distance
        params.solution_limit = 100
    elif routing_mode == "Fast":
        # Fast mode: prioritize delivery time and minimize delays
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH
        params.time_limit.seconds = 25
        # Focus on finding fast solutions quickly
        params.solution_limit = 50
    else:
        # Default fallback
        params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        params.time_limit.seconds = 25
        params.solution_limit = 75

    # Check if the problem is feasible
    # Check if all cities are reachable from depot
    for city in cities[1:]:  # Skip depot
        try:
            path_length = get_distance(G, start_city, city)
        except Exception as e:
            return None
    
    # Check if any single vehicle can handle the demand
    max_capacity = max(capacities)
    max_demand = max(r['demand'] for r in pd_requests)
    total_demand = sum(r['demand'] for r in pd_requests)
    
    # Try solving without pickup & delivery constraints first
    solution = routing.SolveWithParameters(params)
    
    if not solution:
        return None

    # Debug: Check capacity usage for each vehicle
    print(f"DEBUG: Solution found. Checking capacity usage...")
    for vid in range(vehicle_count):
        index = routing.Start(vid)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            print(f"DEBUG: Vehicle {vid} not used")
        else:
            end_index = routing.End(vid)
            final_capacity = cap_dim.CumulVar(end_index)
            capacity_used = solution.Value(final_capacity)
            print(f"DEBUG: Vehicle {vid} used, capacity: {capacity_used}/{capacities[vid]}")

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
            route_seq.append(cities[node_id])
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
        steps.append({'tip': 'plecare', 'oras': start_city, 'distanta': 0, 'comanda': None})
        for a, b in zip(full_path, full_path[1:]):
            d = get_distance(G, a, b)
            dur = get_duration(G, a, b)
            steps.append({
                'tip': detect_step_type(b, pd_requests),
                'oras': b,
                'distanta': d,
                'durata': dur,
                'comanda': detect_command(b, pd_requests)
            })
        steps.append({'tip': 'intoarcere', 'oras': start_city, 'distanta': 0, 'durata': 0, 'comanda': None})

        total_dist = sum(step['distanta'] for step in steps)
        total_cost += total_dist

        # PATCH: etichetare vehicul
        if src_map:
            src_idx = src_map[vid]
            veh_name = vehicle_profiles[src_idx]['nume'] if 'nume' in vehicle_profiles[src_idx] else f"Vehicul {src_idx+1}"
            vehicul_label = veh_name
        else:
            vehicul_label = f"Vehicul {vid+1}"

        route_data.append({
            'vehicul': vehicle_profiles[vid],
            'traseu': steps,
            'cost_km': round(total_dist, 2),
        })

    return route_data, round(total_cost, 2), polylines