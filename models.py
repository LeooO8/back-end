from sqlalchemy import Column, String, BigInteger, Integer, Float, DateTime
from sqlalchemy.orm import declarative_base
from datetime import datetime, timezone

Base = declarative_base()

# WICHTIG - Mehrserver-Unterstützung:
# User.id und Setting.key speichern intern "<guild_id>:<eigentliche_id>",
# damit derselbe Discord-Nutzer bzw. derselbe Einstellungsschlüssel auf
# jedem Server unabhängig existieren kann, ohne das Datenbankschema
# (Primärschlüssel) ändern zu müssen. Alle anderen Tabellen haben dafür
# eine eigene guild_id-Spalte.


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)          # "<guild_id>:<Discord User-ID>"
    guild_id = Column(String, nullable=True)
    discord_id = Column(String, nullable=True)     # reine Discord User-ID, ohne Server-Präfix
    username = Column(String, nullable=False)
    role = Column(String, default="Mitglied")
    balance = Column(BigInteger, default=500)
    status = Column(String, default="offline")
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    on_duty_fraction = Column(String, nullable=True)  # Name der Fraktion, bei der aktuell im Dienst (oder None)
    last_work = Column(DateTime, nullable=True)   # Zeitpunkt der letzten /work-Nutzung
    last_daily = Column(DateTime, nullable=True)  # Zeitpunkt der letzten /daily-Nutzung
    last_seen = Column(DateTime, nullable=True)   # Letzte Interaktion mit dem Bot/Dashboard
    afk_reason = Column(String, nullable=True)    # Grund, falls aktuell AFK (None = nicht AFK)
    afk_since = Column(DateTime, nullable=True)   # Seit wann AFK
    cash = Column(BigInteger, default=0)          # Bargeld (getrennt vom Bank-Guthaben "balance")
    duty_started_at = Column(DateTime, nullable=True)  # Wann die aktuelle Dienstschicht begonnen hat


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
    role_id = Column(String, nullable=True)  # Discord-Rolle, die beim Kauf automatisch vergeben wird


class DutyFraction(Base):
    __tablename__ = "duty_fractions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    on_duty = Column(Integer, default=0)
    total = Column(Integer, default=0)
    hours_today = Column(Float, default=0.0)
    channel_id = Column(String, nullable=True)  # Discord-Kanal-ID für Dienst-Embeds


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
    participants = Column(String, nullable=True)  # Discord-User-IDs, kommagetrennt


class LogEntry(Base):
    __tablename__ = "logs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    type = Column(String, nullable=False)
    text = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)  # "<guild_id>:<eigentlicher_schluessel>"
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
    token = Column(String, nullable=True, unique=True)  # eindeutige Sitzungs-ID (steckt auch im JWT)
    revoked = Column(String, default="")  # "yes", falls die Sitzung beendet wurde


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    user_id = Column(String, nullable=False)      # Discord-ID des Erstellers
    username = Column(String, nullable=False)
    subject = Column(String, nullable=True)        # Kurzer Grund/Betreff
    category = Column(String, nullable=True)        # Gewählte Kategorie aus dem Panel (falls verwendet)
    case_id = Column(String, nullable=True)         # Kurzer Fall-Code fürs Anzeigen, z.B. "S-A1B2C3D4"
    status = Column(String, default="offen")       # "offen" oder "geschlossen"
    channel_id = Column(String, nullable=True)     # Discord-Kanal-ID des Tickets
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String, nullable=True)      # Username, der das Ticket geschlossen hat
    claimed_by = Column(String, nullable=True)      # Username des Team-Mitglieds, das sich zuständig gemeldet hat
    feedback_rating = Column(Integer, nullable=True)  # 1-5 Sterne, vom Ersteller nach dem Schließen abgegeben


class Todo(Base):
    __tablename__ = "todos"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    status = Column(String, default="offen")       # "offen" oder "erledigt"
    assigned_to = Column(String, nullable=True)     # Username, optional
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    done_at = Column(DateTime, nullable=True)
