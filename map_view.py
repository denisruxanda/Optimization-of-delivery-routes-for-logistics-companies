import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import AntPath, Fullscreen

__all__ = ["draw_initial_map", "draw_route_map"]

# default map center (RO)
MAP_CENTER = [45.9432, 24.9668]
DEFAULT_ZOOM = 7
MARKER_DEPOT_COLOR = "green"
MARKER_NODE_COLOR = "blue"
PATH_WEIGHT = 5
ANTPATH_DELAY_MS = 800

def _add_markers(m, city_coords, start_city):
    for city, data in city_coords.items():
        if not data.get("visible", False):
            continue
        icon_color = MARKER_DEPOT_COLOR if city == start_city else MARKER_NODE_COLOR
        folium.Marker(
            location=data["coords"],
            tooltip=city,
            icon=folium.Icon(color=icon_color)
        ).add_to(m)

def draw_initial_map(city_coords, start_city):
    # base map with all visible cities and depot marker
    m = folium.Map(location=MAP_CENTER, zoom_start=DEFAULT_ZOOM)
    Fullscreen(position='topright').add_to(m)
    _add_markers(m, city_coords, start_city)
    st_folium(m, width=1024, height=640)

def draw_route_map(city_coords, start_city, polylines):
    # map with markers + animated polylines (one per vehicle)
    m = folium.Map(location=MAP_CENTER, zoom_start=DEFAULT_ZOOM)
    Fullscreen(position='topright').add_to(m)
    _add_markers(m, city_coords, start_city)

    colors = [
        "blue", "green", "red", "purple", "orange", "darkred",
        "lightred", "beige", "darkblue", "darkgreen", "cadetblue"
    ]

    for i, polyline in enumerate(polylines or []):
        if not polyline:
            continue
        AntPath(
            locations=polyline,
            color=colors[i % len(colors)],
            weight=PATH_WEIGHT,
            delay=ANTPATH_DELAY_MS
        ).add_to(m)

    st_folium(m, width=1024, height=640)
