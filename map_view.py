import streamlit as st
import folium
from streamlit_folium import st_folium
from folium.plugins import AntPath
from folium.plugins import Fullscreen

def draw_initial_map(city_coords, start_city):
    m = folium.Map(location=[45.9432, 24.9668], zoom_start=7)
    Fullscreen(position='topright').add_to(m)
    for city, data in city_coords.items():
        if data.get("visible", False):
            icon_color = "green" if city == start_city else "blue"
            folium.Marker(
                location=data["coords"],
                tooltip=city,
                icon=folium.Icon(color=icon_color)
            ).add_to(m)
    st_folium(m, width=1000, height=600)

def draw_route_map(city_coords, start_city, polylines):
    m = folium.Map(location=[45.9432, 24.9668], zoom_start=7)
    Fullscreen(position='topright').add_to(m)
    for city, data in city_coords.items():
        if data.get("visible", False):
            icon_color = "green" if city == start_city else "blue"
            folium.Marker(
                location=data["coords"],
                tooltip=city,
                icon=folium.Icon(color=icon_color)
            ).add_to(m)

    colors = ["blue", "green", "red", "purple", "orange", "darkred",
              "lightred", "beige", "darkblue", "darkgreen", "cadetblue"]

    for i, polyline in enumerate(polylines):
        AntPath(
            locations=polyline,
            color=colors[i % len(colors)],
            weight=5,
            delay=800
        ).add_to(m)

    st_folium(m, width=1000, height=600)