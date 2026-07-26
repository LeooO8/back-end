from sqlalchemy import Column, String, BigInteger, Integer, Float, DateTime
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)          # Discord User-ID
    username = Column(String, nullable=False)
    role = Column(String, default="Mitglied")
    balance = Column(BigInteger, default=500)
    status = Column(String, default="offline")
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    from_user = Column(String, nullable=False)
    to_user = Column(String, nullable=False)
    amount = Column(BigInteger, nullable=False)
    type = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ShopItem(Base):
    __tablename__ = "shop_items"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(BigInteger, nullable=False)
    sold = Column(Integer, default=0)


class DutyFraction(Base):
    __tablename__ = "duty_fractions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    on_duty = Column(Integer, default=0)
    total = Column(Integer, default=0)
    hours_today = Column(Float, default=0.0)
    channel_id = Column(String, nullable=True)  # Discord-Kanal-ID für Dienst-Embeds


class Giveaway(Base):
    __tablename__ = "giveaways"
    id = Column(Integer, primary_key=True, autoincrement=True)
    prize = Column(String, nullable=False)
    entries = Column(Integer, default=0)
    ends_at = Column(DateTime, nullable=True)
    status = Column(String, default="aktiv")
    winner = Column(String, nullable=True)


class LogEntry(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String, nullable=False)
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
