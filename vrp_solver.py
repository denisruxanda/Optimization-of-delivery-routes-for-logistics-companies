from math import radians, sin, cos, sqrt, atan2
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from graph_builder import build_graph, get_distance, get_duration, get_path

__all__ = ["solve_vrp"]

# ---- constants ----
SECONDS_PER_HOUR = 3600
METERS_PER_KM = 1000
DEFAULT_SERVICE_TIME = 2           # hours (service at pickup/delivery)
MAX_TIME_LIMIT = 999               # hours (relaxed windows)
TIME_WINDOW_COEFFICIENT = 100
SOLVER_TIME_LIMIT_SECONDS = 10
TIME_OPTIMIZATION_LIMIT_SECONDS = 20
FALLBACK_SPEED_KMPH = 60           # used if graph has no path

# encourage chaining multiple orders on same truck
VEHICLE_STARTUP_COST_KM = 200      # penalty to open a vehicle when cost=distance
VEHICLE_STARTUP_COST_HOURS = 2     # penalty to open a vehicle when cost=time

# ---- helpers ----
def _haversine_km(a, b):
    R = 6371.0
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return 2 * R * atan2(sqrt(x), sqrt(1-x))

def _safe_graph_distance(G, coords, ci, cj):
    try:
        return get_distance(G, ci, cj)
    except Exception:
        pa, pb = coords[ci]['coords'], coords[cj]['coords']
        return _haversine_km(pa, pb)

def _safe_graph_duration(G, coords, ci, cj):
    try:
        return get_duration(G, ci, cj)
    except Exception:
        pa, pb = coords[ci]['coords'], coords[cj]['coords']
        dist = _haversine_km(pa, pb)
        return dist / max(FALLBACK_SPEED_KMPH, 1e-6)

def _expand_leg_to_steps(G, coords, a, b, step_type_on_arrival, order_meta=None):
    # per-segment steps along shortest path a->b; mark pickup/delivery only on final node b
    try:
        seg_path = get_path(G, a, b)
    except Exception:
        seg_path = [a, b]

    steps = []
    poly_coords = [coords[seg_path[0]]['coords']]
    for i in range(1, len(seg_path)):
        prev_city = seg_path[i - 1]
        city = seg_path[i]
        dkm = _safe_graph_distance(G, coords, prev_city, city)
        th = _safe_graph_duration(G, coords, prev_city, city)

        row = {'tip': "intermediar", 'oras': city, 'distanta': dkm, 'durata': th}

        # only last node of the leg gets pickup/delivery/return semantics
        if i == len(seg_path) - 1:
            row['tip'] = step_type_on_arrival
            if order_meta:
                oid = order_meta.get('id')
                row['order_id'] = oid
                row['comanda'] = oid                 # backward-compat UI
                row['order_pickup'] = order_meta.get('pickup')
                row['order_delivery'] = order_meta.get('delivery')
                # include deadline on BOTH pickup and delivery, so the table knows it early
                row['time_limit'] = order_meta.get('time_limit_hrs')

        steps.append(row)
        poly_coords.append(coords[city]['coords'])
    return steps, poly_coords

def _estimate_leg_hours(G, coords, a, b):
    try:
        return _safe_graph_duration(G, coords, a, b)
    except Exception:
        pa, pb = coords[a]['coords'], coords[b]['coords']
        d = _haversine_km(pa, pb)
        return d / max(FALLBACK_SPEED_KMPH, 1e-6)

