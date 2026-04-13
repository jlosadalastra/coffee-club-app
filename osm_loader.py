import requests


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Fetch coffee shops by postcode district prefixes (e.g., OX1, OX2)
def fetch_coffee_shops_by_postcodes(postcode_prefixes):
    prefixes = [p.strip().upper() for p in postcode_prefixes if p.strip()]
    if not prefixes:
        return []

    # Search in/around Oxford area relation for practical speed
    # then filter by addr:postcode prefix
    query = f"""
    [out:json][timeout:60];
    area["name"="Oxford"]["boundary"="administrative"]->.searchArea;
    (
      node["amenity"="cafe"](area.searchArea);
      way["amenity"="cafe"](area.searchArea);
      relation["amenity"="cafe"](area.searchArea);
    );
    out center tags;
    """

    resp = requests.get(OVERPASS_URL, params={"data": query}, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    shops = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue

        postcode = tags.get("addr:postcode", "") or ""
        postcode_up = postcode.upper()

        # Keep if postcode starts with any configured prefix (OX1 etc)
        if not any(postcode_up.startswith(pref) for pref in prefixes):
            continue

        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")

        if lat is None or lon is None:
            continue

        address_parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:postcode", "")
        ]
        address = " ".join([a for a in address_parts if a]).strip()

        shops.append({
            "name": name,
            "address": address if address else None,
            "postcode": postcode if postcode else None,
            "lat": float(lat),
            "lon": float(lon),
            "source": "osm"
        })

    # de-dup by name + rounded coords
    seen = set()
    deduped = []
    for s in shops:
        key = (s["name"].lower(), round(s["lat"], 5), round(s["lon"], 5))
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    return deduped