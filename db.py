from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey,
    UniqueConstraint, func, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, date
import os

DB_PATH = os.getenv("DB_PATH", "sqlite:///data/coffee_app.db")
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False} if DB_PATH.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    join_code = Column(String(30), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="group")
    postcode_configs = relationship("PostcodeConfig", back_populates="group")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    first_name = Column(String(80), nullable=False)
    last_name = Column(String(80), nullable=False)
    role = Column(String(20), default="member")  # admin/member
    created_at = Column(DateTime, default=datetime.utcnow)

    group = relationship("Group", back_populates="users")
    reviews = relationship("Review", back_populates="user")

    __table_args__ = (
        UniqueConstraint("group_id", "first_name", "last_name", name="uq_user_name_per_group"),
    )


class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True)
    name = Column(String(180), nullable=False)
    address = Column(Text, nullable=True)
    postcode = Column(String(20), nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    source = Column(String(30), default="osm")
    active = Column(Integer, default=1)

    reviews = relationship("Review", back_populates="shop")


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    rating = Column(Integer, nullable=False)  # 1..5
    drink_order = Column(String(120), nullable=True)
    review_date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="reviews")
    shop = relationship("Shop", back_populates="reviews")

    __table_args__ = (
        UniqueConstraint("user_id", "shop_id", "review_date", name="uq_one_review_per_shop_per_day"),
    )


class PostcodeConfig(Base):
    __tablename__ = "postcode_config"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    postcode_prefix = Column(String(10), nullable=False)

    group = relationship("Group", back_populates="postcode_configs")

    __table_args__ = (
        UniqueConstraint("group_id", "postcode_prefix", name="uq_postcode_per_group"),
    )


def init_db():
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_or_create_group(session, group_name: str, join_code: str):
    g = session.query(Group).filter_by(join_code=join_code).first()
    if g:
        return g, False
    g = Group(name=group_name, join_code=join_code)
    session.add(g)
    session.commit()
    session.refresh(g)
    return g, True


def get_or_create_user(session, group_id: int, first_name: str, last_name: str, role="member"):
    u = session.query(User).filter_by(group_id=group_id, first_name=first_name.strip(), last_name=last_name.strip()).first()
    if u:
        return u, False
    u = User(group_id=group_id, first_name=first_name.strip(), last_name=last_name.strip(), role=role)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u, True