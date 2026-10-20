
from sqlalchemy import Column, String, BigInteger, Integer, Float, DateTime
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    guild_id = Column(String, nullable=True)
    discord_id = Column(String, nullable=True)
    username = Column(String, nullable=False)
    role = Column(String, default="Mitglied")
    balance = Column(BigInteger, default=500)
    status = Column(String, default="offline")
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    on_duty_fraction = Column(String, nullable=True)
    last_work = Column(DateTime, nullable=True)
    last_daily = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    afk_reason = Column(String, nullable=True)
    afk_since = Column(DateTime, nullable=True)
    cash = Column(BigInteger, default=0)
    duty_started_at = Column(DateTime, nullable=True)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    from_user = Column(String, nullable=False)
    to_user = Column(String, nullable=False)
    amount = Column(BigInteger, nullable=False)
    type = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ShopItem(Base):
    __tablename__ = "shop_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(BigInteger, nullable=False)
    sold = Column(Integer, default=0)
    role_id = Column(String, nullable=True)


class DutyFraction(Base):
    __tablename__ = "duty_fractions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    on_duty = Column(Integer, default=0)
    total = Column(Integer, default=0)
    hours_today = Column(Float, default=0.0)
    channel_id = Column(String, nullable=True)


class Giveaway(Base):
    __tablename__ = "giveaways"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    prize = Column(String, nullable=False)
    entries = Column(Integer, default=0)
    ends_at = Column(DateTime, nullable=True)
    status = Column(String, default="aktiv")
    winner = Column(String, nullable=True)
    channel_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    participants = Column(String, nullable=True)


class LogEntry(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    type = Column(String, nullable=False)
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    user_id = Column(String, nullable=False)
    username = Column(String, nullable=False)
    ip = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    token = Column(String, nullable=True, unique=True)
    revoked = Column(String, default="")


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    user_id = Column(String, nullable=False)
    username = Column(String, nullable=False)
    subject = Column(String, nullable=True)
    category = Column(String, nullable=True)
    case_id = Column(String, nullable=True)
    status = Column(String, default="offen")
    channel_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String, nullable=True)
    claimed_by = Column(String, nullable=True)
    feedback_rating = Column(Integer, nullable=True)


class Todo(Base):
    __tablename__ = "todos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    status = Column(String, default="offen")
    assigned_to = Column(String, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    done_at = Column(DateTime, nullable=True)


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    von = Column(String, nullable=True)
    bis = Column(String, nullable=True)
    ort = Column(String, nullable=True)
    inhalt = Column(String, nullable=True)
    created_by = Column(String, nullable=False)
    channel_id = Column(String, nullable=True)
    message_id = Column(String, nullable=True)
    accepted = Column(String, default="")
    declined = Column(String, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DailyStat(Base):
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    date = Column(String, nullable=False)
    member_count = Column(Integer, default=0)
    message_count = Column(Integer, default=0)
    total_balance = Column(BigInteger, default=0)
    open_tickets = Column(Integer, default=0)
    active_giveaways = Column(Integer, default=0)


class Ausweis(Base):
    __tablename__ = "ausweise"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=False)
    discord_id = Column(String, nullable=False)
    vorname = Column(String, nullable=False)
    nachname = Column(String, nullable=False)
    geburtsdatum = Column(String, nullable=True)
    dienstgrad = Column(String, nullable=True)
    fraktion = Column(String, nullable=True)
    ausweisnummer = Column(String, nullable=False, unique=True)
    ausgestellt_am = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )
    ausgestellt_von = Column(String, nullable=True)
    status = Column(String, default="gültig")
