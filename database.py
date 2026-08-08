import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Server-ID deines ursprünglichen Servers - wird nur für die einmalige
# Migration bestehender Altdaten in die neue Mehrserver-Struktur genutzt.
LEGACY_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")


def init_db():
    Base.metadata.create_all(bind=engine)
    # Kleine, sichere Migration: neue Spalten an bestehenden Tabellen ergänzen,
    # ohne bestehende Daten zu löschen (create_all legt nur neue Tabellen an,
    # keine neuen Spalten an bereits existierenden).
    with engine.connect() as conn:
        for stmt in [
            "ALTER TABLE duty_fractions ADD COLUMN channel_id VARCHAR",
            "ALTER TABLE users ADD COLUMN on_duty_fraction VARCHAR",
            "ALTER TABLE users ADD COLUMN last_work DATETIME",
            "ALTER TABLE users ADD COLUMN last_daily DATETIME",
            "ALTER TABLE users ADD COLUMN afk_reason VARCHAR",
            "ALTER TABLE users ADD COLUMN afk_since DATETIME",
            "ALTER TABLE users ADD COLUMN last_seen DATETIME",
            "ALTER TABLE giveaways ADD COLUMN channel_id VARCHAR",
            "ALTER TABLE giveaways ADD COLUMN message_id VARCHAR",
            "ALTER TABLE giveaways ADD COLUMN participants VARCHAR",
            # Mehrserver-Unterstützung:
            "ALTER TABLE users ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE users ADD COLUMN discord_id VARCHAR",
            "ALTER TABLE transactions ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE shop_items ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE duty_fractions ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE giveaways ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE logs ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE login_sessions ADD COLUMN guild_id VARCHAR",
            "ALTER TABLE login_sessions ADD COLUMN token VARCHAR",
            "ALTER TABLE login_sessions ADD COLUMN revoked VARCHAR DEFAULT ''",
            "ALTER TABLE users ADD COLUMN cash BIGINT DEFAULT 0",
            "ALTER TABLE users ADD COLUMN duty_started_at DATETIME",
            "ALTER TABLE shop_items ADD COLUMN role_id VARCHAR",
            "ALTER TABLE tickets ADD COLUMN category VARCHAR",
            "ALTER TABLE tickets ADD COLUMN case_id VARCHAR",
        ]:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception:
                pass

        # Einmalige Migration: bestehende Altdaten (von vor der Mehrserver-
        # Unterstützung) automatisch deinem ursprünglichen Server zuordnen,
        # damit nichts verloren geht.
        if LEGACY_GUILD_ID:
            try:
                already_migrated = conn.execute(
                    text("SELECT 1 FROM settings WHERE \"key\" = :k"),
                    {"k": f"{LEGACY_GUILD_ID}:_migrated_v1"},
                ).fetchone()
                if not already_migrated:
                    # Nutzer: alte IDs (ohne ':') auf "<guild>:<id>" umstellen
                    old_users = conn.execute(
                        text("SELECT id FROM users WHERE id NOT LIKE '%:%'")
                    ).fetchall()
                    for (old_id,) in old_users:
                        conn.execute(
                            text("UPDATE users SET id = :new_id, guild_id = :gid, discord_id = :old_id WHERE id = :old_id"),
                            {"new_id": f"{LEGACY_GUILD_ID}:{old_id}", "gid": LEGACY_GUILD_ID, "old_id": old_id},
                        )
                    # Settings: alte Schlüssel (ohne ':') auf "<guild>:<key>" umstellen
                    old_settings = conn.execute(
                        text("SELECT \"key\" FROM settings WHERE \"key\" NOT LIKE '%:%'")
                    ).fetchall()
                    for (old_key,) in old_settings:
                        conn.execute(
                            text("UPDATE settings SET \"key\" = :new_key WHERE \"key\" = :old_key"),
                            {"new_key": f"{LEGACY_GUILD_ID}:{old_key}", "old_key": old_key},
                        )
                    # Übrige Tabellen: fehlende guild_id auffüllen
                    for tbl in ["transactions", "shop_items", "duty_fractions", "giveaways", "logs", "login_sessions"]:
                        conn.execute(
                            text(f"UPDATE {tbl} SET guild_id = :gid WHERE guild_id IS NULL"),
                            {"gid": LEGACY_GUILD_ID},
                        )
                    conn.execute(
                        text("INSERT INTO settings (\"key\", value) VALUES (:k, 'yes')"),
                        {"k": f"{LEGACY_GUILD_ID}:_migrated_v1"},
                    )
                    conn.commit()
                    print(f"Migration abgeschlossen: Altdaten wurden Server {LEGACY_GUILD_ID} zugeordnet.")
            except Exception as e:
                print(f"Migration übersprungen/fehlgeschlagen (meist harmlos, z.B. schon migriert): {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
