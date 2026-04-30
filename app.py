import streamlit as st
import folium
from datetime import date
from streamlit_folium import st_folium
from sqlalchemy import func

from db import init_db, SessionLocal, Group, Shop, Review, get_or_create_group, get_or_create_user
from config import APP_TITLE, DEFAULT_POSTCODE, DEFAULT_RADIUS_KM, MIN_RADIUS_KM, MAX_RADIUS_KM, MAX_RESULTS, DRINK_BASE
from services.maps import geocode_text_cached, fetch_cafes_by_radius_cached
from services.data_access import (
    get_current_user, get_current_group,
    replace_group_active_shops, load_shops_df, load_reviews_df
)
from ui.helpers import full_name, shop_label, render_star, add_last_visit_column, green_visited_rows

st.set_page_config(page_title=APP_TITLE, layout="wide")
init_db()
st.title(APP_TITLE)

session = SessionLocal()

if "user_id" not in st.session_state:
    st.subheader("Join or Create Group")
    t1, t2 = st.tabs(["Join Group", "Create Group (Admin)"])
    with t1:
        code = st.text_input("Group code")
        fn = st.text_input("First name")
        ln = st.text_input("Last name")
        if st.button("Join group"):
            g = session.query(Group).filter_by(join_code=code.strip()).first()
            if not g:
                st.error("Group not found.")
            elif not fn.strip() or not ln.strip():
                st.error("Enter first and last name.")
            else:
                u, _ = get_or_create_user(session, g.id, fn, ln)
                st.session_state["user_id"] = u.id
                st.session_state["group_id"] = g.id
                st.rerun()

    with t2:
        gname = st.text_input("Group name", value="Oxford Coffee Club")
        gcode = st.text_input("Join code")
        afn = st.text_input("Admin first name")
        aln = st.text_input("Admin last name")
        if st.button("Create group"):
            if not all([gname.strip(), gcode.strip(), afn.strip(), aln.strip()]):
                st.error("Fill all fields.")
            else:
                g, created = get_or_create_group(session, gname.strip(), gcode.strip())
                if not created:
                    st.error("Join code already exists.")
                else:
                    u, _ = get_or_create_user(session, g.id, afn, aln, role="admin")
                    st.session_state["user_id"] = u.id
                    st.session_state["group_id"] = g.id
                    st.success(f"Group created. Share code: {g.join_code}")
                    st.rerun()
    st.stop()

current_user = get_current_user(session, st.session_state.get("user_id"))
current_group = get_current_group(session, st.session_state.get("group_id"))
if not current_user or not current_group:
    st.error("Session expired.")
    st.stop()

st.caption(f"Logged in as **{full_name(current_user)}** | Group: **{current_group.name}** ({current_group.join_code})")
if st.button("Logout"):
    st.session_state.clear()
    st.rerun()

tabs = ["Map + Submit", "Leaderboards", "Data"]
if current_user.role == "admin":
    tabs.append("Admin")
tab_objs = st.tabs(tabs)
tab_map, tab_lb, tab_data = tab_objs[:3]
tab_admin = tab_objs[3] if current_user.role == "admin" else None