# ---- solver (PUBLIC) ----
def solve_vrp(start_city, pd_requests, coords, vehicle_profiles, routing_mode, allow_split=True, src_map=None):
    pickups = [r['pickup'] for r in pd_requests]
    deliveries = [r['delivery'] for r in pd_requests]
    cities = [start_city] + list(dict.fromkeys(pickups + deliveries))

    G = build_graph(coords)
    n = len(cities)
    city_index = {c: i for i, c in enumerate(cities)}
    dist_m = [[0]*n for _ in range(n)]
    time_m = [[0]*n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                d = t = 0
            else:
                a, b = cities[i], cities[j]
                d = _safe_graph_distance(G, coords, a, b)
                t = _safe_graph_duration(G, coords, a, b)
            dist_m[i][j] = int(round(d))
            time_m[i][j] = int(round(t))

    vehicle_count = max(1, len(vehicle_profiles))
    capacities = [vp['capacitate'] for vp in vehicle_profiles] if vehicle_profiles else [10**9]

    # node list: depot + (pickup, delivery)*
    node_list = [start_city]
    node_types = ['depot']
    order_idx = [-1]
    for i, order in enumerate(pd_requests):
        node_list += [order['pickup'], order['delivery']]
        node_types += ['pickup', 'delivery']
        order_idx += [i, i]

    N = len(node_list)
    manager = pywrapcp.RoutingIndexManager(N, vehicle_count, 0)
    routing = pywrapcp.RoutingModel(manager)

    # arc cost
    time_mode = routing_mode in ("Timp minim", "Fast")
    if time_mode:
        def time_cb(from_index, to_index):
            fi = manager.IndexToNode(from_index); ti = manager.IndexToNode(to_index)
            a = node_list[fi]; b = node_list[ti]
            return int(time_m[city_index[a]][city_index[b]] * SECONDS_PER_HOUR)
        cb = routing.RegisterTransitCallback(time_cb)
        routing.SetArcCostEvaluatorOfAllVehicles(cb)
        routing.SetFixedCostOfAllVehicles(int(VEHICLE_STARTUP_COST_HOURS * SECONDS_PER_HOUR))
    else:
        def dist_cb(from_index, to_index):
            fi = manager.IndexToNode(from_index); ti = manager.IndexToNode(to_index)
            a = node_list[fi]; b = node_list[ti]
            return int(dist_m[city_index[a]][city_index[b]] * METERS_PER_KM)
        cb = routing.RegisterTransitCallback(dist_cb)
        routing.SetArcCostEvaluatorOfAllVehicles(cb)
        routing.SetFixedCostOfAllVehicles(int(VEHICLE_STARTUP_COST_KM * METERS_PER_KM))

    # capacity (kg)
    demands = [0]*N
    for i, t in enumerate(node_types):
        if t == 'pickup':
            demands[i] = pd_requests[order_idx[i]]['demand']
        elif t == 'delivery':
            demands[i] = -pd_requests[order_idx[i]]['demand']

    def demand_cb(index):
        node = manager.IndexToNode(index)
        return demands[node]

    dcb = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(dcb, 0, capacities, True, 'Capacity')
    cap_dim = routing.GetDimensionOrDie('Capacity')

    # pickup-delivery constraints
    for i, _ in enumerate(pd_requests):
        p = 1 + 2*i
        d = 1 + 2*i + 1
        routing.AddPickupAndDelivery(manager.NodeToIndex(p), manager.NodeToIndex(d))
        routing.solver().Add(
            routing.VehicleVar(manager.NodeToIndex(p)) == routing.VehicleVar(manager.NodeToIndex(d))
        )
        routing.solver().Add(
            cap_dim.CumulVar(manager.NodeToIndex(p)) <= cap_dim.CumulVar(manager.NodeToIndex(d))
        )

    # time dimension (travel + service-at-from), relaxed
    svc = [int(DEFAULT_SERVICE_TIME * SECONDS_PER_HOUR)] * N
    def full_time_cb(from_index, to_index):
        fi = manager.IndexToNode(from_index); ti = manager.IndexToNode(to_index)
        a = node_list[fi]; b = node_list[ti]
        return int(time_m[city_index[a]][city_index[b]] * SECONDS_PER_HOUR + svc[fi])

    ft_idx = routing.RegisterTransitCallback(full_time_cb)
    routing.AddDimension(ft_idx, 0, int(MAX_TIME_LIMIT * SECONDS_PER_HOUR), True, 'Time')
    time_dim = routing.GetDimensionOrDie('Time')
    for i in range(N):
        idx = manager.NodeToIndex(i)
        time_dim.CumulVar(idx).SetRange(0, int(MAX_TIME_LIMIT * SECONDS_PER_HOUR))
    time_dim.SetGlobalSpanCostCoefficient(TIME_WINDOW_COEFFICIENT)

    # search params
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    if time_mode:
        p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        p.time_limit.seconds = TIME_OPTIMIZATION_LIMIT_SECONDS
    else:
        p.time_limit.seconds = SOLVER_TIME_LIMIT_SECONDS

    solution = routing.SolveWithParameters(p)

    # ---------- fallback (chained, per-vehicle) ----------
    if not solution:
        routes, polylines = [], []
        vcount = vehicle_count or 1
        if vcount == 0:
            vehicle_profiles = [{"nume": "Vehicle", "capacitate": 10**9, "echipaj": False, "numar": 1}]
            vcount = 1

        # assign orders (tightest deadline first), by minimal added drive-time
        order_ids = list(range(len(pd_requests)))
        order_ids.sort(key=lambda k: float(pd_requests[k].get("time_limit_hrs", MAX_TIME_LIMIT)))

        assignments = [[] for _ in range(vcount)]
        last_city = [start_city for _ in range(vcount)]
        for oid in order_ids:
            o = pd_requests[oid]
            best_v = None
            best_cost = float('inf')
            for vid in range(vcount):
                cap = vehicle_profiles[vid].get("capacitate", 10**9)
                if o.get("demand", 0) > cap:
                    continue
                c = _estimate_leg_hours(G, coords, last_city[vid], o['pickup']) + \
                    _estimate_leg_hours(G, coords, o['pickup'], o['delivery'])
                if c < best_cost:
                    best_cost = c; best_v = vid
            if best_v is None:
                best_v = min(range(vcount), key=lambda vid:
                             _estimate_leg_hours(G, coords, last_city[vid], o['pickup']) +
                             _estimate_leg_hours(G, coords, o['pickup'], o['delivery']))
            assignments[best_v].append(oid)
            last_city[best_v] = o['delivery']

        # build chained routes: depot -> (p/d)* -> depot
        for vid in range(vcount):
            veh = vehicle_profiles[vid]
            steps = [{'tip': 'plecare', 'oras': start_city, 'distanta': 0, 'durata': 0, 'comanda': None}]
            polyline = []
            cur = start_city

            for oid in assignments[vid]:
                o = pd_requests[oid]
                s_steps, s_poly = _expand_leg_to_steps(G, coords, cur, o['pickup'], "pickup", order_meta=o)
                steps += s_steps; polyline += (s_poly if not polyline else s_poly[1:]); cur = o['pickup']
                s_steps, s_poly = _expand_leg_to_steps(G, coords, cur, o['delivery'], "delivery", order_meta=o)
                steps += s_steps; polyline += (s_poly if not polyline else s_poly[1:]); cur = o['delivery']

            if cur != start_city:
                s_steps, s_poly = _expand_leg_to_steps(G, coords, cur, start_city, "intoarcere", order_meta=None)
                steps += s_steps; polyline += (s_poly if not polyline else s_poly[1:])

            polylines.append(polyline)
            routes.append({'vehicul': veh, 'traseu': steps})

        return routes, polylines, 0.0

    # ---------- extract OR-Tools solution ----------
    routes, polylines, total_cost = [], [], 0.0
    for vid in range(vehicle_count):
        index = routing.Start(vid)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue

        seq_nodes = []
        while not routing.IsEnd(index):
            node_id = manager.IndexToNode(index)
            seq_nodes.append(node_list[node_id])
            index = solution.Value(routing.NextVar(index))
        seq_nodes.append(start_city)

        steps = [{'tip': 'plecare', 'oras': start_city, 'distanta': 0, 'durata': 0, 'comanda': None}]
        polyline = []
        picked = set(); onboard = set()

        for a, b in zip(seq_nodes[:-1], seq_nodes[1:]):
            arr_type = "intermediar"
            arr_order_meta = None

            for j, order in enumerate(pd_requests):
                if b == order['pickup'] and j not in picked:
                    arr_type = "pickup"; arr_order_meta = order
                    picked.add(j); onboard.add(j); break

            if arr_type == "intermediar":
                for j, order in enumerate(pd_requests):
                    if b == order['delivery'] and j in onboard:
                        arr_type = "delivery"; arr_order_meta = order
                        onboard.remove(j); break

            if b == start_city and arr_type == "intermediar":
                arr_type = "intoarcere"

            leg_steps, leg_poly = _expand_leg_to_steps(G, coords, a, b, arr_type, order_meta=arr_order_meta)
            steps += leg_steps
            polyline += (leg_poly if not polyline else leg_poly[1:])

        polylines.append(polyline)
        routes.append({'vehicul': vehicle_profiles[vid], 'traseu': steps})

    return routes, polylines, 0.0
