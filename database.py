import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    # Kleine, sichere Migration: neue Spalten an bestehenden Tabellen ergänzen,
    # ohne bestehende Daten zu löschen (create_all legt nur neue Tabellen an,
    # keine neuen Spalten an bereits existierenden).
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE duty_fractions ADD COLUMN channel_id VARCHAR"))
            conn.commit()
        except Exception:
            pass  # Spalte existiert schon oder Tabelle ist neu angelegt - beides okay
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN on_duty_fraction VARCHAR"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_work DATETIME"))
            conn.commit()
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_daily DATETIME"))
            conn.commit()
        except Exception:
            pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