with tab_map:
    st.subheader("Map + Submit")
    cL, cM, cR = st.columns([1,2,1])

    with cM:
        with st.form("controls_form", clear_on_submit=False):
            postcode = st.text_input("Postcode", value=DEFAULT_POSTCODE)
            radius = st.number_input("Radius (km)", min_value=MIN_RADIUS_KM, max_value=MAX_RADIUS_KM, value=DEFAULT_RADIUS_KM, step=1)
            fast_mode = st.checkbox("Fast mode (recommended)", value=True)
            submitted = st.form_submit_button("Refresh cafes")

        if submitted:
            try:
                geo = geocode_text_cached(postcode)
                if not geo:
                    st.error("Could not geocode postcode.")
                else:
                    lat, lon = geo
                    shops = fetch_cafes_by_radius_cached(lat, lon, radius_km=int(radius), fast_mode=fast_mode, max_results=MAX_RESULTS)
                    if not shops and fast_mode:
                        shops = fetch_cafes_by_radius_cached(lat, lon, radius_km=int(radius), fast_mode=False, max_results=MAX_RESULTS)
                    if not shops:
                        st.warning("No cafes found.")
                    else:
                        n = replace_group_active_shops(session, current_group.id, shops)
                        st.success(f"Updated active cafes: {n}")
                    st.session_state["map_center"] = (lat, lon)
                    st.session_state["map_radius"] = int(radius)
                    st.session_state["map_postcode"] = postcode.strip().upper()
            except Exception as e:
                st.error(f"Fetch failed: {e}")

    shops_df = load_shops_df(session, current_group.id, active_only=True)
    reviews_df = load_reviews_df(session, current_group.id)

    if shops_df.empty:
        st.info("No cafes loaded yet.")
    else:
        shops_df["Cafe"] = shops_df.apply(shop_label, axis=1)
        shops_df = add_last_visit_column(shops_df, reviews_df, current_user.id)

        focus = st.selectbox("Focus cafe", ["(None)"] + shops_df["Cafe"].tolist())

        center_lat = float(shops_df["lat"].mean())
        center_lon = float(shops_df["lon"].mean())
        zoom = 13
        if focus != "(None)":
            rr = shops_df[shops_df["Cafe"] == focus].iloc[0]
            center_lat, center_lon = float(rr["lat"]), float(rr["lon"])
            zoom = 16

        fmap = folium.Map(location=[center_lat, center_lon], zoom_start=zoom, tiles="OpenStreetMap")

        if st.session_state.get("map_center"):
            clat, clon = st.session_state["map_center"]
            rkm = st.session_state.get("map_radius", 2)
            pcode = st.session_state.get("map_postcode", "N/A")
            folium.Circle([clat, clon], radius=float(rkm)*1000, color="blue", weight=2, fill=True, fill_opacity=0.08,
                          tooltip=f"{pcode} • Radius {rkm} km").add_to(fmap)
            folium.Marker([clat, clon], tooltip=f"Search centre: {pcode}",
                          icon=folium.Icon(color="green", icon="info-sign")).add_to(fmap)

        for _, r in shops_df.iterrows():
            color = "red" if (focus != "(None)" and r["Cafe"] == focus) else "blue"
            popup = f"{r['name']}<br>{r['address'] or ''}<br>{r['postcode'] or ''}<br>Last visit: {r['Last visit'] if r['Last visit'] == r['Last visit'] else '-'}"
            folium.Marker([r["lat"], r["lon"]], popup=popup, tooltip=r["Cafe"],
                          icon=folium.Icon(color=color, icon="coffee", prefix="fa")).add_to(fmap)

        st_folium(fmap, width=None, height=520, key="main_map")

        st.markdown("### Submit review")
        c1, c2 = st.columns([2,1])
        with c1:
            options = {r["Cafe"]: int(r["id"]) for _, r in shops_df.iterrows()}
            default_label = focus if focus in options else list(options.keys())[0]
            sel = st.selectbox("Select cafe", list(options.keys()), index=list(options.keys()).index(default_label))
            sid = options[sel]

            s1,s2,s3,s4,s5 = st.columns(5)
            if "selected_rating" not in st.session_state:
                st.session_state["selected_rating"] = 4
            if s1.button("⭐", key="sr1"): st.session_state["selected_rating"] = 1
            if s2.button("⭐⭐", key="sr2"): st.session_state["selected_rating"] = 2
            if s3.button("⭐⭐⭐", key="sr3"): st.session_state["selected_rating"] = 3
            if s4.button("⭐⭐⭐⭐", key="sr4"): st.session_state["selected_rating"] = 4
            if s5.button("⭐⭐⭐⭐⭐", key="sr5"): st.session_state["selected_rating"] = 5
            st.write(f"Rating: **{render_star(st.session_state['selected_rating'])}**")

            decaf = st.checkbox("Decaf")
            iced = st.checkbox("Iced")
            drink_opts = []
            for d in DRINK_BASE:
                x = d
                if decaf: x = f"Decaf {x}"
                if iced: x = f"Iced {x}"
                drink_opts.append(x)
            dc = st.selectbox("Drink order", drink_opts + ["Other"])
            drink = st.text_input("Other drink") if dc == "Other" else dc
            if dc == "Other" and not drink.strip():
                drink = "Other"

            today = date.today()
            st.write(f"Date: **{today}**")

            if st.button("Submit review", type="primary"):
                ex = session.query(Review).filter_by(
                    group_id=current_group.id, user_id=current_user.id, shop_id=sid, review_date=today
                ).first()
                if ex:
                    st.error("You already reviewed this cafe today.")
                else:
                    session.add(Review(group_id=current_group.id, user_id=current_user.id, shop_id=sid,
                                       rating=st.session_state["selected_rating"],
                                       drink_order=drink.strip() if drink else None, review_date=today))
                    session.commit()
                    st.success("Review submitted.")
                    st.rerun()

        with c2:
            st.markdown("### Cafe list")
            table = shops_df[["Cafe", "postcode", "Last visit"]].rename(columns={"postcode":"Postcode"}).copy()
            st.dataframe(table.style.apply(green_visited_rows, axis=None), use_container_width=True, hide_index=True)

