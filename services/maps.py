import requests
import streamlit as st
from config import NOMINATIM_URL, OVERPASS_ENDPOINTS, USER_AGENT

def overpass_query(query: str, timeout_sec=20):
    last_err = None
    headers = {"User-Agent": USER_AGENT}
    for ep in OVERPASS_ENDPOINTS:
        try:
            r = requests.get(ep, params={"data": query}, headers=headers, timeout=timeout_sec)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
    raise last_err

@st.cache_data(ttl=86400)
def geocode_text_cached(query_text: str):
    params = {"q": query_text, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])

@st.cache_data(ttl=43200)
def fetch_cafes_by_radius_cached(lat, lon, radius_km=2, fast_mode=True, max_results=120):
    radius_m = int(radius_km * 1000)

    if fast_mode:
        query = f"""
        [out:json][timeout:20];
        node["amenity"="cafe"](around:{radius_m},{lat},{lon});
        out tags;
        """
    else:
        query = f"""
        [out:json][timeout:20];
        (
          node["amenity"="cafe"](around:{radius_m},{lat},{lon});
          way["amenity"="cafe"](around:{radius_m},{lat},{lon});
          relation["amenity"="cafe"](around:{radius_m},{lat},{lon});
        );
        out center tags;
        """

    data = overpass_query(query, timeout_sec=20)
    elements = data.get("elements", [])

    shops = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        if el.get("type") == "node":
            slat, slon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center", {})
            slat, slon = c.get("lat"), c.get("lon")

        if slat is None or slon is None:
            continue

        address = " ".join([p for p in [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:postcode", "")
        ] if p]).strip()

        shops.append({
            "name": name,
            "address": address if address else None,
            "postcode": tags.get("addr:postcode"),
            "lat": float(slat),
            "lon": float(slon),
            "source": "radius_fast" if fast_mode else "radius_full"
        })

    shops = shops[:max_results]

    seen, out = set(), []
    for s in shops:
        key = (s["name"].lower(), round(s["lat"], 5), round(s["lon"], 5))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out