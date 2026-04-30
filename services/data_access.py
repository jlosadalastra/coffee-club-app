import pandas as pd
from sqlalchemy import func
from db import Group, User, Shop, Review

def get_current_user(session, user_id):
    return session.query(User).filter_by(id=user_id).first() if user_id else None

def get_current_group(session, group_id):
    return session.query(Group).filter_by(id=group_id).first() if group_id else None

def replace_group_active_shops(session, group_id, shops):
    session.query(Shop).filter(
        Shop.group_id == group_id, Shop.active == 1
    ).update({"active": 0}, synchronize_session=False)

    count = 0
    for s in shops:
        ex = session.query(Shop).filter(
            Shop.group_id == group_id,
            func.lower(Shop.name) == s["name"].lower(),
            func.abs(Shop.lat - s["lat"]) < 0.0001,
            func.abs(Shop.lon - s["lon"]) < 0.0001
        ).first()

        if ex:
            ex.active = 1
            ex.address = s.get("address")
            ex.postcode = s.get("postcode")
            ex.source = s.get("source", ex.source)
        else:
            session.add(Shop(
                group_id=group_id,
                name=s["name"],
                address=s.get("address"),
                postcode=s.get("postcode"),
                lat=s["lat"],
                lon=s["lon"],
                source=s.get("source", "radius"),
                active=1
            ))
        count += 1

    session.commit()
    return count

def load_shops_df(session, group_id, active_only=True):
    q = session.query(Shop).filter(Shop.group_id == group_id)
    if active_only:
        q = q.filter(Shop.active == 1)
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=["id","name","address","postcode","lat","lon","active","source"])
    return pd.DataFrame([{
        "id": r.id, "name": r.name, "address": r.address, "postcode": r.postcode,
        "lat": r.lat, "lon": r.lon, "active": r.active, "source": r.source
    } for r in rows])

def load_reviews_df(session, group_id):
    q = (
        session.query(
            Review.id.label("review_id"),
            Review.rating,
            Review.drink_order,
            Review.review_date,
            Review.user_id,
            Review.shop_id,
            User.first_name,
            User.last_name,
            Shop.name.label("shop_name")
        )
        .join(User, User.id == Review.user_id)
        .join(Shop, Shop.id == Review.shop_id)
        .filter(Review.group_id == group_id)
    )
    rows = q.all()
    if not rows:
        return pd.DataFrame(columns=["review_id","rating","drink_order","review_date","user_id","shop_id","reviewer","shop_name"])
    return pd.DataFrame([{
        "review_id": r.review_id,
        "rating": r.rating,
        "drink_order": r.drink_order,
        "review_date": r.review_date,
        "user_id": r.user_id,
        "shop_id": r.shop_id,
        "reviewer": f"{r.first_name} {r.last_name}",
        "shop_name": r.shop_name
    } for r in rows])