with tab_lb:
    st.subheader("Leaderboards")
    r = load_reviews_df(session, current_group.id)
    if r.empty:
        st.info("No reviews yet.")
    else:
        drink_filter = st.selectbox("Filter by drink", ["All"] + sorted([d for d in r["drink_order"].dropna().unique().tolist() if str(d).strip()]))
        df = r.copy()
        if drink_filter != "All":
            df = df[df["drink_order"] == drink_filter]

        kind = st.selectbox("Type", ["Best coffee shops", "Top coffee drinkers"])
        if kind == "Best coffee shops":
            lb = df.groupby("shop_name", as_index=False).agg(Reviews=("rating","count"), Avg=("rating","mean"))
            if lb.empty:
                st.warning("No data.")
            else:
                lb["Average rating"] = lb["Avg"].round(1).apply(render_star)
                out = lb.rename(columns={"shop_name":"Coffee shop"}).sort_values(["Reviews","Avg"], ascending=[False, False])
                st.dataframe(out[["Coffee shop","Reviews","Average rating"]], use_container_width=True, hide_index=True)
        else:
            td = df.groupby("reviewer", as_index=False).agg(Reviews=("review_id","count")).sort_values("Reviews", ascending=False)
            st.dataframe(td, use_container_width=True, hide_index=True)

with tab_data:
    st.subheader("Data export (group only)")
    rv = load_reviews_df(session, current_group.id)
    sh = load_shops_df(session, current_group.id, active_only=True)

    a,b = st.columns(2)
    with a:
        st.write("Reviews")
        st.dataframe(rv, use_container_width=True, hide_index=True)
        st.download_button("Download reviews CSV", rv.to_csv(index=False).encode("utf-8"), "reviews.csv", "text/csv")
    with b:
        st.write("Shops")
        x = sh.copy()
        if not x.empty:
            x["Cafe"] = x.apply(shop_label, axis=1)
            st.dataframe(x[["Cafe","postcode","lat","lon","source"]], use_container_width=True, hide_index=True)
        st.download_button("Download shops CSV", sh.to_csv(index=False).encode("utf-8"), "shops.csv", "text/csv")

if tab_admin is not None:
    with tab_admin:
        st.subheader("Admin (current group only)")
        shops_all = load_shops_df(session, current_group.id, active_only=False)

        if shops_all.empty:
            st.info("No cafes in this group.")
        else:
            active = shops_all[shops_all["active"] == 1].copy()
            inactive = shops_all[shops_all["active"] == 0].copy()

            st.markdown("### Cafes")
            if not active.empty:
                active["label"] = active.apply(shop_label, axis=1)
                pick = st.selectbox("Deactivate one", active["label"].tolist())
                if st.button("Deactivate selected"):
                    sid = int(active[active["label"] == pick]["id"].iloc[0])
                    s = session.query(Shop).filter_by(id=sid, group_id=current_group.id).first()
                    if s:
                        s.active = 0
                        session.commit()
                        st.success("Deactivated.")
                        st.rerun()

            kw = st.text_input("Deactivate by chain keyword")
            if st.button("Deactivate keyword matches"):
                if kw.strip():
                    rows = session.query(Shop).filter(
                        Shop.group_id == current_group.id,
                        Shop.active == 1,
                        func.lower(Shop.name).contains(kw.strip().lower())
                    ).all()
                    for rr in rows:
                        rr.active = 0
                    session.commit()
                    st.success(f"Deactivated {len(rows)} cafes.")
                    st.rerun()

            if not inactive.empty:
                inactive["label"] = inactive.apply(shop_label, axis=1)
                rp = st.selectbox("Restore one", inactive["label"].tolist())
                if st.button("Restore selected"):
                    sid = int(inactive[inactive["label"] == rp]["id"].iloc[0])
                    s = session.query(Shop).filter_by(id=sid, group_id=current_group.id).first()
                    if s:
                        s.active = 1
                        session.commit()
                        st.success("Restored.")
                        st.rerun()

        st.markdown("---")
        st.markdown("### Reviews")
        rv = load_reviews_df(session, current_group.id)
        if rv.empty:
            st.info("No reviews.")
        else:
            rv["label"] = rv.apply(lambda r: f"[{r['review_id']}] {r['reviewer']} | {r['shop_name']} | {r['rating']}⭐ | {r['review_date']}", axis=1)
            rp = st.selectbox("Delete single review", rv["label"].tolist())
            if st.button("Delete selected review"):
                rid = int(rv[rv["label"] == rp]["review_id"].iloc[0])
                obj = session.query(Review).filter_by(id=rid, group_id=current_group.id).first()
                if obj:
                    session.delete(obj)
                    session.commit()
                    st.success("Deleted review.")
                    st.rerun()

            confirm = st.checkbox("I understand deleting ALL reviews in this group cannot be undone")
            if st.button("Delete ALL reviews in group"):
                if not confirm:
                    st.error("Confirm first.")
                else:
                    n = session.query(Review).filter(Review.group_id == current_group.id).delete()
                    session.commit()
                    st.success(f"Deleted {n} reviews.")
                    st.rerun()