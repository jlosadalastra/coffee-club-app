import streamlit as st
import pandas as pd
from datetime import date
from sqlalchemy import func
import pydeck as pdk
import requests

from db import (
    init_db, SessionLocal, Group, User, Shop, Review, PostcodeConfig,
    get_or_create_group, get_or_create_user
)
from osm_loader import fetch_coffee_shops_by_postcodes

st.set_page_config(page_title="Coffee Club Map", layout="wide")
init_db()


# ---------- Helpers ----------
def full_name(u: User):
    return f"{u.first_name} {u.last_name}"


def get_current_user(session):
    uid = st.session_state.get("user_id")
    if not uid:
        return None
    return session.query(User).filter_by(id=uid).first()


def get_current_group(session):
    gid = st.session_state.get("group_id")
    if not gid:
        return None
    return session.query(Group).filter_by(id=gid).first()


def get_group_postcodes(session, group_id):
    rows = session.query(PostcodeConfig).filter_by(group_id=group_id).all()
    return [r.postcode_prefix for r in rows]


def set_group_postcodes(session, group_id, postcodes):
    session.query(PostcodeConfig).filter_by(group_id=group_id).delete()
    for p in postcodes:
        p = p.strip().upper()
        if p:
            session.add(PostcodeConfig(group_id=group_id, postcode_prefix=p))
    session.commit()


def load_shops_df(session, active_only=True):
    q = session.query(Shop)
    if active_only:
        q = q.filter_by(active=1)
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=["id", "name", "address", "postcode", "lat", "lon", "active"])
    return pd.DataFrame([{
        "id": r.id,
        "name": r.name,
        "address": r.address,
        "postcode": r.postcode,
        "lat": r.lat,
        "lon": r.lon,
        "active": r.active
    } for r in rows])


def load_reviews_df(session):
    q = (
        session.query(
            Review.id.label("review_id"),
            Review.rating,
            Review.drink_order,
            Review.review_date,
            User.first_name,
            User.last_name,
            Shop.name.label("shop_name"),
            Shop.id.label("shop_id"),
            Review.user_id.label("user_id")
        )
        .join(User, User.id == Review.user_id)
        .join(Shop, Shop.id == Review.shop_id)
    )
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=[
            "review_id", "rating", "drink_order", "review_date", "first_name",
            "last_name", "shop_name", "shop_id", "reviewer", "user_id"
        ])
    return pd.DataFrame([{
        "review_id": r.review_id,
        "rating": r.rating,
        "drink_order": r.drink_order,
        "review_date": r.review_date,
        "first_name": r.first_name,
        "last_name": r.last_name,
        "reviewer": f"{r.first_name} {r.last_name}",
        "shop_name": r.shop_name,
        "shop_id": r.shop_id,
        "user_id": r.user_id
    } for r in rows])


def bayesian_score(df, min_reviews=3, m=5):
    if df.empty:
        return pd.DataFrame(columns=["shop_name", "review_count", "avg_rating", "bayesian"])
    C = df["rating"].mean()
    agg = df.groupby("shop_name", as_index=False).agg(
        review_count=("rating", "count"),
        avg_rating=("rating", "mean")
    )
    agg = agg[agg["review_count"] >= min_reviews].copy()
    if agg.empty:
        agg["bayesian"] = []
        return agg

    agg["bayesian"] = (
        (agg["review_count"] / (agg["review_count"] + m)) * agg["avg_rating"]
        + (m / (agg["review_count"] + m)) * C
    )
    return agg.sort_values(["bayesian", "review_count"], ascending=[False, False])


def top_drinkers(df):
    if df.empty:
        return pd.DataFrame(columns=["reviewer", "reviews_submitted"])
    agg = df.groupby("reviewer", as_index=False).agg(reviews_submitted=("review_id", "count"))
    return agg.sort_values("reviews_submitted", ascending=False)


