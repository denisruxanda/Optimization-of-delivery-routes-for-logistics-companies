import streamlit as st
from graph_builder import build_graph
from vrp_solver import solve_vrp
from map_view import draw_initial_map, draw_route_map
from table_view import draw_table
import json

def load_coordinates(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_road_data(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)

st.set_page_config(page_title="Delivery Route Optimization", layout="wide")

# ---- State ----
if "vehicle_profiles" not in st.session_state:
    st.session_state.vehicle_profiles = []
if "edit_vehicle_index" not in st.session_state:
    st.session_state.edit_vehicle_index = -1
if "requests" not in st.session_state:
    st.session_state.requests = []
if "edit_index" not in st.session_state:
    st.session_state.edit_index = -1
if "last_routes" not in st.session_state:
    st.session_state.last_routes = []
if "last_cost" not in st.session_state:
    st.session_state.last_cost = 0
if "last_polylines" not in st.session_state:
    st.session_state.last_polylines = []
if "routes_generated" not in st.session_state:
    st.session_state.routes_generated = False
if "allow_split" not in st.session_state:
    st.session_state.allow_split = True

city_coords = load_coordinates("coords.json")
road_data = load_road_data("roads.json")
cities = [c for c, v in city_coords.items() if v.get("visible", False)]
placeholder = "Select from the list or type"
cities_placeholder = [placeholder] + cities

# ---- Select depot first, separate from forms! ----
selected_hq = st.sidebar.selectbox(
    "Depot",
    options=cities_placeholder,
    index=0,
    key="hq_select"
)
if selected_hq == placeholder:
    start_city = None
else:
    start_city = selected_hq

# ----------------- Fleet Sidebar ----------------
st.sidebar.header("🚚 Fleet configuration")
st.sidebar.info(
    "All freight vehicles in the fleet are considered, according to European legislation, to be equipped with a tachograph."
)

with st.sidebar.form("fleet_form", clear_on_submit=False):
    idx = st.session_state.edit_vehicle_index
    if idx != -1 and len(st.session_state.vehicle_profiles) > idx:
        vdata = st.session_state.vehicle_profiles[idx]
        v_name = st.text_input("Vehicle name", vdata['nume'])
        v_cap = st.number_input("Capacity (kg)", min_value=1, value=int(vdata['capacitate']), step=100)
        v_ech = st.checkbox("Crew of 2 drivers?", value=vdata.get('echipaj', False))
        v_count = st.number_input("Vehicle number of this type", min_value=1, value=int(vdata['numar']))
        label = "Save changes"
        renunta = st.form_submit_button("Cancel edit")
        if renunta:
            st.session_state.edit_vehicle_index = -1
            st.session_state.routes_generated = False
            st.rerun()
    else:
        v_name = st.text_input("Vehicle name", "Truck")
        v_cap = st.number_input("Capacity (kg)", min_value=1, value=25000, step=100)
        v_ech = st.checkbox("Crew of 2 drivers?")
        v_count = st.number_input("Vehicle number of this type", min_value=1, value=1)
        label = "Add vehicle"
    salveaza = st.form_submit_button(label)
    if salveaza:
        vehicul_nou = {
            "nume": v_name,
            "capacitate": v_cap,
            "tahograf": True,  # mereu bifat!
            "echipaj": v_ech,
            "numar": v_count
        }
        if st.session_state.edit_vehicle_index != -1:
            st.session_state.vehicle_profiles[st.session_state.edit_vehicle_index] = vehicul_nou
            st.session_state.edit_vehicle_index = -1
            st.session_state.routes_generated = False
            st.success("Vehicle updated.")
        else:
            st.session_state.vehicle_profiles.append(vehicul_nou)
            st.session_state.routes_generated = False
            st.success(f"Vehicle {v_name} added.")
        st.rerun()

# Butoane pentru salvare/flota ORDINE, cu download!
c1, c2 = st.sidebar.columns([1,1])
with c1:
    json_fleet = json.dumps(st.session_state.vehicle_profiles, indent=2)
    st.download_button("💾 Save fleet", data=json_fleet, file_name="fleet_config.json", mime="application/json", use_container_width=True)
with c2:
    fleet_file = st.file_uploader("Upload fleet config", type="json", key="fleet_upld")
    if fleet_file:
        st.session_state.vehicle_profiles = json.load(fleet_file)
        # Setează tahograf pe True la import!
        for v in st.session_state.vehicle_profiles:
            v['tahograf'] = True
        st.sidebar.success("Fleet loaded!")

if st.session_state.vehicle_profiles:
    st.sidebar.markdown("### 🚚 Current fleet:")
    count = 1
    for i, vp in enumerate(st.session_state.vehicle_profiles):
        for k in range(vp['numar']):
            c1, c2, c3 = st.sidebar.columns([8,1,1])
            crew = "Crew 2" if vp.get("echipaj") else ""
            tach = "Tachograph" if vp.get("tahograf") else ""
            details = " | ".join(filter(None, [tach, crew]))
            if details:
                details = " | " + details
            c1.write(f"{count}. {vp['nume']} — {vp['capacitate']} kg{details}")
            if c2.button("✏️", key=f"editv_{i}_{k}"):
                st.session_state.edit_vehicle_index = i
                st.session_state.routes_generated = False
                st.rerun()
            if c3.button("❌", key=f"delv_{i}_{k}"):
                st.session_state.vehicle_profiles.pop(i)
                st.session_state.routes_generated = False
                if st.session_state.edit_vehicle_index == i:
                    st.session_state.edit_vehicle_index = -1
                st.rerun()
            count += 1

# ---- Routing Mode (must be before order form and any use of mode) ----
st.sidebar.markdown("---")
mode = st.sidebar.radio(
    "Select routing mode:",
    options=["Economic", "Fast"],
    horizontal=False
)

# ------------------ Orders Sidebar -----------------
st.sidebar.header("📦 Orders (pickup & delivery)")

with st.sidebar.form("request_form", clear_on_submit=False):
    idx = st.session_state.edit_index
    p_cities = cities_placeholder
    if idx != -1 and len(st.session_state.requests) > idx:
        req = st.session_state.requests[idx]
        pickup_idx = p_cities.index(req['pickup'])
        delivery_idx = p_cities.index(req['delivery'])
        pickup = st.selectbox("Pickup", p_cities, index=pickup_idx, key="pickup_req" if idx == -1 else None)
        delivery = st.selectbox("Delivery", p_cities, index=delivery_idx, key="delivery_req" if idx == -1 else None)
        demand = st.number_input("Quantity (kg)", min_value=1, value=int(req['demand']) if idx != -1 else 1000, step=100)
        tl = st.number_input("Allocated time (h)", min_value=1, value=int(req['time_limit_hrs']) if idx != -1 else 24)
        labelr = "Save change"
        renunta = st.form_submit_button("Cancel edit")
        if renunta:
            st.session_state.edit_index = -1
            st.session_state.routes_generated = False
            st.rerun()
    else:
        pickup_idx = 0
        delivery_idx = 0
        pickup = st.selectbox("Pickup", p_cities, index=pickup_idx, key="pickup_req")
        delivery = st.selectbox("Delivery", p_cities, index=delivery_idx, key="delivery_req")
        demand = st.number_input("Quantity (kg)", min_value=1, value=1000, step=100)
        tl = st.number_input("Allocated time (h)", min_value=1, value=24)
        labelr = "Add order"
    max_cap = max([v['capacitate'] for v in st.session_state.vehicle_profiles], default=0)
    is_oversized = demand > max_cap

    salveazar = st.form_submit_button(labelr)
    if salveazar:
        if pickup == placeholder or delivery == placeholder:
            st.warning("Please select both Pickup and Delivery cities.")
        else:
            # Check if quantity exceeds maximum vehicle capacity
            max_vehicle_capacity = max([v['capacitate'] for v in st.session_state.vehicle_profiles], default=0)
            if demand > max_vehicle_capacity:
                st.error(f"⚠️ **Order quantity exceeds vehicle capacity**\n\n"
                        f"The order quantity ({demand:,} kg) exceeds the maximum vehicle capacity ({max_vehicle_capacity:,} kg).\n\n"
                        f"**Please either:**\n"
                        f"• Reduce the order quantity to {max_vehicle_capacity:,} kg or less\n"
                        f"• Add vehicles with larger capacity\n"
                        f"• Split this into multiple smaller orders")
            else:
                req_nou = {
                    "pickup": pickup,
                    "delivery": delivery,
                    "demand": demand,
                    "time_limit_hrs": tl,
                    "divizibil": False
                }
                if idx != -1:
                    st.session_state.requests[idx] = req_nou
                    st.session_state.edit_index = -1
                    st.session_state.routes_generated = False
                    st.success("Order modified.")
                else:
                    st.session_state.requests.append(req_nou)
                    st.session_state.routes_generated = False
                    st.success("Order added.")
                st.rerun()

c1, c2 = st.sidebar.columns([1,1])
with c1:
    json_orders = json.dumps(st.session_state.requests, indent=2)
    st.download_button("💾 Save orders", data=json_orders, file_name="orders_config.json", mime="application/json", use_container_width=True)
with c2:
    orders_file = st.file_uploader("Upload orders config", type="json", key="orders_upld")
    if orders_file:
        st.session_state.requests = json.load(orders_file)
        st.sidebar.success("Orders loaded!")

if st.session_state.requests:
    st.sidebar.markdown("### 📦 Active Orders:")
    for i, r in enumerate(st.session_state.requests):
        c1, c2, c3 = st.sidebar.columns([8,1,1])
        c1.write(f"{i+1}. {r['pickup']} → {r['delivery']} ({r['demand']}kg, {r['time_limit_hrs']}h)")
        if c2.button("✏️", key=f"editr_{i}"):
            st.session_state.edit_index = i
            st.session_state.routes_generated = False
            st.rerun()
        if c3.button("❌", key=f"delr_{i}"):
            st.session_state.requests.pop(i)
            st.session_state.routes_generated = False
            if st.session_state.edit_index == i:
                st.session_state.edit_index = -1
            st.rerun()

# ---- Routing controls ----
gen_rute = st.sidebar.button("Generate routes", use_container_width=True)
if gen_rute:
    st.session_state.routes_generated = True
    st.rerun()
if st.sidebar.button("Reset", use_container_width=True):
    st.session_state.requests = []
    st.session_state.vehicle_profiles = []
    st.session_state.routes_generated = False
    st.session_state.last_routes = []
    st.session_state.last_cost = 0
    st.session_state.last_polylines = []
    st.session_state.edit_index = -1
    st.session_state.edit_vehicle_index = -1
    st.rerun()

# ---- Main ----
st.title("Optimization of delivery routes for logistics companies")
if not start_city:
    st.info("Please select Depot first.")
    draw_initial_map(city_coords, None)
    st.stop()
if not st.session_state.requests or not st.session_state.vehicle_profiles:
    st.info("Add at least one vehicle and one order to generate routes.")
    draw_initial_map(city_coords, start_city)
    st.stop()

# -- Use actual fleet vehicles, not virtual ones --
profile_expanded = []
profile_src = []
for idx, vp in enumerate(st.session_state.vehicle_profiles):
    for _ in range(vp['numar']):
        profile_expanded.append(vp)
        profile_src.append(idx)

# Order requests by time_limit_hrs (urgency)
chunks = sorted(st.session_state.requests, key=lambda x: x['time_limit_hrs'])

# -- Generate routes --
if st.session_state.routes_generated:
    # Check if all cities are connected
    G = build_graph(city_coords)
    all_cities = set()
    for r in chunks:
        all_cities.add(r['pickup'])
        all_cities.add(r['delivery'])
    all_cities.add(start_city)
    
    connected_cities = set(G.nodes())
    missing_cities = all_cities - connected_cities
    if missing_cities:
        st.error(f"⚠️ Cities not in road network: {missing_cities}")
        st.stop()
    
    # Check connectivity between pickup and delivery cities
    for r in chunks:
        try:
            import networkx as nx
            if not nx.has_path(G, r['pickup'], r['delivery']):
                st.error(f"⚠️ No road connection between {r['pickup']} and {r['delivery']}. Please use cities that are connected in the road network.")
                st.info("💡 Try these connected city pairs: Bucuresti ↔ Giurgiu, Cluj-Napoca ↔ Dej, Brasov ↔ Sibiu")
                st.stop()
        except:
            pass
    
    try:
        # Use basic solver directly - no more smart splitting
        res = solve_vrp(
            start_city=start_city,
            pd_requests=chunks,
            coords=city_coords,
            vehicle_profiles=profile_expanded,
            routing_mode=mode,
            src_map=profile_src
        )
        if not res:
            st.error("⚠️ Unable to generate route: not enough roads in the network for all requests or not enough vehicles. Increase vehicle number or allocated time.")
            draw_initial_map(city_coords, start_city)
            st.stop()
        else:
            rt, tc, pl = res
            st.session_state.last_routes = rt
            st.session_state.last_cost = tc
            st.session_state.last_polylines = pl
            
            # Draw map and table first
            draw_route_map(city_coords, start_city, st.session_state.last_polylines)
            draw_table(st.session_state.last_routes, st.session_state.last_cost,
                       min(r['time_limit_hrs'] for r in chunks))
    except Exception as e:
        st.error(f"⚠️ Error in solver: {str(e)}")
        draw_initial_map(city_coords, start_city)
        st.stop()
else:
    draw_initial_map(city_coords, start_city)
