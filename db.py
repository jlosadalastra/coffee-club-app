import os
import secrets
from datetime import datetime, date

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime, ForeignKey,
    UniqueConstraint, Text
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_PATH = os.getenv("DB_PATH", "sqlite:///data/coffee_app_v2.db")

engine = create_engine(
    DB_PATH,
    connect_args={"check_same_thread": False} if DB_PATH.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    join_code = Column(String(30), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    users = relationship("User", back_populates="group")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    first_name = Column(String(80), nullable=False)
    last_name = Column(String(80), nullable=False)
    role = Column(String(20), default="member")
    created_at = Column(DateTime, default=datetime.utcnow)

    group = relationship("Group", back_populates="users")

    __table_args__ = (
        UniqueConstraint("group_id", "first_name", "last_name", name="uq_user_name_per_group"),
    )


class Shop(Base):
    __tablename__ = "shops"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    name = Column(String(180), nullable=False)
    address = Column(Text, nullable=True)
    postcode = Column(String(20), nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    source = Column(String(30), default="osm")
    active = Column(Integer, default=1)


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    drink_order = Column(String(120), nullable=True)
    review_date = Column(Date, nullable=False, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", "shop_id", "review_date", name="uq_review_day_group"),
    )


class PostcodeConfig(Base):
    __tablename__ = "postcode_config"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    postcode_prefix = Column(String(10), nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "postcode_prefix", name="uq_postcode_per_group"),
    )


def init_db():
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_or_create_group(session, group_name: str, join_code: str | None = None):
    code = (join_code or secrets.token_hex(4)).upper().replace("-", "")
    g = session.query(Group).filter_by(join_code=code).first()
    if g:
        return g, False
    g = Group(name=group_name, join_code=code)
    session.add(g)
    session.commit()
    session.refresh(g)
    return g, True


def get_or_create_user(session, group_id: int, first_name: str, last_name: str, role="member"):
    u = session.query(User).filter_by(
        group_id=group_id,
        first_name=first_name.strip(),
        last_name=last_name.strip()
    ).first()
    if u:
        return u, False
    u = User(group_id=group_id, first_name=first_name.strip(), last_name=last_name.strip(), role=role)
    session.add(u)
    session.commit()
    session.refresh(u)
    return u, True