def refresh_shops_from_osm(session, postcode_prefixes):
    shops = fetch_coffee_shops_by_postcodes(postcode_prefixes)
    added = 0
    for s in shops:
        exists = (
            session.query(Shop)
            .filter(func.lower(Shop.name) == s["name"].lower())
            .filter(func.abs(Shop.lat - s["lat"]) < 0.0001)
            .filter(func.abs(Shop.lon - s["lon"]) < 0.0001)
            .first()
        )
        if not exists:
            session.add(Shop(
                name=s["name"],
                address=s.get("address"),
                postcode=s.get("postcode"),
                lat=s["lat"],
                lon=s["lon"],
                source="osm",
                active=1
            ))
            added += 1
        else:
            exists.active = 1
            exists.address = s.get("address")
            exists.postcode = s.get("postcode")
    session.commit()
    return added, len(shops)


def replace_active_shops_with_result(session, shops):
    # make active list exactly match latest radius result
    session.query(Shop).filter(Shop.active == 1).update({"active": 0}, synchronize_session=False)

    activated_or_added = 0
    for s in shops:
        exists = (
            session.query(Shop)
            .filter(func.lower(Shop.name) == s["name"].lower())
            .filter(func.abs(Shop.lat - s["lat"]) < 0.0001)
            .filter(func.abs(Shop.lon - s["lon"]) < 0.0001)
            .first()
        )
        if exists:
            exists.active = 1
            exists.address = s.get("address")
            exists.postcode = s.get("postcode")
            exists.source = s.get("source", exists.source)
            activated_or_added += 1
        else:
            session.add(Shop(
                name=s["name"],
                address=s.get("address"),
                postcode=s.get("postcode"),
                lat=s["lat"],
                lon=s["lon"],
                source=s.get("source", "osm_radius"),
                active=1
            ))
            activated_or_added += 1

    session.commit()
    return activated_or_added


