import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
from sqlalchemy import func
import pydeck as pdk

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


def load_shops_df(session):
    rows = session.query(Shop).filter_by(active=1).all()
    if not rows:
        return pd.DataFrame(columns=["id", "name", "address", "postcode", "lat", "lon"])
    return pd.DataFrame([{
        "id": r.id, "name": r.name, "address": r.address, "postcode": r.postcode,
        "lat": r.lat, "lon": r.lon
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
            Shop.id.label("shop_id")
        )
        .join(User, User.id == Review.user_id)
        .join(Shop, Shop.id == Review.shop_id)
    )
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=["review_id","rating","drink_order","review_date","first_name","last_name","shop_name","shop_id"])
    return pd.DataFrame([{
        "review_id": r.review_id,
        "rating": r.rating,
        "drink_order": r.drink_order,
        "review_date": r.review_date,
        "first_name": r.first_name,
        "last_name": r.last_name,
        "reviewer": f"{r.first_name} {r.last_name}",
        "shop_name": r.shop_name,
        "shop_id": r.shop_id
    } for r in rows])


def bayesian_score(df, min_reviews=3, m=5):
    # df must have columns: shop_name, rating
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
    agg["bayesian"] = ((agg["review_count"]/(agg["review_count"]+m))*agg["avg_rating"] +
                       (m/(agg["review_count"]+m))*C)
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
    session.commit()
    return added, len(shops)


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
                    # default postcodes
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

    # Editable postcodes
    postcodes = get_group_postcodes(session, current_group.id)
    postcodes_txt = st.text_input("Postcode prefixes (comma-separated)", value=", ".join(postcodes))
    if st.button("Save postcodes"):
        pcs = [p.strip().upper() for p in postcodes_txt.split(",") if p.strip()]
        set_group_postcodes(session, current_group.id, pcs)
        st.success("Postcodes updated.")

    if st.button("Refresh shops from OSM"):
        pcs = get_group_postcodes(session, current_group.id)
        with st.spinner("Fetching coffee shops from OpenStreetMap..."):
            added, found = refresh_shops_from_osm(session, pcs)
        st.success(f"Found {found} shops, added {added} new.")

    st.markdown("---")
    st.subheader("Leaderboard settings")
    min_reviews = st.number_input("Minimum reviews per shop", min_value=1, max_value=20, value=3, step=1)
    bayes_m = st.number_input("Bayesian m (confidence)", min_value=1, max_value=50, value=5, step=1)

# -------- Main tabs --------
tab_map, tab_review, tab_leaderboard, tab_data = st.tabs(["Map", "Submit Review", "Leaderboards", "Data"])

# Map tab
with tab_map:
    st.subheader("Coffee shops map")
    shops_df = load_shops_df(session)

    reviewed_only = st.checkbox("Show reviewed shops only", value=False)

    reviews_df = load_reviews_df(session)
    if reviewed_only and not reviews_df.empty:
        reviewed_shop_ids = reviews_df["shop_id"].unique().tolist()
        shops_df = shops_df[shops_df["id"].isin(reviewed_shop_ids)]

    if shops_df.empty:
        st.info("No shops loaded yet. Use sidebar: 'Refresh shops from OSM'.")
    else:
        mid_lat = shops_df["lat"].mean()
        mid_lon = shops_df["lon"].mean()

        layer = pdk.Layer(
            "ScatterplotLayer",
            data=shops_df,
            get_position='[lon, lat]',
            get_radius=60,
            get_fill_color='[30, 136, 229, 180]',
            pickable=True,
        )

        view_state = pdk.ViewState(latitude=mid_lat, longitude=mid_lon, zoom=12, pitch=0)
        tooltip = {"text": "{name}\n{address}\n{postcode}"}

        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state, tooltip=tooltip), use_container_width=True)
        st.dataframe(shops_df[["name", "address", "postcode"]], use_container_width=True, hide_index=True)

# Review tab
with tab_review:
    st.subheader("Submit a review")
    shops_df = load_shops_df(session)

    if shops_df.empty:
        st.info("No shops available. Refresh shops from OSM first.")
    else:
        shop_options = {f"{r['name']} ({r['postcode'] or 'N/A'})": r["id"] for _, r in shops_df.iterrows()}
        selected_label = st.selectbox("Select shop", list(shop_options.keys()))
        shop_id = shop_options[selected_label]

        rating = st.slider("Rating (1-5)", min_value=1, max_value=5, value=4)
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
                lb["avg_rating"] = lb["avg_rating"].round(2)
                lb["bayesian"] = lb["bayesian"].round(3)
                st.dataframe(lb, use_container_width=True, hide_index=True)
        else:
            td = top_drinkers(df_filtered)
            st.dataframe(td, use_container_width=True, hide_index=True)

# Data tab
with tab_data:
    st.subheader("Export data")
    reviews_df = load_reviews_df(session)
    shops_df = load_shops_df(session)

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
        st.dataframe(shops_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download shops CSV",
            data=shops_df.to_csv(index=False).encode("utf-8"),
            file_name="shops.csv",
            mime="text/csv"
        )