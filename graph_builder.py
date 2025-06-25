import networkx as nx
import json
import os

def load_road_data(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)
    
def build_graph(city_coords: dict, road_file="roads.json") -> nx.Graph:
    G = nx.Graph()
    roads = load_road_data(road_file)
    for road in roads:
        frm = road.get("from")
        to = road.get("to")
        dist = road.get("distance_km")
        dur = road.get("duration_hours")
        if frm not in city_coords or to not in city_coords:
            print(f"[EROARE COORDONATE] {frm} sau {to} lipsesc din city_coords")
            continue
        G.add_edge(frm, to, weight=dist, distance=dist, duration=dur)
    return G

def get_distance(G: nx.Graph, source: str, target: str) -> float:
    return nx.dijkstra_path_length(G, source, target, weight="distance")

def get_duration(G: nx.Graph, source: str, target: str) -> float:
    return nx.dijkstra_path_length(G, source, target, weight="duration")

def get_path(G: nx.Graph, source: str, target: str) -> list:
    return nx.dijkstra_path(G, source, target, weight="distance")