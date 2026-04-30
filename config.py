APP_TITLE = "☕ Coffee Club"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
]

USER_AGENT = "coffee-club-v2.4"

GEOCODE_TTL_SEC = 86400   # 24h
CAFE_FETCH_TTL_SEC = 43200  # 12h

DEFAULT_POSTCODE = "OX2 6AT"
DEFAULT_RADIUS_KM = 2
MAX_RADIUS_KM = 20
MIN_RADIUS_KM = 1

MAX_RESULTS = 120

DRINK_BASE = [
    "Latte","Cappuccino","Flat White","Americano","Espresso",
    "Mocha","Cortado/Macchiato","Filter Coffee","Chai Latte"
]