# ---- optional radius mode helpers ----
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def geocode_text(query_text: str):
    params = {"q": query_text, "format": "json", "limit": 1}
    headers = {"User-Agent": "coffee-club-app/1.4"}
    r = requests.get(NOMINATIM_URL, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"])


def fetch_cafes_by_radius(lat, lon, radius_km=2.0):
    radius_m = int(radius_km * 1000)
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="cafe"](around:{radius_m},{lat},{lon});
      way["amenity"="cafe"](around:{radius_m},{lat},{lon});
      relation["amenity"="cafe"](around:{radius_m},{lat},{lon});
    );
    out center tags;
    """
    r = requests.get(OVERPASS_URL, params={"data": query}, timeout=120)
    r.raise_for_status()
    data = r.json()

    shops = []
    for el in data.get("elements", []):
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

        postcode = tags.get("addr:postcode", None)
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
            "postcode": postcode,
            "lat": float(slat),
            "lon": float(slon),
            "source": "osm_radius"
        })

    seen = set()
    deduped = []
    for s in shops:
        key = (s["name"].lower(), round(s["lat"], 5), round(s["lon"], 5))
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return deduped


def render_star_text(x):
    return f"⭐ {round(float(x), 1):.1f}"


def extract_street(address: str):
    if not address or not str(address).strip():
        return "Unknown street"
    cleaned = " ".join(str(address).split())
    return cleaned


def shop_label(row):
    street = extract_street(row.get("address"))
    return f"{row.get('name', 'Unknown cafe')}, {street}"


def add_last_visit_for_user(session, shops_df, user_id):
    if shops_df.empty:
        shops_df["Last visit"] = pd.NaT
        return shops_df

    user_last_visits = (
        session.query(
            Review.shop_id.label("shop_id"),
            func.max(Review.review_date).label("last_visit")
        )
        .filter(Review.user_id == user_id)
        .group_by(Review.shop_id)
        .all()
    )
    last_visit_map = {row.shop_id: row.last_visit for row in user_last_visits}
    out = shops_df.copy()
    out["Last visit"] = out["id"].map(last_visit_map)
    return out


# ---------- UI ----------
st.title("☕ Coffee Club Map & Reviews")
session = SessionLocal()

# -------- Auth / Group onboarding --------
if "user_id" not in st.session_state:
    st.subheader("Join or Create a Group")
    tab1, tab2 = st.tabs(["Join Group", "Create Group (Admin)"])

    with tab1:
        join_code = st.text_input("Group code", placeholder="e.g. OXF-COFFEE-01")
        first_name = st.text_input("First name")
        last_name = st.text_input("Last name")
        if st.button("Join group"):
            g = session.query(Group).filter_by(join_code=join_code.strip()).first()
            if not g:
                st.error("Group code not found.")
            elif not first_name.strip() or not last_name.strip():
                st.error("Please enter first and last name.")
            else:
                u, _ = get_or_create_user(session, g.id, first_name, last_name)
                st.session_state["user_id"] = u.id
                st.session_state["group_id"] = g.id
                st.success(f"Welcome {full_name(u)}")
                st.rerun()

    with tab2:
        g_name = st.text_input("Group name", value="Oxford Coffee Club")
        g_code = st.text_input("Admin-defined join code", placeholder="Set a code to share")
        admin_first = st.text_input("Admin first name")
        admin_last = st.text_input("Admin last name")
        if st.button("Create group"):
            if not g_name.strip() or not g_code.strip() or not admin_first.strip() or not admin_last.strip():
                st.error("Fill all fields.")
            else:
                g, created = get_or_create_group(session, g_name.strip(), g_code.strip())
                if not created:
                    st.error("Join code already exists. Choose another.")
                else:
                    u, _ = get_or_create_user(session, g.id, admin_first, admin_last, role="admin")
                    for p in ["OX1", "OX2", "OX3", "OX4"]:
                        session.add(PostcodeConfig(group_id=g.id, postcode_prefix=p))
                    session.commit()
                    st.session_state["user_id"] = u.id
                    st.session_state["group_id"] = g.id
                    st.success(f"Group created. Share code: {g.join_code}")
                    st.rerun()
    st.stop()

current_user = get_current_user(session)
current_group = get_current_group(session)

if not current_user or not current_group:
    st.error("Session expired. Refresh and join again.")
    st.stop()

st.caption(f"Logged in as: **{full_name(current_user)}** | Group: **{current_group.name}** (`{current_group.join_code}`)")
if st.button("Logout"):
    st.session_state.clear()
    st.rerun()

# -------- Sidebar controls --------
with st.sidebar:
    st.header("Controls")

    fetch_mode = st.radio("Shop fetch mode", ["Postcode prefixes", "Base location + radius (km)"], index=0)

    if fetch_mode == "Postcode prefixes":
        postcodes = get_group_postcodes(session, current_group.id)
        postcodes_txt = st.text_input("Postcode prefixes (comma-separated)", value=", ".join(postcodes))
        if st.button("Save postcodes"):
            pcs = [p.strip().upper() for p in postcodes_txt.split(",") if p.strip()]
            set_group_postcodes(session, current_group.id, pcs)
            st.success("Postcodes updated.")

        if st.button("Refresh shops from OSM (postcodes)"):
            pcs = get_group_postcodes(session, current_group.id)
            with st.spinner("Fetching coffee shops from OpenStreetMap..."):
                added, found = refresh_shops_from_osm(session, pcs)
            st.success(f"Found {found} shops, added {added} new.")
    else:
        postcode_text = st.text_input("Postcode", value="OX2 6AT")
        radius_km = st.number_input("Radius (km)", min_value=0.3, max_value=20.0, value=2.0, step=0.1)
        if st.button("Refresh shops from OSM (radius)"):
            try:
                with st.spinner("Geocoding postcode..."):
                    geo = geocode_text(postcode_text)
                if not geo:
                    st.error("Could not geocode that postcode.")
                else:
                    lat, lon = geo
                    with st.spinner("Fetching cafes by radius..."):
                        shops = fetch_cafes_by_radius(lat, lon, radius_km=radius_km)
                        count_active = replace_active_shops_with_result(session, shops)
                    st.success(f"Found {len(shops)} shops. Active list replaced with {count_active} cafes.")
            except Exception as e:
                st.error(f"Error while fetching shops: {e}")

    st.markdown("---")
    st.subheader("Leaderboard settings")
    min_reviews = st.number_input("Minimum reviews per shop", min_value=1, max_value=20, value=3, step=1)
    bayes_m = st.number_input("Bayesian m (confidence)", min_value=1, max_value=50, value=5, step=1)

# -------- Main tabs --------
tabs = ["Map", "Submit Review", "Leaderboards", "Data"]
if current_user.role == "admin":
    tabs.append("Admin")

tab_objs = st.tabs(tabs)
tab_map, tab_review, tab_leaderboard, tab_data = tab_objs[:4]
tab_admin = tab_objs[4] if current_user.role == "admin" else None

# Map tab
with tab_map:
    st.subheader("Coffee shops map")
    shops_df = load_shops_df(session, active_only=True)

    reviewed_only = st.checkbox("Show reviewed shops only", value=False)
    reviews_df = load_reviews_df(session)

    if reviewed_only and not reviews_df.empty:
        reviewed_shop_ids = reviews_df["shop_id"].unique().tolist()
        shops_df = shops_df[shops_df["id"].isin(reviewed_shop_ids)]

    if shops_df.empty:
        st.info("No shops loaded yet. Use sidebar fetch.")
    else:
        display_df = shops_df.copy()
        display_df["Cafe"] = display_df.apply(shop_label, axis=1)
        display_df = add_last_visit_for_user(session, display_df, current_user.id)

        # Focus/Highlight control
        cafe_options = ["(None)"] + display_df["Cafe"].tolist()
        selected_cafe = st.selectbox("Focus cafe on map", cafe_options)

        # Map styling columns
        plot_df = display_df.copy()

        # make pydeck-safe primitives only
        plot_df["Last visit"] = plot_df["Last visit"].astype(str).replace("NaT", "")
        plot_df["radius"] = 15
        plot_df["r"] = 30
        plot_df["g"] = 136
        plot_df["b"] = 229
        plot_df["a"] = 170
        #plot_df["label"] = plot_df["name"].astype(str)

        # Default center
        center_lat = plot_df["lat"].mean()
        center_lon = plot_df["lon"].mean()
        zoom_level = 13

        if selected_cafe != "(None)":
            sel_row = plot_df[plot_df["Cafe"] == selected_cafe].iloc[0]
            center_lat = float(sel_row["lat"])
            center_lon = float(sel_row["lon"])
            zoom_level = 15

            idx = plot_df["Cafe"] == selected_cafe
            plot_df.loc[idx, "radius"] = 160
            plot_df.loc[idx, ["r", "g", "b", "a"]] = [220, 20, 60, 230]

        scatter = pdk.Layer(
            "ScatterplotLayer",
            data=plot_df,
            get_position='[lon, lat]',
            get_radius='radius',
            get_fill_color='[r, g, b, a]',
            pickable=True
        )

        text = pdk.Layer(
            "TextLayer",
            data=plot_df,
            get_position='[lon, lat]',
            get_text='label',
            get_size=12,
            get_color='[40, 40, 40, 220]',
            get_pixel_offset='[8, -2]'
        )

        view_state = pdk.ViewState(
            latitude=float(center_lat),
            longitude=float(center_lon),
            zoom=zoom_level,
            pitch=0
        )

        tooltip = {
            "text": "{name}\n{address}\n{postcode}\nLast visit: {Last visit}"
        }

        st.pydeck_chart(
            pdk.Deck(
                map_style="mapbox://styles/mapbox/light-v10",
                initial_view_state=view_state,
                layers=[scatter, text],
                tooltip=tooltip
            ),
            use_container_width=True
        )

        st.dataframe(
            display_df[["Cafe", "postcode", "Last visit"]].rename(columns={"postcode": "Postcode"}),
            use_container_width=True,
            hide_index=True
        )

# Review tab
with tab_review:
    st.subheader("Submit a review")
    shops_df = load_shops_df(session, active_only=True)

    if shops_df.empty:
        st.info("No shops available. Refresh shops first.")
    else:
        shop_options = {shop_label(r): r["id"] for _, r in shops_df.iterrows()}
        selected_label = st.selectbox("Select shop", list(shop_options.keys()))
        shop_id = shop_options[selected_label]

        st.write("Your rating")
        if "selected_rating" not in st.session_state:
            st.session_state["selected_rating"] = 4

        c1, c2, c3, c4, c5 = st.columns(5)
        if c1.button("⭐", key="rate1"):
            st.session_state["selected_rating"] = 1
        if c2.button("⭐⭐", key="rate2"):
            st.session_state["selected_rating"] = 2
        if c3.button("⭐⭐⭐", key="rate3"):
            st.session_state["selected_rating"] = 3
        if c4.button("⭐⭐⭐⭐", key="rate4"):
            st.session_state["selected_rating"] = 4
        if c5.button("⭐⭐⭐⭐⭐", key="rate5"):
            st.session_state["selected_rating"] = 5

        rating = st.session_state["selected_rating"]
        st.write(f"Selected: **{render_star_text(rating)}**")

        drink_order = st.text_input("Drink order", placeholder="e.g. Flat White, Oat Latte")
        today = date.today()
        st.write(f"Review date: **{today}**")

        if st.button("Submit review"):
            existing = (
                session.query(Review)
                .filter_by(user_id=current_user.id, shop_id=shop_id, review_date=today)
                .first()
            )
            if existing:
                st.error("You already reviewed this shop today.")
            else:
                rv = Review(
                    user_id=current_user.id,
                    shop_id=shop_id,
                    rating=rating,
                    drink_order=drink_order.strip() if drink_order else None,
                    review_date=today
                )
                session.add(rv)
                session.commit()
                st.success("Review submitted!")

# Leaderboard tab
with tab_leaderboard:
    st.subheader("Leaderboards")
    reviews_df = load_reviews_df(session)

    if reviews_df.empty:
        st.info("No reviews yet.")
    else:
        drink_filter = st.selectbox(
            "Filter by drink type",
            ["All"] + sorted([d for d in reviews_df["drink_order"].dropna().unique().tolist() if d.strip()])
        )

        df_filtered = reviews_df.copy()
        if drink_filter != "All":
            df_filtered = df_filtered[df_filtered["drink_order"] == drink_filter]

        leaderboard_type = st.selectbox(
            "Leaderboard type",
            ["Best coffee shops (Bayesian)", "Top coffee drinkers"]
        )

        if leaderboard_type == "Best coffee shops (Bayesian)":
            lb = bayesian_score(df_filtered[["shop_name", "rating"]], min_reviews=min_reviews, m=bayes_m)
            if lb.empty:
                st.warning("No shops meet minimum review threshold.")
            else:
                lb["Average rating"] = lb["avg_rating"].round(1).apply(render_star_text)
                lb["Complex average"] = lb["bayesian"].round(1).apply(render_star_text)

                out = lb.rename(columns={"shop_name": "Coffee shop", "review_count": "Reviews"})[
                    ["Coffee shop", "Reviews", "Average rating", "Complex average"]
                ]
                st.dataframe(out, use_container_width=True, hide_index=True)

                st.info(
                    "Complex average (?) = Bayesian average.\n\n"
                    "It combines a cafe's own average with the global average so places with very few "
                    "reviews don't jump unfairly to the top.\n\n"
                    "As a cafe gets more reviews, its own rating has more influence."
                )
        else:
            td = top_drinkers(df_filtered)
            st.dataframe(td, use_container_width=True, hide_index=True)

# Data tab
with tab_data:
    st.subheader("Export data")
    reviews_df = load_reviews_df(session)
    shops_df = load_shops_df(session, active_only=True)

    c1, c2 = st.columns(2)
    with c1:
        st.write("Reviews")
        st.dataframe(reviews_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download reviews CSV",
            data=reviews_df.to_csv(index=False).encode("utf-8"),
            file_name="reviews.csv",
            mime="text/csv"
        )

    with c2:
        st.write("Shops")
        display_df = shops_df.copy()
        display_df["Cafe"] = display_df.apply(shop_label, axis=1)
        display_df = add_last_visit_for_user(session, display_df, current_user.id)

        st.dataframe(
            display_df[["Cafe", "postcode", "lat", "lon", "Last visit"]].rename(columns={"postcode": "Postcode"}),
            use_container_width=True,
            hide_index=True
        )
        st.download_button(
            "Download shops CSV",
            data=shops_df.to_csv(index=False).encode("utf-8"),
            file_name="shops.csv",
            mime="text/csv"
        )

# Admin tab
if tab_admin is not None:
    with tab_admin:
        st.subheader("Admin: Manage cafes")

        shops_all_df = load_shops_df(session, active_only=False)
        if shops_all_df.empty:
            st.info("No cafes found in database.")
        else:
            active_df = shops_all_df[shops_all_df["active"] == 1].copy()
            inactive_df = shops_all_df[shops_all_df["active"] == 0].copy()

            st.markdown("### Deactivate one cafe")
            if active_df.empty:
                st.info("No active cafes to deactivate.")
            else:
                options = {f"{shop_label(r)} [ID:{int(r['id'])}]": int(r["id"]) for _, r in active_df.iterrows()}
                target_label = st.selectbox("Select cafe", list(options.keys()), key="admin_single_shop")
                if st.button("Deactivate selected cafe"):
                    sid = options[target_label]
                    shop = session.query(Shop).filter_by(id=sid).first()
                    if shop:
                        shop.active = 0
                        session.commit()
                        st.success(f"Deactivated: {shop.name}")
                        st.rerun()

            st.markdown("---")
            st.markdown("### Deactivate by chain keyword")
            chain_kw = st.text_input("Chain keyword", placeholder="e.g. costa, nero, starbucks")

            if st.button("Preview chain matches"):
                if chain_kw.strip():
                    q = chain_kw.strip().lower()
                    matches = active_df[active_df["name"].str.lower().str.contains(q, na=False)].copy()
                    matches["Cafe"] = matches.apply(shop_label, axis=1)
                    st.write(f"Matches: {len(matches)}")
                    st.dataframe(matches[["id", "Cafe", "postcode"]], use_container_width=True, hide_index=True)
                else:
                    st.warning("Enter a keyword first.")

            if st.button("Deactivate all matches for keyword"):
                if not chain_kw.strip():
                    st.warning("Enter a keyword first.")
                else:
                    q = chain_kw.strip().lower()
                    to_deactivate = session.query(Shop).filter(
                        Shop.active == 1,
                        func.lower(Shop.name).contains(q)
                    ).all()
                    for s in to_deactivate:
                        s.active = 0
                    session.commit()
                    st.success(f"Deactivated {len(to_deactivate)} cafes matching '{chain_kw.strip()}'.")
                    st.rerun()

            st.markdown("---")
            st.markdown("### Restore deactivated cafes")
            if inactive_df.empty:
                st.info("No deactivated cafes to restore.")
            else:
                restore_options = {f"{shop_label(r)} [ID:{int(r['id'])}]": int(r["id"]) for _, r in inactive_df.iterrows()}
                restore_label = st.selectbox("Select cafe to restore", list(restore_options.keys()), key="restore_single_shop")
                if st.button("Restore selected cafe"):
                    sid = restore_options[restore_label]
                    shop = session.query(Shop).filter_by(id=sid).first()
                    if shop:
                        shop.active = 1
                        session.commit()
                        st.success(f"Restored: {shop.name}")
                        st.rerun()

                st.markdown("#### Restore by chain keyword")
                restore_kw = st.text_input("Restore keyword", placeholder="e.g. costa, nero", key="restore_kw")

                if st.button("Preview restore matches"):
                    if restore_kw.strip():
                        q = restore_kw.strip().lower()
                        matches = inactive_df[inactive_df["name"].str.lower().str.contains(q, na=False)].copy()
                        matches["Cafe"] = matches.apply(shop_label, axis=1)
                        st.write(f"Matches: {len(matches)}")
                        st.dataframe(matches[["id", "Cafe", "postcode"]], use_container_width=True, hide_index=True)
                    else:
                        st.warning("Enter a keyword first.")

                if st.button("Restore all matches for keyword"):
                    if not restore_kw.strip():
                        st.warning("Enter a keyword first.")
                    else:
                        q = restore_kw.strip().lower()
                        to_restore = session.query(Shop).filter(
                            Shop.active == 0,
                            func.lower(Shop.name).contains(q)
                        ).all()
                        for s in to_restore:
                            s.active = 1
                        session.commit()
                        st.success(f"Restored {len(to_restore)} cafes matching '{restore_kw.strip()}'.")
                        st.rerun()