"""
Ein einziger Service, der ZWEI Dinge gleichzeitig macht:
1. Den echten Discord-Bot (discord.py) - reagiert auf Befehle im Server
2. Die Dashboard-API (FastAPI) - liefert Daten fürs Web-Dashboard

Beide teilen sich dieselbe Datenbank, laufen im selben Prozess und werden
zusammen als EIN Dienst gehostet (z.B. auf Railway).

MEHRSERVER-UNTERSTÜTZUNG: Der Bot kann auf mehreren Discord-Servern
gleichzeitig laufen. Jeder Server hat seine EIGENEN, komplett getrennten
Daten (Konten, Shop, Dienste, Giveaways, Einstellungen, Logs). Das
Dashboard fragt dafür bei jeder Anfrage eine guild_id (Server-ID) mit.
"""
import os
import io
import time
import asyncio
import random
import string
import textwrap
from datetime import datetime, timedelta, timezone
from typing import Literal
import httpx
import jwt
import uuid
import discord
from PIL import Image, ImageDraw, ImageFont
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI, Depends, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db, init_db, SessionLocal
from models import User, Transaction, ShopItem, DutyFraction, Giveaway, LogEntry, Setting, LoginSession, Ticket, Todo

# ---------- Konfiguration (kommt aus Umgebungsvariablen, siehe README) ----------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8000/auth/callback")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
DISCORD_API = "https://discord.com/api"


def ukey(guild_id, discord_id) -> str:
    """Baut den internen User-Schlüssel '<guild_id>:<discord_id>'."""
    return f"{guild_id}:{discord_id}"


def gkey(guild_id, key) -> str:
    """Baut den internen Settings-Schlüssel '<guild_id>:<key>'."""
    return f"{guild_id}:{key}"


def get_setting_value(db, guild_id, key, default=None):
    s = db.query(Setting).get(gkey(guild_id, key))
    return s.value if s and s.value not in (None, "") else default


# ---------- Einheitliches Embed-Design ----------
BRAND_COLOR = 0x2B2D31       # Dezentes Dunkelgrau - primäre Aktionen (Käufe, Willkommen, Ankündigungen) - kein knalliger Farbbalken mehr
COLOR_SUCCESS = 0x57F287     # Grün - erfolgreiche Aktionen
COLOR_DANGER = 0xED4245      # Rot - Fehler/Ablehnungen
COLOR_INFO = 0x2B2D31        # Dezentes Dunkelgrau - neutrale Infos (Dienst, Tickets)
COLOR_LOG = 0x2B2D31         # Dezentes Dunkelgrau - Log-Kanal-Einträge

LOG_TYPE_STYLE = {
    "shop": ("🛒", COLOR_SUCCESS),
    "bank": ("🏦", COLOR_INFO),
    "dienst": ("🚔", COLOR_INFO),
    "afk": ("😴", COLOR_LOG),
    "login": ("🔐", COLOR_LOG),
    "system": ("⚙️", COLOR_LOG),
}


def apply_brand(embed: discord.Embed, db, guild: "discord.Guild | None") -> discord.Embed:
    """Setzt einheitlich die Autor-Zeile (Server-Name + Icon oben im Embed) und,
    falls unter Einstellungen ein 'embed_banner_url' hinterlegt ist, ein Banner-
    Bild ganz unten. Bei Embeds mit eigenem Banner (Willkommen, Ticket-Panel)
    danach einfach embed.set_image() erneut aufrufen - das überschreibt es gezielt."""
    if guild:
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
        banner_url = get_setting_value(db, str(guild.id), "embed_banner_url")
        if banner_url:
            embed.set_image(url=banner_url)
    return embed


MODULE_NAMES = {
    "bank": "Bank-System",
    "shop": "Shop-System",
    "dienst": "Dienst-System",
    "afk": "AFK-System",
    "giveaways": "Giveaways",
    "tickets": "Tickets",
}


def is_module_enabled(db, guild_id, module_key: str) -> bool:
    """Prüft, ob ein Modul für einen Server aktiviert ist. Module sind
    standardmäßig aktiv - erst eine explizite Einstellung 'modul_<key>' auf
    'nein'/'false'/'0'/'aus' schaltet sie ab (Setting-Key z.B. 'modul_bank')."""
    value = get_setting_value(db, str(guild_id), f"modul_{module_key}")
    if value is None:
        return True
    return value.strip().lower() not in ("nein", "no", "false", "0", "aus")


async def module_disabled_reply(interaction: discord.Interaction, module_key: str):
    name = MODULE_NAMES.get(module_key, module_key)
    await interaction.response.send_message(f"🚫 **{name}** ist auf diesem Server deaktiviert.", ephemeral=True)


# =========================================================
# TEIL 1: DER DISCORD-BOT
# =========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
BOT_START_TIME = datetime.now(timezone.utc)


async def maintenance_check(interaction: discord.Interaction) -> bool:
    """Globaler Check vor JEDEM Slash-Befehl: blockt alle Befehle im
    Wartungsmodus außer für Server-Administratoren."""
    if not interaction.guild_id:
        return True
    db = SessionLocal()
    try:
        maint = get_setting_value(db, str(interaction.guild_id), "wartungsmodus")
    finally:
        db.close()
    if maint and maint.strip().lower() in ("ja", "yes", "true", "1", "an"):
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "🛠️ Der Bot befindet sich gerade im Wartungsmodus. Bitte versuch es in Kürze erneut.", ephemeral=True
        )
        return False
    return True


bot.tree.interaction_check = maintenance_check


def get_starting_balance(db, guild_id) -> int:
    setting = db.query(Setting).get(gkey(guild_id, "startguthaben"))
    try:
        return int(setting.value) if setting and setting.value else 500
    except (TypeError, ValueError):
        return 500


def get_or_create_user_by_id(db, guild_id: str, discord_id: str, username: str) -> User:
    uid = ukey(guild_id, discord_id)
    user = db.query(User).get(uid)
    if not user:
        start = get_starting_balance(db, guild_id)
        user = User(id=uid, guild_id=str(guild_id), discord_id=str(discord_id), username=username, balance=start)
        db.add(user)
        log(db, str(guild_id), "system", f"{username} wurde neu angelegt (Startguthaben {start} ₡)")
        db.commit()
    user.last_seen = datetime.now(timezone.utc)
    db.commit()
    return user


def sync_admin_role(db, guild: "discord.Guild | None", user: User):
    """Gleicht user.role automatisch mit der aktuellen Discord-Situation ab:
    - Der Server-Owner bekommt immer automatisch 'Owner'.
    - Mitglieder mit der unter Einstellungen hinterlegten Admin-Rolle
      ('admin_rolle', eine Discord-Rollen-ID) bekommen automatisch 'Admin'.
    - Verliert ein Nutzer diese Rolle wieder (und ist nicht Owner), wird die
      Dashboard-Rolle automatisch zurück auf 'Member' gesetzt.
    Läuft ohne Wirkung, falls der Server oder das Mitglied gerade nicht über
    den Bot-Cache erreichbar ist (z.B. Bot kurzzeitig offline)."""
    if not guild:
        return
    member = guild.get_member(int(user.discord_id))
    if not member:
        return

    if member.id == guild.owner_id:
        if user.role != "Owner":
            user.role = "Owner"
        return

    admin_role_id = get_setting_value(db, str(guild.id), "admin_rolle")
    has_admin_role = False
    if admin_role_id:
        try:
            has_admin_role = any(str(r.id) == str(admin_role_id) for r in member.roles)
        except (TypeError, ValueError):
            has_admin_role = False

    if has_admin_role:
        if user.role != "Admin":
            user.role = "Admin"
    elif user.role == "Admin":
        # Admin-Rolle wurde in Discord entzogen -> Dashboard-Rolle zurücksetzen
        user.role = "Mitglied"


def get_or_create_user(db, member: discord.Member) -> User:
    user = get_or_create_user_by_id(db, str(member.guild.id), str(member.id), member.display_name)
    sync_admin_role(db, member.guild, user)
    db.commit()
    return user


def log(db: Session, guild_id: str, type_: str, text: str):
    db.add(LogEntry(guild_id=str(guild_id), type=type_, text=text))
    _post_log_to_channel(guild_id, type_, text)


def _post_log_to_channel(guild_id, type_: str, text: str):
    """Postet den Log-Eintrag zusätzlich in den Log-Kanal, falls einer eingestellt ist.
    Funktioniert sicher sowohl aus normalen als auch aus asynchronen Aufrufen heraus."""
    try:
        if not bot.is_ready() or not MAIN_LOOP or not MAIN_LOOP.is_running():
            return
        db2 = SessionLocal()
        try:
            channel_id = get_setting_value(db2, guild_id, "log_kanal")
            banner_url = get_setting_value(db2, guild_id, "embed_banner_url")
        finally:
            db2.close()
        if not channel_id:
            return

        async def _send():
            try:
                channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
                icon, color = LOG_TYPE_STYLE.get(type_, ("📋", COLOR_LOG))
                embed = discord.Embed(description=text, color=color, timestamp=datetime.now(timezone.utc))
                embed.set_author(name=f"{icon} {type_.capitalize()}")
                if banner_url:
                    embed.set_image(url=banner_url)
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Log-Kanal-Post fehlgeschlagen: {e}")

        asyncio.run_coroutine_threadsafe(_send(), MAIN_LOOP)
    except Exception as e:
        print(f"Log-Kanal-Vorbereitung fehlgeschlagen: {e}")


@bot.event
async def on_ready():
    print(f"Bot eingeloggt als {bot.user}")
    try:
        # Einmalig aufräumen: zuvor global bei Discord angemeldete Befehle entfernen,
        # damit sie sich nicht mit den Server-spezifischen Befehlen doppeln.
        try:
            await bot.http.bulk_upsert_global_commands(bot.application_id, payload=[])
        except Exception as cleanup_err:
            print(f"Aufräumen der globalen Befehle fehlgeschlagen (kein Problem): {cleanup_err}")

        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"{len(synced)} Slash-Commands sofort auf '{guild.name}' synchronisiert")

        if not check_expired_giveaways.is_running():
            check_expired_giveaways.start()
        if not apply_daily_interest.is_running():
            apply_daily_interest.start()
        if not auto_end_duty.is_running():
            auto_end_duty.start()
    except Exception as e:
        print(f"Fehler beim Synchronisieren der Slash-Commands: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    """Neues Mitglied bekommt automatisch das eingestellte Startguthaben + Willkommensnachricht."""
    db = SessionLocal()
    try:
        user = get_or_create_user(db, member)
        guild_id = str(member.guild.id)

        channel = None
        channel_id = get_setting_value(db, guild_id, "willkommen_kanal")
        if channel_id:
            channel = member.guild.get_channel(int(channel_id))
        if channel is None:
            channel = member.guild.system_channel
        if channel is None:
            for ch in member.guild.text_channels:
                perms = ch.permissions_for(member.guild.me)
                if perms.send_messages:
                    channel = ch
                    break

        if channel:
            try:
                template = get_setting_value(
                    db, guild_id, "willkommen_text",
                    default="Schön, dass du da bist, {user}! Du hast ein Startguthaben von **{balance} ₡** erhalten.",
                )
                text = (
                    template.replace("{user}", member.mention)
                    .replace("{server}", member.guild.name)
                    .replace("{balance}", f"{user.balance:,}".replace(",", "."))
                    .replace("{mitgliederzahl}", str(member.guild.member_count))
                )
                embed = discord.Embed(
                    title=f"👋 Willkommen auf {member.guild.name}!", description=text,
                    color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.add_field(name="💰 Startguthaben", value=f"{user.balance:,} ₡".replace(",", "."), inline=True)
                embed.add_field(name="👥 Mitgliederzahl", value=f"#{member.guild.member_count}", inline=True)
                apply_brand(embed, db, member.guild)
                banner_url = get_setting_value(db, guild_id, "welcome_banner_url")
                if banner_url:
                    embed.set_image(url=banner_url)  # eigenes Willkommens-Banner hat Vorrang vor dem generischen
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Konnte Willkommensnachricht nicht senden: {e}")
    finally:
        db.close()


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Sobald der Bot einem neuen Server beitritt, sofort die Slash-Commands dort anmelden.
    Der neue Server bekommt automatisch ein eigenes, leeres Dashboard - es werden
    keinerlei Daten von anderen Servern übernommen, da alles über die guild_id
    getrennt gespeichert wird."""
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"{len(synced)} Slash-Commands auf neuem Server '{guild.name}' synchronisiert")
    except Exception as e:
        print(f"Fehler beim Synchronisieren auf neuem Server: {e}")


def format_afk_duration(since: datetime) -> str:
    since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
    delta = datetime.now(timezone.utc) - since
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "gerade eben"
    if minutes < 60:
        return f"vor {minutes} Minute(n)"
    hours = minutes // 60
    return f"vor {hours} Stunde(n)"


GIVEAWAY_EMOJI = "🎉"


async def draw_giveaway_winner(db, giveaway: Giveaway):
    ids = [i for i in (giveaway.participants or "").split(",") if i.strip()]
    winner_discord_id = random.choice(ids) if ids else None
    winner_name = None
    if winner_discord_id:
        winner_user = db.query(User).get(ukey(giveaway.guild_id, winner_discord_id))
        winner_name = winner_user.username if winner_user else winner_discord_id
    giveaway.status = "beendet"
    giveaway.winner = winner_name
    log(db, giveaway.guild_id, "system", f"Giveaway '{giveaway.prize}' beendet — Gewinner: {winner_name or 'niemand teilgenommen'}")
    db.commit()

    if giveaway.channel_id and bot.is_ready():
        try:
            channel = bot.get_channel(int(giveaway.channel_id)) or await bot.fetch_channel(int(giveaway.channel_id))
            if winner_discord_id:
                embed = discord.Embed(
                    title="🏆 Giveaway beendet!",
                    description=f"### 🎁 {giveaway.prize}\n\nHerzlichen Glückwunsch <@{winner_discord_id}>! 🎉",
                    color=COLOR_SUCCESS, timestamp=datetime.now(timezone.utc),
                )
            else:
                embed = discord.Embed(
                    title="🏆 Giveaway beendet!",
                    description=f"### 🎁 {giveaway.prize}\n\nLeider hat niemand teilgenommen.",
                    color=COLOR_LOG, timestamp=datetime.now(timezone.utc),
                )
            apply_brand(embed, db, channel.guild)
            await channel.send(embed=embed)
        except Exception as e:
            print(f"Konnte Giveaway-Ergebnis nicht senden: {e}")


@tasks.loop(minutes=1)
async def auto_end_duty():
    """Beendet automatisch den Dienst von Nutzern, die länger als die pro
    Server eingestellte Zeit ('dienst_auto_ende', in Minuten) im
    Dienst sind. Nutzt dieselbe Logik wie /dienst (inkl. Auszahlung der
    geleisteten Stunden) und postet das Ergebnis in den Dienst-Kanal."""
    db = SessionLocal()
    try:
        for guild in bot.guilds:
            guild_id = str(guild.id)
            limit_raw = get_setting_value(db, guild_id, "dienst_auto_ende")
            if not limit_raw:
                continue
            try:
                limit_minutes = float(limit_raw)
            except (TypeError, ValueError):
                continue
            if limit_minutes <= 0:
                continue

            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=limit_minutes)
            users_on_duty = db.query(User).filter(
                User.guild_id == guild_id, User.on_duty_fraction.isnot(None)
            ).all()
            for u in users_on_duty:
                started = u.duty_started_at
                if not started:
                    continue
                started = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started
                if started > cutoff:
                    continue  # noch innerhalb der erlaubten Zeit

                fraction_name = u.on_duty_fraction
                try:
                    f, status, paid = toggle_duty_for_user(db, guild_id, u, fraction_name)
                except ValueError as e:
                    print(f"Automatisches Dienstende für {u.username} fehlgeschlagen: {e}")
                    continue
                log(db, guild_id, "dienst", f"{u.username} wurde nach {int(limit_minutes)} Minuten automatisch außer Dienst gesetzt")
                await post_duty_embed(f, "Automatisches Dienstende")
    finally:
        db.close()


@tasks.loop(seconds=30)
async def check_expired_giveaways():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        expired = db.query(Giveaway).filter(Giveaway.status == "aktiv", Giveaway.ends_at <= now).all()
        for g in expired:
            await draw_giveaway_winner(db, g)
    finally:
        db.close()


@tasks.loop(hours=1)
async def apply_daily_interest():
    """Zahlt einmal pro Tag pro Server Zinsen auf alle Bankguthaben aus,
    falls unter Einstellungen ein Zinssatz (%) hinterlegt ist. Läuft
    stündlich, wendet die Zinsen aber pro Server nur einmal pro Tag an
    (unabhängig davon, wie oft der Bot zwischendurch neu startet)."""
    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        for guild in bot.guilds:
            guild_id = str(guild.id)
            rate = get_setting_value(db, guild_id, "zinssatz_taeglich")
            if not rate:
                continue
            try:
                rate_val = float(rate)
            except (TypeError, ValueError):
                continue
            if rate_val <= 0:
                continue

            last_applied = get_setting_value(db, guild_id, "_zinsen_zuletzt")
            if last_applied == today:
                continue  # heute schon ausgezahlt

            users = db.query(User).filter(User.guild_id == guild_id).all()
            for u in users:
                if u.balance <= 0:
                    continue
                zins = round(u.balance * rate_val / 100)
                if zins > 0:
                    u.balance += zins
                    db.add(Transaction(guild_id=guild_id, from_user="Zinsen", to_user=u.username, amount=zins, type="Zinsen"))

            setting = db.query(Setting).get(gkey(guild_id, "_zinsen_zuletzt"))
            if setting:
                setting.value = today
            else:
                db.add(Setting(key=gkey(guild_id, "_zinsen_zuletzt"), value=today))
            log(db, guild_id, "bank", f"Tägliche Zinsen ({rate_val}%) an alle Nutzer ausgezahlt")
            db.commit()
    finally:
        db.close()


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != GIVEAWAY_EMOJI or payload.user_id == bot.user.id:
        return
    db = SessionLocal()
    try:
        g = db.query(Giveaway).filter(Giveaway.message_id == str(payload.message_id), Giveaway.status == "aktiv").first()
        if not g:
            return
        ids = [i for i in (g.participants or "").split(",") if i.strip()]
        if str(payload.user_id) not in ids:
            ids.append(str(payload.user_id))
            g.participants = ",".join(ids)
            g.entries = len(ids)
            db.commit()
    finally:
        db.close()


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if str(payload.emoji) != GIVEAWAY_EMOJI:
        return
    db = SessionLocal()
    try:
        g = db.query(Giveaway).filter(Giveaway.message_id == str(payload.message_id), Giveaway.status == "aktiv").first()
        if not g:
            return
        ids = [i for i in (g.participants or "").split(",") if i.strip()]
        if str(payload.user_id) in ids:
            ids.remove(str(payload.user_id))
            g.participants = ",".join(ids)
            g.entries = len(ids)
            db.commit()
    finally:
        db.close()


@bot.tree.command(name="giveaway_erstellen", description="[Admin] Startet ein Giveaway")
@app_commands.describe(preis="Was verlost wird", dauer_minuten="Wie lange das Giveaway läuft (in Minuten)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_create_cmd(interaction: discord.Interaction, preis: str, dauer_minuten: int):
    if dauer_minuten <= 0:
        return await interaction.response.send_message("Die Dauer muss positiv sein.", ephemeral=True)
    db_check = SessionLocal()
    try:
        if not is_module_enabled(db_check, interaction.guild_id, "giveaways"):
            return await module_disabled_reply(interaction, "giveaways")
    finally:
        db_check.close()
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=dauer_minuten)
    embed = discord.Embed(
        title="🎉 Giveaway gestartet!",
        description=f"### 🏆 {preis}\n\nReagiere mit {GIVEAWAY_EMOJI}, um teilzunehmen!",
        color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="⏰ Endet", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.add_field(name="🎁 Gestartet von", value=interaction.user.mention, inline=True)
    embed.set_footer(text="Viel Glück!")
    db_brand = SessionLocal()
    try:
        apply_brand(embed, db_brand, interaction.guild)
    finally:
        db_brand.close()
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    await message.add_reaction(GIVEAWAY_EMOJI)

    db = SessionLocal()
    try:
        g = Giveaway(guild_id=str(interaction.guild_id), prize=preis, status="aktiv", ends_at=ends_at,
                     channel_id=str(interaction.channel.id), message_id=str(message.id), participants="")
        db.add(g)
        log(db, str(interaction.guild_id), "system", f"{interaction.user.display_name} hat ein Giveaway gestartet: {preis}")
        db.commit()
    finally:
        db.close()


@bot.tree.command(name="giveaway_beenden", description="[Admin] Beendet ein Giveaway sofort und lost aus")
@app_commands.describe(giveaway_id="Die ID des Giveaways (siehe Dashboard)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_end_cmd(interaction: discord.Interaction, giveaway_id: int):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "giveaways"):
            return await module_disabled_reply(interaction, "giveaways")
        g = db.query(Giveaway).filter(Giveaway.id == giveaway_id, Giveaway.guild_id == str(interaction.guild_id)).first()
        if not g or g.status != "aktiv":
            return await interaction.response.send_message("Giveaway nicht gefunden oder schon beendet.", ephemeral=True)
        await draw_giveaway_winner(db, g)
        await interaction.response.send_message(f"✅ Giveaway **{g.prize}** wurde beendet.")
    finally:
        db.close()


@bot.tree.command(name="giveaway_neu_auslosen", description="[Admin] Lost einen neuen Gewinner für ein beendetes Giveaway aus")
@app_commands.describe(giveaway_id="Die ID des Giveaways (siehe Dashboard)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_reroll_cmd(interaction: discord.Interaction, giveaway_id: int):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "giveaways"):
            return await module_disabled_reply(interaction, "giveaways")
        g = db.query(Giveaway).filter(Giveaway.id == giveaway_id, Giveaway.guild_id == str(interaction.guild_id)).first()
        if not g:
            return await interaction.response.send_message("Giveaway nicht gefunden.", ephemeral=True)
        await draw_giveaway_winner(db, g)
        await interaction.response.send_message(f"🔁 Neuer Gewinner für **{g.prize}** wurde ausgelost.")
    finally:
        db.close()


@giveaway_create_cmd.error
@giveaway_end_cmd.error
@giveaway_reroll_cmd.error
async def giveaway_cmd_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Dafür brauchst du Administrator-Rechte auf diesem Server.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Etwas ist schiefgelaufen.", ephemeral=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    guild_id = str(message.guild.id)

    db = SessionLocal()
    try:
        # Eigenes AFK entfernen, sobald man wieder schreibt
        me = db.query(User).get(ukey(guild_id, message.author.id))
        if me and me.afk_reason:
            me.afk_reason = None
            me.afk_since = None
            log(db, guild_id, "afk", f"{me.username} ist nicht mehr AFK (automatisch erkannt)")
            db.commit()
            try:
                await message.channel.send(f"👋 Willkommen zurück, {message.author.mention}! Dein AFK-Status wurde entfernt.")
            except Exception:
                pass

        # Erwähnte Mitglieder, die AFK sind, melden
        for mentioned in message.mentions:
            if mentioned.bot or mentioned.id == message.author.id:
                continue
            target = db.query(User).get(ukey(guild_id, mentioned.id))
            if target and target.afk_reason:
                dauer = format_afk_duration(target.afk_since) if target.afk_since else ""
                try:
                    await message.channel.send(
                        f"💤 {mentioned.display_name} ist gerade AFK ({dauer}): {target.afk_reason}"
                    )
                except Exception:
                    pass
    finally:
        db.close()


@bot.tree.command(name="afk", description="Setzt dich auf AFK, bis du wieder schreibst")
@app_commands.describe(grund="Warum du AFK bist (optional)")
async def afk_cmd(interaction: discord.Interaction, grund: str = "Kein Grund angegeben"):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "afk"):
            return await module_disabled_reply(interaction, "afk")
        user = get_or_create_user(db, interaction.user)
        user.afk_reason = grund
        user.afk_since = datetime.now(timezone.utc)
        log(db, str(interaction.guild_id), "afk", f"{user.username} ist jetzt AFK: {grund}")
        db.commit()
        await interaction.response.send_message(f"😴 {interaction.user.mention} ist jetzt AFK: {grund}")
    finally:
        db.close()


@bot.tree.command(name="kontostand", description="Zeigt deinen aktuellen Kontostand")
async def balance_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "bank"):
            return await module_disabled_reply(interaction, "bank")
        user = get_or_create_user(db, interaction.user)
        await interaction.response.send_message(
            f"💰 Kontostand: **{user.balance:,} ₡**\n💵 Bargeld: **{user.cash:,} ₡**".replace(",", ".")
        )
    finally:
        db.close()


@bot.tree.command(name="einzahlen", description="Zahlt Bargeld auf dein Bankkonto ein")
@app_commands.describe(betrag="Wie viel Bargeld eingezahlt werden soll")
async def deposit_cmd(interaction: discord.Interaction, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "bank"):
            return await module_disabled_reply(interaction, "bank")
        user = get_or_create_user(db, interaction.user)
        if user.cash < betrag:
            return await interaction.response.send_message("❌ Nicht genug Bargeld.", ephemeral=True)
        user.cash -= betrag
        user.balance += betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=user.username, to_user=user.username, amount=betrag, type="Einzahlung"))
        log(db, str(interaction.guild_id), "bank", f"{user.username} hat {betrag} ₡ eingezahlt")
        db.commit()
        await interaction.response.send_message(f"✅ {betrag:,} ₡ eingezahlt. Neues Bankguthaben: **{user.balance:,} ₡**".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="auszahlen", description="Hebt Guthaben von deinem Bankkonto als Bargeld ab")
@app_commands.describe(betrag="Wie viel Guthaben abgehoben werden soll")
async def withdraw_cmd(interaction: discord.Interaction, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "bank"):
            return await module_disabled_reply(interaction, "bank")
        user = get_or_create_user(db, interaction.user)
        if user.balance < betrag:
            return await interaction.response.send_message("❌ Nicht genug Bankguthaben.", ephemeral=True)
        user.balance -= betrag
        user.cash += betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=user.username, to_user=user.username, amount=betrag, type="Auszahlung"))
        log(db, str(interaction.guild_id), "bank", f"{user.username} hat {betrag} ₡ abgehoben")
        db.commit()
        await interaction.response.send_message(f"✅ {betrag:,} ₡ abgehoben. Neues Bargeld: **{user.cash:,} ₡**".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="ueberweisen", description="Überweist Guthaben an ein anderes Mitglied")
@app_commands.describe(empfaenger="An wen überwiesen werden soll", betrag="Wie viel überwiesen werden soll")
async def transfer_cmd(interaction: discord.Interaction, empfaenger: discord.Member, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "bank"):
            return await module_disabled_reply(interaction, "bank")
        max_transfer = get_setting_value(db, interaction.guild_id, "max_ueberweisung")
        if max_transfer and betrag > int(max_transfer):
            return await interaction.response.send_message(f"❌ Maximal erlaubter Betrag: {int(max_transfer):,} ₡".replace(",", "."), ephemeral=True)
        sender = get_or_create_user(db, interaction.user)
        receiver = get_or_create_user(db, empfaenger)
        if sender.balance < betrag:
            return await interaction.response.send_message("❌ Nicht genug Guthaben.", ephemeral=True)
        sender.balance -= betrag
        receiver.balance += betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=sender.username, to_user=receiver.username, amount=betrag, type="Überweisung"))
        log(db, str(interaction.guild_id), "bank", f"{sender.username} überwies {betrag} ₡ an {receiver.username}")
        db.commit()
        await interaction.response.send_message(f"✅ {betrag:,} ₡ an {empfaenger.mention} überwiesen.".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="shop", description="Zeigt alle Artikel im Shop")
async def shop_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "shop"):
            return await module_disabled_reply(interaction, "shop")
        items = db.query(ShopItem).filter(ShopItem.guild_id == str(interaction.guild_id)).all()
        if not items:
            return await interaction.response.send_message("Der Shop ist noch leer.")

        embed = discord.Embed(
            title="🛒 Shop", description=f"**{len(items)}** Artikel verfügbar — kauf mit `/kaufen item_id:<ID>`",
            color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
        )
        for i in items:
            preis = f"{i.price:,} ₡".replace(",", ".")
            embed.add_field(name=f"`{i.id:04d}` · {i.name}", value=f"💰 {preis}", inline=True)
        apply_brand(embed, db, interaction.guild)

        await interaction.response.send_message(embed=embed)
    finally:
        db.close()


@bot.tree.command(name="kaufen", description="Kauft einen Artikel aus dem Shop")
@app_commands.describe(item_id="Die Artikel-ID aus /shop (z.B. 4)")
async def complete_purchase(interaction: discord.Interaction, item_id: int, edit: bool = False):
    db = SessionLocal()
    try:
        if not is_module_enabled(db, interaction.guild_id, "shop"):
            return await module_disabled_reply(interaction, "shop")
        item = db.query(ShopItem).get(item_id)
        if not item:
            embed = discord.Embed(description="❌ Artikel nicht mehr verfügbar.", color=COLOR_DANGER)
            return await (interaction.response.edit_message(content=None, embed=embed, view=None) if edit else interaction.response.send_message(embed=embed, ephemeral=True))
        user = get_or_create_user(db, interaction.user)
        if user.balance < item.price:
            embed = discord.Embed(description="❌ Nicht genug Guthaben.", color=COLOR_DANGER)
            return await (interaction.response.edit_message(content=None, embed=embed, view=None) if edit else interaction.response.send_message(embed=embed, ephemeral=True))

        if not edit:
            require_confirm = get_setting_value(db, str(interaction.guild_id), "shop_kaufbestaetigung")
            if require_confirm and require_confirm.strip().lower() in ("ja", "yes", "true", "1"):
                preis_text = f"{item.price:,} ₡".replace(",", ".")
                embed = discord.Embed(
                    title="🛍️ Kauf bestätigen",
                    description=f"### {item.name}\nMöchtest du diesen Artikel wirklich kaufen?",
                    color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="💰 Preis", value=preis_text, inline=True)
                embed.add_field(name="👛 Dein Guthaben danach", value=f"{(user.balance - item.price):,} ₡".replace(",", "."), inline=True)
                apply_brand(embed, db, interaction.guild)
                view = PurchaseConfirmView(item.id, interaction.user.id)
                return await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        user.balance -= item.price
        item.sold += 1
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=user.username, to_user="Shop", amount=item.price, type="Kauf"))
        log(db, str(interaction.guild_id), "shop", f"{user.username} kaufte '{item.name}'")
        db.commit()

        role_note = ""
        if item.role_id:
            try:
                role = interaction.guild.get_role(int(item.role_id))
                if role:
                    await interaction.user.add_roles(role)
                    role_note = f"\n🎭 Rolle **{role.name}** wurde dir vergeben."
            except Exception as e:
                role_note = "\n⚠️ Rolle konnte nicht vergeben werden — Bot-Berechtigungen prüfen."
                print(f"Rollenvergabe fehlgeschlagen: {e}")

        embed = discord.Embed(
            title="✅ Kauf erfolgreich", description=f"Du hast **{item.name}** gekauft.{role_note}",
            color=COLOR_SUCCESS, timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👛 Neues Guthaben", value=f"{user.balance:,} ₡".replace(",", "."), inline=True)
        apply_brand(embed, db, interaction.guild)
        if edit:
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed)
    finally:
        db.close()


class PurchaseConfirmView(discord.ui.View):
    def __init__(self, item_id: int, buyer_id: int):
        super().__init__(timeout=30)
        self.item_id = item_id
        self.buyer_id = buyer_id

    @discord.ui.button(label="Ja, kaufen", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            return await interaction.response.send_message("Das ist nicht dein Kauf.", ephemeral=True)
        await complete_purchase(interaction, self.item_id, edit=True)
        self.stop()

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            return await interaction.response.send_message("Das ist nicht dein Kauf.", ephemeral=True)
        await interaction.response.edit_message(content="❌ Kauf abgebrochen.", embed=None, view=None)
        self.stop()


WORK_COOLDOWN = timedelta(hours=1)
DAILY_COOLDOWN = timedelta(hours=24)
DAILY_AMOUNT = 250


@bot.tree.command(name="work", description="Arbeite bei einem Job und verdiene Guthaben")
@app_commands.describe(job="Bei welchem Job du arbeitest")
async def work_cmd(
    interaction: discord.Interaction,
    job: Literal["Polizei", "Feuerwehr", "Notfallsanitäter", "Rettungsdienst", "LKW", "Bus"],
):
    db = SessionLocal()
    try:
        user = get_or_create_user(db, interaction.user)
        now = datetime.now(timezone.utc)
        last = user.last_work.replace(tzinfo=timezone.utc) if user.last_work else None
        if last and now - last < WORK_COOLDOWN:
            remaining = WORK_COOLDOWN - (now - last)
            minuten = int(remaining.total_seconds() // 60) + 1
            return await interaction.response.send_message(
                f"⏳ Du musst noch **{minuten} Minute(n)** warten, bevor du wieder arbeiten kannst.", ephemeral=True
            )
        verdienst = random.randint(100, 400)
        user.balance += verdienst
        user.last_work = now
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=f"Job:{job}", to_user=user.username, amount=verdienst, type="Arbeit"))
        log(db, str(interaction.guild_id), "system", f"{user.username} hat als {job} gearbeitet und {verdienst} ₡ verdient")
        db.commit()
        await interaction.response.send_message(f"💼 Du hast als **{job}** gearbeitet und **{verdienst} ₡** verdient!")
    finally:
        db.close()


@bot.tree.command(name="daily", description="Hole dir dein tägliches Guthaben ab")
async def daily_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = get_or_create_user(db, interaction.user)
        now = datetime.now(timezone.utc)
        last = user.last_daily.replace(tzinfo=timezone.utc) if user.last_daily else None
        if last and now - last < DAILY_COOLDOWN:
            remaining = DAILY_COOLDOWN - (now - last)
            hours = int(remaining.total_seconds() // 3600)
            minutes = int((remaining.total_seconds() % 3600) // 60)
            return await interaction.response.send_message(
                f"⏳ Dein tägliches Guthaben ist schon abgeholt. Nochmal in **{hours}h {minutes}min** möglich.", ephemeral=True
            )
        user.balance += DAILY_AMOUNT
        user.last_daily = now
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user="Täglicher Bonus", to_user=user.username, amount=DAILY_AMOUNT, type="Daily"))
        log(db, str(interaction.guild_id), "system", f"{user.username} hat den täglichen Bonus abgeholt")
        db.commit()
        await interaction.response.send_message(f"🎁 Du hast deinen täglichen Bonus von **{DAILY_AMOUNT} ₡** abgeholt!")
    finally:
        db.close()


@bot.tree.command(name="geld_geben", description="[Admin] Gibt einem Mitglied Guthaben")
@app_commands.describe(mitglied="Wer das Guthaben bekommt", betrag="Wie viel Guthaben")
@app_commands.checks.has_permissions(administrator=True)
async def give_money_cmd(interaction: discord.Interaction, mitglied: discord.Member, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        target = get_or_create_user(db, mitglied)
        target.balance += betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=f"Admin:{interaction.user.display_name}", to_user=target.username, amount=betrag, type="Admin-Gutschrift"))
        log(db, str(interaction.guild_id), "system", f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ gegeben")
        db.commit()
        await interaction.response.send_message(
            f"✅ {mitglied.mention} hat **{betrag:,} ₡** erhalten. Neuer Kontostand: **{target.balance:,} ₡**".replace(",", ".")
        )
    finally:
        db.close()


@bot.tree.command(name="geld_abziehen", description="[Admin] Zieht einem Mitglied Guthaben ab")
@app_commands.describe(mitglied="Wem Guthaben abgezogen wird", betrag="Wie viel Guthaben")
@app_commands.checks.has_permissions(administrator=True)
async def remove_money_cmd(interaction: discord.Interaction, mitglied: discord.Member, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        target = get_or_create_user(db, mitglied)
        target.balance -= betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=target.username, to_user=f"Admin:{interaction.user.display_name}", amount=betrag, type="Admin-Abzug"))
        log(db, str(interaction.guild_id), "system", f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ abgezogen")
        db.commit()
        await interaction.response.send_message(
            f"✅ {mitglied.mention} wurden **{betrag:,} ₡** abgezogen. Neuer Kontostand: **{target.balance:,} ₡**".replace(",", ".")
        )
    finally:
        db.close()


@bot.tree.command(name="kontostand_ansehen", description="[Admin] Zeigt den Kontostand eines Mitglieds")
@app_commands.describe(mitglied="Wessen Kontostand angezeigt werden soll")
@app_commands.checks.has_permissions(administrator=True)
async def view_balance_cmd(interaction: discord.Interaction, mitglied: discord.Member):
    db = SessionLocal()
    try:
        target = get_or_create_user(db, mitglied)
        await interaction.response.send_message(
            f"💰 {mitglied.mention}\n"
            f"Kontostand: **{target.balance:,} ₡**\n"
            f"Bargeld: **{target.cash:,} ₡**".replace(",", "."),
            ephemeral=True,
        )
    finally:
        db.close()


@give_money_cmd.error
@remove_money_cmd.error
@view_balance_cmd.error
async def admin_money_cmd_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Dafür brauchst du Administrator-Rechte auf diesem Server.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Etwas ist schiefgelaufen.", ephemeral=True)


async def post_duty_embed(fraction: DutyFraction, changed_by: str):
    """Postet eine Embed-Nachricht in den Dienst-Kanal der Fraktion, falls einer hinterlegt ist."""
    if not fraction.channel_id or not bot.is_ready():
        return
    try:
        channel = bot.get_channel(int(fraction.channel_id)) or await bot.fetch_channel(int(fraction.channel_id))
        on_duty_now = fraction.on_duty > 0
        embed = discord.Embed(
            title=f"🚔 {fraction.name}",
            description=f"{'🟢 **Im Dienst**' if on_duty_now else '⚪ **Außer Dienst**'} — geändert von {changed_by}",
            color=COLOR_SUCCESS if on_duty_now else COLOR_LOG,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="👥 Aktuell im Dienst", value=f"{fraction.on_duty} / {fraction.total}", inline=True)
        embed.add_field(name="⏱️ Stunden heute", value=f"{fraction.hours_today:.1f} h", inline=True)
        db_brand = SessionLocal()
        try:
            apply_brand(embed, db_brand, channel.guild)
        finally:
            db_brand.close()
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Konnte Dienst-Embed nicht senden: {e}")


def toggle_duty_for_user(db, guild_id: str, user: User, fraction_name: str):
    """Schaltet den Dienststatus eines Nutzers um. Zählt beim Abtreten die
    tatsächlich geleistete Zeit zur Fraktion dazu und zahlt (falls in den
    Einstellungen eine Vergütung/Stunde hinterlegt ist) automatisch aus.
    Gibt (fraction, status_text, ausgezahlter_betrag) zurück oder wirft ValueError."""
    if user.on_duty_fraction and user.on_duty_fraction.lower() != fraction_name.lower():
        raise ValueError(f"Du bist gerade bei **{user.on_duty_fraction}** im Dienst. Geh dort zuerst außer Dienst.")

    f = db.query(DutyFraction).filter(DutyFraction.guild_id == guild_id, DutyFraction.name.ilike(fraction_name)).first()
    if not f:
        f = DutyFraction(guild_id=guild_id, name=fraction_name, total=10)
        db.add(f)
        db.commit()

    paid = 0
    if user.on_duty_fraction:
        # Abtreten: geleistete Zeit berechnen und gutschreiben
        started = user.duty_started_at
        if started:
            started = started.replace(tzinfo=timezone.utc) if started.tzinfo is None else started
            hours = max(0.0, (datetime.now(timezone.utc) - started).total_seconds() / 3600)
            f.hours_today = round((f.hours_today or 0) + hours, 2)
            rate = get_setting_value(db, guild_id, "dienst_verguetung")
            if rate:
                paid = round(float(rate) * hours)
                if paid > 0:
                    user.balance += paid
                    db.add(Transaction(guild_id=guild_id, from_user="Dienstlohn", to_user=user.username, amount=paid, type="Dienstlohn"))
        user.on_duty_fraction = None
        user.duty_started_at = None
        f.on_duty = max(0, f.on_duty - 1)
        status = "außer Dienst"
    else:
        if f.total and f.on_duty >= f.total:
            raise ValueError(f"Bei **{f.name}** sind schon alle Plätze belegt ({f.on_duty}/{f.total}).")
        user.on_duty_fraction = fraction_name
        user.duty_started_at = datetime.now(timezone.utc)
        f.on_duty += 1
        status = "im Dienst"

    log(db, guild_id, "dienst", f"{user.username} ist jetzt {status} bei {f.name}")
    db.commit()
    return f, status, paid


@bot.tree.command(name="dienst", description="Dienst antreten oder abtreten für eine Fraktion")
@app_commands.describe(fraktion="Welche Fraktion")
async def duty_cmd(
    interaction: discord.Interaction,
    fraktion: Literal["Polizei", "Feuerwehr", "Notfallsanitäter", "Rettungsdienst", "LKW", "Bus"],
):
    db = SessionLocal()
    try:
        guild_id = str(interaction.guild_id)
        if not is_module_enabled(db, guild_id, "dienst"):
            return await module_disabled_reply(interaction, "dienst")
        user = get_or_create_user(db, interaction.user)
        try:
            f, status, paid = toggle_duty_for_user(db, guild_id, user, fraktion)
        except ValueError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        pay_note = f" Du hast **{paid:,} ₡** Dienstlohn erhalten.".replace(",", ".") if paid else ""
        await interaction.response.send_message(f"👮 {f.name}: **{status}** ({f.on_duty}/{f.total}).{pay_note}")
        await post_duty_embed(f, interaction.user.display_name)
    finally:
        db.close()


# ---------- Ticket-Karte (Bild statt Text-Box) ----------
_TICKET_CARD_BASE_CACHE: dict[str, "Image.Image"] = {}


def _load_font(paths: list, size: int) -> "ImageFont.FreeTypeFont":
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


_FONT_TITLE_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
_FONT_TEXT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]


async def get_ticket_card_base(url: str) -> "Image.Image | None":
    """Lädt die Grundplatte-Bilddatei (per URL) und cacht sie im Arbeitsspeicher,
    damit sie nicht bei jedem einzelnen Ticket erneut heruntergeladen wird."""
    if url in _TICKET_CARD_BASE_CACHE:
        return _TICKET_CARD_BASE_CACHE[url].copy()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        _TICKET_CARD_BASE_CACHE[url] = img
        return img.copy()
    except Exception as e:
        print(f"Konnte Ticket-Karten-Grundplatte nicht laden: {e}")
        return None


def draw_row_icon(draw: "ImageDraw.ImageDraw", cx: float, cy: float, r: float, label: str, color: tuple):
    """Zeichnet ein kleines, zum Zeileninhalt passendes Icon (Uhr, Person, Tag,
    Kategorie-Raute) frei mit einfachen Formen - kein Emoji-Font nötig, läuft
    also überall zuverlässig, egal welche Schriftarten auf dem Server verfügbar sind."""
    l = label.lower()
    if "erstellt" in l or "zeit" in l or "datum" in l:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=2)
        draw.line((cx, cy, cx, cy - r * 0.6), fill=color, width=2)
        draw.line((cx, cy, cx + r * 0.5, cy + r * 0.2), fill=color, width=2)
    elif "nutzer" in l or "user" in l or "geschlossen" in l or "team" in l:
        draw.ellipse((cx - r * 0.5, cy - r, cx + r * 0.5, cy - r * 0.1), fill=color)
        draw.pieslice((cx - r, cy - r * 0.1, cx + r, cy + r * 1.6), 180, 360, fill=color)
    elif "case" in l or "id" in l:
        draw.rounded_rectangle((cx - r, cy - r * 0.8, cx + r, cy + r * 0.8), radius=r * 0.25, outline=color, width=2)
        draw.line((cx - r * 0.5, cy, cx + r * 0.5, cy), fill=color, width=2)
        draw.line((cx - r * 0.5, cy + r * 0.4, cx + r * 0.2, cy + r * 0.4), fill=color, width=2)
    elif "kategorie" in l:
        draw.polygon([(cx - r, cy), (cx, cy - r), (cx + r, cy), (cx, cy + r)], outline=color, width=2)
        draw.ellipse((cx - r * 0.15, cy - r * 0.15, cx + r * 0.15, cy + r * 0.15), fill=color)
    else:
        draw.ellipse((cx - r * 0.4, cy - r * 0.4, cx + r * 0.4, cy + r * 0.4), fill=color)


def draw_ticket_card(base: "Image.Image", title: str, intro: str, rows: list[tuple[str, str]], accent: tuple = (114, 137, 218), eyebrow: str = None) -> io.BytesIO:
    """Zeichnet optional ein kleines Kategorie-Label, Titel, Icon-Badge,
    Einleitungstext und kompakte Info-'Chips' (mit kleinem gezeichnetem Icon,
    Label, Wert) direkt auf die Grundplatte. Gibt ein fertiges PNG als
    BytesIO zurück. Skaliert Zeilenhöhe/Abstände automatisch runter, falls
    viele Zeilen sonst über den unteren Rand der Grundplatte hinausragen würden."""
    base = base.copy()
    w, h = base.size
    accent_rgba = (*accent, 255)

    TEXT_MAIN = (255, 255, 255, 255)
    TEXT_SUB = (190, 192, 200, 255)
    TEXT_LABEL = (155, 157, 170, 255)
    TEXT_EYEBROW = (170, 172, 185, 255)
    CHIP_FILL = (255, 255, 255, 20)

    # -- mittlere Größe: kompakt, aber nicht zu winzig --
    font_title = _load_font(_FONT_TITLE_PATHS, max(18, w // 22))
    font_label = _load_font(_FONT_TITLE_PATHS, max(10, w // 48))
    font_value = _load_font(_FONT_TEXT_PATHS, max(12, w // 38))
    font_intro = _load_font(_FONT_TEXT_PATHS, max(11, w // 40))
    font_eyebrow = _load_font(_FONT_TITLE_PATHS, max(10, w // 47))

    pad_x = int(w * 0.05)
    top = int(h * 0.15)
    eyebrow_height = 0
    if eyebrow:
        eyebrow_height = font_eyebrow.size + 10
        top += eyebrow_height
    bottom_margin = int(h * 0.045)
    badge_size = int(font_title.size * 1.25)
    chip_gap = 8

    wrap_width = max(20, int((w - 2 * pad_x) / (font_intro.size * 0.52)))
    intro_lines = textwrap.wrap(intro, width=wrap_width) if intro else []
    header_height = badge_size + 18 + len(intro_lines) * (font_intro.size + 6) + 12

    available = h - bottom_margin - top - header_height
    chip_h_ideal = int(badge_size * 1.05)
    needed = len(rows) * (chip_h_ideal + chip_gap) - chip_gap if rows else 0
    scale = min(1.0, max(0.55, available / needed)) if needed > 0 else 1.0
    chip_h = max(22, int(chip_h_ideal * scale))
    chip_gap_s = max(5, int(chip_gap * scale))
    if scale < 1.0:
        font_label = _load_font(_FONT_TITLE_PATHS, max(9, int(font_label.size * scale)))
        font_value = _load_font(_FONT_TEXT_PATHS, max(11, int(font_value.size * scale)))

    y = top + header_height
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    yy = y
    for _ in rows:
        odraw.rounded_rectangle((pad_x, yy, w - pad_x, yy + chip_h), radius=9, fill=CHIP_FILL)
        yy += chip_h + chip_gap_s

    composed = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(composed, "RGBA")

    if eyebrow:
        draw.text((pad_x, int(h * 0.15)), eyebrow.upper(), font=font_eyebrow, fill=TEXT_EYEBROW)

    y = top
    draw.rounded_rectangle((pad_x, y, pad_x + badge_size, y + badge_size), radius=int(badge_size * 0.27), fill=accent_rgba)
    cx, cy = pad_x + badge_size // 2, y + badge_size // 2
    r = max(2, badge_size // 7)
    draw.ellipse((cx - 2 * r - 1, cy - r - 1, cx - 1, cy - 1), fill=(255, 255, 255, 255))
    draw.ellipse((cx + 1, cy + 1, cx + 2 * r + 1, cy + r + 1), fill=(255, 255, 255, 255))
    draw.line((cx - 2 * r, cy - r, cx + 2 * r, cy + r), fill=accent_rgba, width=max(2, r))

    draw.text((pad_x + badge_size + 12, y + badge_size // 2 - font_title.size // 2 - 1), title, font=font_title, fill=TEXT_MAIN)
    y += badge_size + 18

    for line in intro_lines:
        draw.text((pad_x, y), line, font=font_intro, fill=TEXT_SUB)
        y += font_intro.size + 6
    y += 12

    icon_r = max(7, int(chip_h * 0.25))
    for label, value in rows:
        icon_cx = pad_x + 16
        icon_cy = y + chip_h // 2
        draw_row_icon(draw, icon_cx, icon_cy, icon_r, label, accent_rgba)
        text_x = pad_x + 16 + icon_r + 13
        draw.text((text_x, y + chip_h * 0.15), label, font=font_label, fill=TEXT_LABEL)
        draw.text((text_x, y + chip_h * 0.47), value, font=font_value, fill=TEXT_MAIN)
        y += chip_h + chip_gap_s

    buf = io.BytesIO()
    composed.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def build_ticket_card_file(db, guild_id: str, title: str, intro: str, rows: list[tuple[str, str]], accent: tuple = (114, 137, 218), eyebrow: str = None) -> "discord.File | None":
    """Baut die fertige Ticket-Karte als discord.File, falls eine Grundplatte
    hinterlegt ist (Setting 'ticket_karte_grundplatte_url'). Gibt None zurück,
    wenn keine Grundplatte gesetzt ist oder das Laden/Zeichnen fehlschlägt -
    der Aufrufer soll dann auf Text (Components V2/Embed) zurückfallen."""
    url = get_setting_value(db, guild_id, "ticket_karte_grundplatte_url")
    if not url:
        return None
    base = await get_ticket_card_base(url)
    if base is None:
        return None
    try:
        buf = draw_ticket_card(base, title, intro, rows, accent=accent, eyebrow=eyebrow)
        return discord.File(buf, filename="ticket.png")
    except Exception as e:
        print(f"Konnte Ticket-Karte nicht zeichnen: {e}")
        return None


# ---------- Ticket-System ----------
def generate_case_id() -> str:
    return "S-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


# Discord "Components V2" (Container/TextDisplay/Section) - erlaubt Nachrichten ohne
# die klassische Embed-Form (kein Farbbalken links, freiere Struktur). Verfügbar seit
# discord.py 2.6. Wir prüfen zur Laufzeit, ob die Bibliothek das unterstützt, und
# fallen sonst automatisch auf normale Embeds zurück - der Bot crasht so nie deswegen.
COMPONENTS_V2 = hasattr(discord.ui, "LayoutView") and hasattr(discord.ui, "Container")


def ticket_info_block(ticket: Ticket) -> str:
    """Baut die Bullet-Liste mit CaseID/Erstellt am/Nutzer im Stil von gängigen Ticket-Bots."""
    created = ticket.created_at.strftime("%d. %B %Y um %H:%M") if ticket.created_at else "—"
    lines = [
        f"📋 **CaseID:** `#{ticket.case_id or ticket.id}`",
        f"🕐 **Erstellt am:** {created}",
        f"👤 **Nutzer:** <@{ticket.user_id}>",
    ]
    if ticket.category:
        lines.insert(1, f"🏷️ **Kategorie:** {ticket.category}")
    return "\n".join(lines)


async def claim_ticket(interaction: discord.Interaction, ticket_id: int):
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).get(ticket_id)
        if not ticket or ticket.status == "geschlossen":
            return await interaction.response.send_message("Dieses Ticket ist nicht mehr offen.", ephemeral=True)
        if ticket.claimed_by:
            return await interaction.response.send_message(f"Dieses Ticket wurde bereits von **{ticket.claimed_by}** übernommen.", ephemeral=True)
        ticket.claimed_by = interaction.user.display_name
        log(db, ticket.guild_id, "system", f"Ticket #{ticket.case_id or ticket.id} wurde von {interaction.user.display_name} übernommen")
        db.commit()
        await interaction.response.send_message(f"✅ **{interaction.user.display_name}** kümmert sich jetzt um dieses Ticket.")
    finally:
        db.close()


class TicketCloseView(discord.ui.View):
    """Klassische Fallback-Variante (normale Buttons unter einem Embed)."""
    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="Übernehmen", style=discord.ButtonStyle.secondary, custom_id="claim_ticket", emoji="✅")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await claim_ticket(interaction, self.ticket_id)

    @discord.ui.button(label="Ticket schließen", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await close_ticket(interaction, self.ticket_id, closed_by=interaction.user.display_name)


if COMPONENTS_V2:
    class TicketClaimButtonV2(discord.ui.Button):
        def __init__(self, ticket_id: int):
            super().__init__(label="Übernehmen", style=discord.ButtonStyle.secondary, emoji="✅", custom_id=f"ticket_claim_v2:{ticket_id}")
            self.ticket_id = ticket_id

        async def callback(self, interaction: discord.Interaction):
            await claim_ticket(interaction, self.ticket_id)

    class TicketCloseButtonV2(discord.ui.Button):
        def __init__(self, ticket_id: int):
            super().__init__(label="Ticket schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id=f"ticket_close_v2:{ticket_id}")
            self.ticket_id = ticket_id

        async def callback(self, interaction: discord.Interaction):
            await close_ticket(interaction, self.ticket_id, closed_by=interaction.user.display_name)

    class TicketContainerV2(discord.ui.Container):
        """Baut die Ticket-Eröffnungsnachricht als eigenständigen Container statt Embed -
        kein farbiger Seitenbalken, freiere Struktur, sieht nicht nach 'Standard-Embed' aus."""
        def __init__(self, ticket: Ticket, mention: str, grund: str):
            super().__init__(accent_colour=discord.Colour(COLOR_INFO))
            self.add_item(discord.ui.TextDisplay(f"## 🎫 Neues Support-Ticket"))
            self.add_item(discord.ui.TextDisplay(f"Hey {mention}, danke für deine Anfrage! Ein Team-Mitglied meldet sich in Kürze."))
            self.add_item(discord.ui.Separator())
            self.add_item(discord.ui.TextDisplay(ticket_info_block(ticket)))
            if grund and grund != "Kein Grund angegeben":
                self.add_item(discord.ui.TextDisplay(f"**📝 Anliegen:** {grund}"))
            self.add_item(discord.ui.Separator())
            self.add_item(discord.ui.ActionRow(TicketClaimButtonV2(ticket.id), TicketCloseButtonV2(ticket.id)))

    class TicketOpenLayoutV2(discord.ui.LayoutView):
        def __init__(self, ticket: Ticket, mention: str, grund: str):
            super().__init__(timeout=None)
            self.add_item(TicketContainerV2(ticket, mention, grund))

    class TicketClosedContainerV2(discord.ui.Container):
        def __init__(self, ticket: Ticket, closed_by: str):
            super().__init__(accent_colour=discord.Colour(COLOR_DANGER))
            self.add_item(discord.ui.TextDisplay("## ❌ Support-Fall abgeschlossen"))
            self.add_item(discord.ui.TextDisplay(f"**{closed_by}** hat dieses Ticket geschlossen."))
            self.add_item(discord.ui.Separator())
            self.add_item(discord.ui.TextDisplay(ticket_info_block(ticket)))
            self.add_item(discord.ui.Separator())
            self.add_item(discord.ui.TextDisplay("-# Der Kanal wird in 5 Sekunden archiviert"))

    class TicketClosedLayoutV2(discord.ui.LayoutView):
        def __init__(self, ticket: Ticket, closed_by: str):
            super().__init__(timeout=None)
            self.add_item(TicketClosedContainerV2(ticket, closed_by))


async def close_ticket(interaction: discord.Interaction, ticket_id: int, closed_by: str):
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).get(ticket_id)
        if not ticket or ticket.status == "geschlossen":
            return await interaction.response.send_message("Dieses Ticket ist bereits geschlossen.", ephemeral=True)
        ticket.status = "geschlossen"
        ticket.closed_at = datetime.now(timezone.utc)
        ticket.closed_by = closed_by
        log(db, ticket.guild_id, "system", f"Ticket #{ticket.case_id or ticket.id} ({ticket.username}) wurde von {closed_by} geschlossen")
        db.commit()

        rows = [("CaseID", f"#{ticket.case_id or ticket.id}")]
        rows += [("Erstellt am", ticket.created_at.strftime("%d. %B %Y um %H:%M") if ticket.created_at else "—")]
        rows += [("Nutzer", ticket.username)]
        card_file = await build_ticket_card_file(
            db, ticket.guild_id, "Support-Fall abgeschlossen",
            f"@{ticket.username} braucht keine Hilfe mehr!",
            rows, accent=(237, 66, 69), eyebrow=ticket.category or "Support",
        )

        if card_file:
            await interaction.response.send_message(file=card_file)
        elif COMPONENTS_V2:
            await interaction.response.send_message(view=TicketClosedLayoutV2(ticket, closed_by))
        else:
            embed = discord.Embed(
                title="❌ Support-Fall abgeschlossen",
                description=f"**{closed_by}** hat dieses Ticket geschlossen.\n\n{ticket_info_block(ticket)}",
                color=COLOR_DANGER, timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Der Kanal wird in 5 Sekunden archiviert")
            apply_brand(embed, db, interaction.guild)
            await interaction.response.send_message(embed=embed)

        channel = interaction.guild.get_channel(int(ticket.channel_id)) if ticket.channel_id else None
        if channel:
            await asyncio.sleep(5)
            try:
                await channel.delete(reason=f"Ticket geschlossen von {closed_by}")
            except Exception as e:
                print(f"Ticket-Kanal konnte nicht gelöscht werden: {e}")
    finally:
        db.close()


async def create_ticket_channel(interaction: discord.Interaction, grund: str, kategorie: str = None):
    """Zentrale Ticket-Erstellung - wird sowohl von /ticket als auch vom Panel-Dropdown genutzt."""
    db = SessionLocal()
    try:
        guild_id = str(interaction.guild_id)
        if not is_module_enabled(db, guild_id, "tickets"):
            return await module_disabled_reply(interaction, "tickets")

        existing = db.query(Ticket).filter(
            Ticket.guild_id == guild_id, Ticket.user_id == str(interaction.user.id), Ticket.status == "offen"
        ).first()
        if existing:
            return await interaction.response.send_message("❌ Du hast bereits ein offenes Ticket.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        support_role_id = get_setting_value(db, guild_id, "ticket_support_rolle")
        support_role = None
        if support_role_id:
            support_role = interaction.guild.get_role(int(support_role_id))
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        parent_category = None
        category_id = get_setting_value(db, guild_id, "ticket_kategorie")
        if category_id:
            parent_category = interaction.guild.get_channel(int(category_id))

        case_id = generate_case_id()
        channel_name = f"ticket-{interaction.user.name}".lower().replace(" ", "-")[:90]
        channel = await interaction.guild.create_text_channel(
            channel_name, category=parent_category, overwrites=overwrites, reason=f"Ticket von {interaction.user.display_name}"
        )

        ticket = Ticket(
            guild_id=guild_id, user_id=str(interaction.user.id), username=interaction.user.display_name,
            subject=grund, category=kategorie, case_id=case_id, status="offen", channel_id=str(channel.id),
        )
        db.add(ticket)
        log(db, guild_id, "system", f"{interaction.user.display_name} hat ein Ticket eröffnet ({case_id}): {grund}")
        db.commit()

        ping = f"{interaction.user.mention} {support_role.mention if support_role else ''}".strip()

        rows = [("CaseID", f"#{ticket.case_id}"), ("Erstellt am", ticket.created_at.strftime("%d. %B %Y um %H:%M"))]
        rows.append(("Nutzer", interaction.user.display_name))
        card_file = await build_ticket_card_file(
            db, guild_id, "Neues Support-Ticket",
            f"Hey {interaction.user.display_name}, danke für deine Anfrage! Ein Team-Mitglied meldet sich in Kürze.",
            rows, eyebrow=ticket.category or "Support",
        )

        if card_file:
            close_view = TicketCloseView(ticket.id)
            await channel.send(content=ping, file=card_file, view=close_view)
        elif COMPONENTS_V2:
            # Bei Components V2 darf 'content' nicht zusammen mit einer LayoutView gesendet
            # werden - die Erwähnung/Ping steckt stattdessen im ersten Textbaustein.
            view = TicketOpenLayoutV2(ticket, ping, grund)
            await channel.send(view=view)
        else:
            embed = discord.Embed(
                title="🎫 Neues Support-Ticket",
                description=f"Hey {interaction.user.mention}, danke für deine Anfrage! Ein Team-Mitglied meldet sich in Kürze.\n\n{ticket_info_block(ticket)}",
                color=COLOR_INFO, timestamp=datetime.now(timezone.utc),
            )
            if grund and grund != "Kein Grund angegeben":
                embed.add_field(name="📝 Anliegen", value=grund, inline=False)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            apply_brand(embed, db, interaction.guild)
            await channel.send(content=ping, embed=embed, view=TicketCloseView(ticket.id))
        await interaction.followup.send(f"✅ Dein Ticket wurde erstellt: {channel.mention}", ephemeral=True)
    finally:
        db.close()


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, categories: list[str]):
        options = [discord.SelectOption(label=c, emoji="🎫") for c in categories[:25]]
        super().__init__(placeholder="Wähle eine Kategorie…", options=options, custom_id="ticket_panel_select")

    async def callback(self, interaction: discord.Interaction):
        await create_ticket_channel(interaction, grund=f"Kategorie: {self.values[0]}", kategorie=self.values[0])


class TicketPanelView(discord.ui.View):
    """Fallback-Panel (normaler View unter einem Embed)."""
    def __init__(self, categories: list[str]):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect(categories))


if COMPONENTS_V2:
    class TicketPanelContainerV2(discord.ui.Container):
        def __init__(self, titel: str, text: str, categories: list[str]):
            super().__init__(accent_colour=discord.Colour(BRAND_COLOR))
            self.add_item(discord.ui.TextDisplay(f"## 🎫 {titel}"))
            self.add_item(discord.ui.TextDisplay(text))
            self.add_item(discord.ui.Separator())
            self.add_item(discord.ui.ActionRow(TicketCategorySelect(categories)))

    class TicketPanelLayoutV2(discord.ui.LayoutView):
        def __init__(self, titel: str, text: str, categories: list[str]):
            super().__init__(timeout=None)
            self.add_item(TicketPanelContainerV2(titel, text, categories))


@bot.tree.command(name="ticket_panel", description="[Admin] Postet ein Ticket-Panel mit Kategorie-Auswahl in diesen Kanal")
@app_commands.checks.has_permissions(administrator=True)
async def ticket_panel_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        guild_id = str(interaction.guild_id)
        titel = get_setting_value(db, guild_id, "ticket_panel_titel", default="Support-Tickets")
        text = get_setting_value(db, guild_id, "ticket_panel_text", default="Hast du eine Frage oder ein Problem? Erstell dir hier ein privates Ticket — unser Team hilft dir schnellstmöglich weiter.")
        bild_url = get_setting_value(db, guild_id, "ticket_panel_bild_url")
        kategorien_raw = get_setting_value(db, guild_id, "ticket_kategorien", default="Support")
        kategorien = [k.strip() for k in kategorien_raw.split(",") if k.strip()]
    finally:
        db.close()

    if COMPONENTS_V2:
        await interaction.response.send_message(view=TicketPanelLayoutV2(titel, text, kategorien))
    else:
        embed = discord.Embed(title=f"🎫 {titel}", description=text, color=BRAND_COLOR, timestamp=datetime.now(timezone.utc))
        db2 = SessionLocal()
        try:
            apply_brand(embed, db2, interaction.guild)
        finally:
            db2.close()
        if bild_url:
            embed.set_image(url=bild_url)
        await interaction.response.send_message(embed=embed, view=TicketPanelView(kategorien))


@bot.tree.command(name="ticket", description="Öffnet ein neues Support-Ticket")
@app_commands.describe(grund="Worum geht es? (kurz)")
async def ticket_cmd(interaction: discord.Interaction, grund: str = "Kein Grund angegeben"):
    await create_ticket_channel(interaction, grund)


@bot.tree.command(name="ticket_schliessen", description="Schließt das aktuelle Ticket (nur im Ticket-Kanal nutzbar)")
async def ticket_close_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.channel_id == str(interaction.channel_id), Ticket.status == "offen").first()
        if not ticket:
            return await interaction.response.send_message("Das ist kein offener Ticket-Kanal.", ephemeral=True)
    finally:
        db.close()
    await close_ticket(interaction, ticket.id, closed_by=interaction.user.display_name)


async def send_announcement(guild: discord.Guild, guild_id: str, titel: str, nachricht: str, rolle: "discord.Role | None" = None) -> discord.TextChannel:
    """Baut das Ankündigungs-Embed und postet es in den eingestellten Kanal.
    Wirft ValueError, falls kein Kanal konfiguriert oder gefunden wurde."""
    db = SessionLocal()
    try:
        channel_id = get_setting_value(db, guild_id, "ankuendigungskanal")
    finally:
        db.close()
    if not channel_id:
        raise ValueError("Es ist kein Ankündigungskanal eingestellt (siehe Einstellungen → Rollen & Kanäle).")
    channel = guild.get_channel(int(channel_id))
    if not channel:
        raise ValueError("Der eingestellte Ankündigungskanal wurde nicht gefunden.")

    embed = discord.Embed(
        title=f"📢 {titel}", description=nachricht, color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Ankündigung")
    db2 = SessionLocal()
    try:
        apply_brand(embed, db2, guild)
    finally:
        db2.close()
    content = rolle.mention if rolle else None
    await channel.send(content=content, embed=embed)
    return channel


@bot.tree.command(name="ankuendigen", description="Sendet eine Ankündigung in den eingestellten Ankündigungskanal")
@app_commands.describe(titel="Überschrift der Ankündigung", nachricht="Der eigentliche Text", rolle="Optional: Rolle, die gepingt werden soll")
@app_commands.checks.has_permissions(administrator=True)
async def announce_cmd(interaction: discord.Interaction, titel: str, nachricht: str, rolle: discord.Role = None):
    try:
        channel = await send_announcement(interaction.guild, str(interaction.guild_id), titel, nachricht, rolle)
    except ValueError as e:
        return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
    await interaction.response.send_message(f"✅ Ankündigung gesendet in {channel.mention}.", ephemeral=True)


# =========================================================
# TEIL 2: DIE DASHBOARD-API
# =========================================================
app = FastAPI(title="Server-Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


MAIN_LOOP = None


@app.on_event("startup")
async def startup():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    init_db()
    if DISCORD_BOT_TOKEN:
        asyncio.create_task(bot.start(DISCORD_BOT_TOKEN))
    else:
        print("WARNUNG: DISCORD_BOT_TOKEN fehlt - der Bot startet nicht, nur die API läuft.")


# ---------- Auth (Discord OAuth2 fürs Dashboard-Login) ----------
@app.get("/auth/login")
def login():
    params = (
        f"client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
        f"&response_type=code&scope=identify%20guilds.members.read"
    )
    return RedirectResponse(f"{DISCORD_API}/oauth2/authorize?{params}")


@app.get("/auth/callback")
async def callback(code: str, request: Request):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            raise HTTPException(400, "Discord-Login fehlgeschlagen")
        access_token = token_res.json()["access_token"]
        user_res = await client.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {access_token}"})
        discord_user = user_res.json()

    db = SessionLocal()
    log(db, None, "login", f"{discord_user['username']} hat sich über Discord angemeldet")

    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unbekannt").split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "unbekannt")
    session_id = str(uuid.uuid4())
    db.add(LoginSession(guild_id=None, user_id=discord_user["id"], username=discord_user["username"],
                        ip=client_ip, user_agent=user_agent, token=session_id, revoked=""))
    db.commit()
    db.close()

    session_token = jwt.encode(
        {"sub": discord_user["id"], "username": discord_user["username"], "avatar": discord_user.get("avatar"),
         "sid": session_id, "exp": int(time.time()) + 60 * 60 * 24 * 7},
        JWT_SECRET, algorithm="HS256",
    )
    redirect = RedirectResponse(FRONTEND_URL)
    redirect.set_cookie(
        "session", session_token, httponly=True, samesite="none", secure=True, max_age=60 * 60 * 24 * 7
    )
    return redirect


@app.get("/auth/me")
def me(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sitzung abgelaufen")


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            session_id = payload.get("sid")
            if session_id:
                db = SessionLocal()
                try:
                    session = db.query(LoginSession).filter(LoginSession.token == session_id).first()
                    if session:
                        session.revoked = "yes"
                        db.commit()
                finally:
                    db.close()
        except jwt.PyJWTError:
            pass
    response.delete_cookie("session", samesite="none", secure=True)
    return {"ok": True}


def require_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sitzung abgelaufen")

    session_id = payload.get("sid")
    if session_id:
        db = SessionLocal()
        try:
            session = db.query(LoginSession).filter(LoginSession.token == session_id).first()
            if session and session.revoked == "yes":
                raise HTTPException(401, "Diese Sitzung wurde beendet. Bitte erneut anmelden.")
        finally:
            db.close()
    return payload


def is_guild_member(guild_id: str, discord_user_id: str) -> bool:
    """Prüft (über den Bot-Cache), ob der Nutzer Mitglied des angegebenen Servers ist."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return False
    member = guild.get_member(int(discord_user_id))
    return member is not None


def require_guild_access(guild_id: str, user=Depends(require_user), db: Session = Depends(get_db)):
    """Stellt sicher, dass der eingeloggte Nutzer wirklich Mitglied dieses Servers ist,
    bevor er dort etwas verändern darf. Gleicht dabei zugleich die Dashboard-Rolle
    mit der aktuellen Discord-Rolle/Owner-Status ab."""
    guild = bot.get_guild(int(guild_id))
    if not guild or not guild.get_member(int(user["sub"])):
        raise HTTPException(403, "Du bist kein Mitglied dieses Servers.")

    db_user = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    sync_admin_role(db, guild, db_user)
    db.commit()
    return user


# ---------- Server-Auswahl ----------
@app.get("/api/my-guilds")
def my_guilds(user=Depends(require_user)):
    result = []
    for guild in bot.guilds:
        member = guild.get_member(int(user["sub"]))
        if member:
            result.append({"id": str(guild.id), "name": guild.name})
    return result


@app.get("/api/guild-info")
def guild_info(guild_id: str, db: Session = Depends(get_db)):
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    return {
        "id": guild_id,
        "name": guild.name if guild else None,
        "icon_url": str(guild.icon.url) if guild and guild.icon else None,
        "exists": guild is not None,
    }


@app.get("/api/guild-channels")
def guild_channels(guild_id: str):
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return []
    return [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]


@app.get("/api/guild-roles")
def guild_roles(guild_id: str):
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return []
    return [{"id": str(r.id), "name": r.name} for r in guild.roles if r.name != "@everyone"]


@app.get("/api/guild-categories")
def guild_categories(guild_id: str):
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if not guild:
        return []
    return [{"id": str(c.id), "name": c.name} for c in guild.categories]


def compute_total_balance(db, guild_id: str, guild) -> int:
    """Guthaben-Summe über ALLE echten Mitglieder - inkl. Startguthaben für
    Mitglieder ohne DB-Eintrag, damit das konsistent mit der Bank-Kontenliste ist."""
    db_users = {u.discord_id: u.balance for u in db.query(User).filter(User.guild_id == guild_id).all()}
    if not guild:
        return sum(db_users.values())
    starting = get_starting_balance(db, guild_id)
    total = 0
    for member in guild.members:
        if member.bot:
            continue
        total += db_users.get(str(member.id), starting)
    return total


# ---------- Übersicht ----------
@app.get("/api/overview")
def overview(guild_id: str, db: Session = Depends(get_db)):
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    total_balance = compute_total_balance(db, guild_id, guild)
    member_count = guild.member_count if guild else (db.query(func.count(User.id)).filter(User.guild_id == guild_id).scalar() or 0)
    on_duty = db.query(func.sum(DutyFraction.on_duty)).filter(DutyFraction.guild_id == guild_id).scalar() or 0
    recent = db.query(LogEntry).filter(LogEntry.guild_id == guild_id).order_by(LogEntry.created_at.desc()).limit(5).all()
    uptime_seconds = (datetime.now(timezone.utc) - BOT_START_TIME).total_seconds()
    return {
        "bot_status": "online" if bot.is_ready() else "startet…",
        "member_count": member_count,
        "on_duty": on_duty,
        "total_balance": total_balance,
        "uptime_seconds": uptime_seconds,
        "recent_logs": [{"id": l.id, "type": l.type, "text": l.text, "time": l.created_at.isoformat()} for l in recent],
    }


# ---------- Ankündigungen ----------
@app.post("/api/announce")
async def announce_dashboard(guild_id: str, titel: str, nachricht: str, user=Depends(require_guild_access)):
    if not bot.is_ready():
        raise HTTPException(503, "Der Bot ist gerade nicht verbunden.")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(404, "Server nicht gefunden.")
    try:
        channel = await send_announcement(guild, guild_id, titel, nachricht)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db = SessionLocal()
    try:
        log(db, guild_id, "system", f"Ankündigung gesendet von {user.get('username', 'Dashboard')}: {titel}")
        db.commit()
    finally:
        db.close()
    return {"ok": True, "channel": channel.name}


# ---------- Bot-Steuerung ----------
@app.post("/api/bot/sync")
async def sync_commands(guild_id: str, user=Depends(require_guild_access)):
    """Synct die Slash-Commands neu für diesen Server (z.B. nachdem neue Befehle
    hinzugefügt wurden und sie in Discord noch nicht auftauchen)."""
    if not bot.is_ready():
        raise HTTPException(503, "Der Bot ist gerade nicht verbunden.")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise HTTPException(404, "Server nicht gefunden — ist der Bot dort Mitglied?")
    try:
        synced = await bot.tree.sync(guild=guild)
    except Exception as e:
        raise HTTPException(500, f"Sync fehlgeschlagen: {e}")
    db = SessionLocal()
    try:
        log(db, guild_id, "system", f"Befehle neu synchronisiert von {user.get('username', 'Dashboard')} ({len(synced)} Befehle)")
        db.commit()
    finally:
        db.close()
    return {"ok": True, "synced_count": len(synced)}


# ---------- Bank ----------
@app.get("/api/bank/accounts")
def bank_accounts(guild_id: str, db: Session = Depends(get_db)):
    """Zeigt ALLE echten Discord-Mitglieder mit Kontostand, nicht nur die,
    die schon mal einen Bot-Befehl genutzt oder sich im Dashboard angemeldet
    haben. Mitglieder ohne DB-Eintrag bekommen das Startguthaben angezeigt."""
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    db_users = {u.discord_id: u for u in db.query(User).filter(User.guild_id == guild_id).all()}

    if not guild:
        accounts = [{"id": u.id, "name": u.username, "balance": u.balance, "cash": u.cash, "role": u.role} for u in db_users.values()]
        return sorted(accounts, key=lambda a: a["balance"], reverse=True)

    accounts = []
    for member in guild.members:
        if member.bot:
            continue
        u = db_users.get(str(member.id))
        if u:
            accounts.append({"id": u.id, "name": u.username, "balance": u.balance, "cash": u.cash, "role": u.role})
        else:
            accounts.append({
                "id": f"{guild_id}:{member.id}", "name": member.display_name,
                "balance": get_starting_balance(db, guild_id), "cash": 0, "role": "Mitglied",
            })
    return sorted(accounts, key=lambda a: a["balance"], reverse=True)


@app.get("/api/bank/transactions")
def bank_transactions(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": t.id, "from": t.from_user, "to": t.to_user, "amount": t.amount,
             "type": t.type, "time": t.created_at.isoformat()}
            for t in db.query(Transaction).filter(Transaction.guild_id == guild_id).order_by(Transaction.created_at.desc()).limit(50).all()]


@app.post("/api/bank/transfer")
def bank_transfer_dashboard(guild_id: str, empfaenger_id: str, betrag: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if betrag <= 0:
        raise HTTPException(400, "Der Betrag muss positiv sein.")
    max_transfer = get_setting_value(db, guild_id, "max_ueberweisung")
    if max_transfer and betrag > int(max_transfer):
        raise HTTPException(400, f"Maximal erlaubter Betrag: {int(max_transfer)} ₡")
    sender = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    receiver = db.query(User).get(empfaenger_id)
    if not receiver:
        discord_id = empfaenger_id.split(":", 1)[1] if ":" in empfaenger_id else empfaenger_id
        guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        member = guild.get_member(int(discord_id)) if guild else None
        if not member:
            raise HTTPException(404, "Empfänger nicht gefunden")
        receiver = get_or_create_user_by_id(db, guild_id, discord_id, member.display_name)
    if sender.id == receiver.id:
        raise HTTPException(400, "Du kannst nicht an dich selbst überweisen.")
    if sender.balance < betrag:
        raise HTTPException(400, "Nicht genug Guthaben.")
    sender.balance -= betrag
    receiver.balance += betrag
    db.add(Transaction(guild_id=guild_id, from_user=sender.username, to_user=receiver.username, amount=betrag, type="Überweisung"))
    log(db, guild_id, "bank", f"{sender.username} überwies {betrag} ₡ an {receiver.username} (über Dashboard)")
    db.commit()
    return {"ok": True, "balance": sender.balance}


@app.post("/api/bank/deposit")
def bank_deposit_dashboard(guild_id: str, betrag: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if betrag <= 0:
        raise HTTPException(400, "Der Betrag muss positiv sein.")
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    if me.cash < betrag:
        raise HTTPException(400, "Nicht genug Bargeld.")
    me.cash -= betrag
    me.balance += betrag
    db.add(Transaction(guild_id=guild_id, from_user=me.username, to_user=me.username, amount=betrag, type="Einzahlung"))
    log(db, guild_id, "bank", f"{me.username} hat {betrag} ₡ eingezahlt (über Dashboard)")
    db.commit()
    return {"ok": True, "balance": me.balance, "cash": me.cash}


@app.post("/api/bank/withdraw")
def bank_withdraw_dashboard(guild_id: str, betrag: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if betrag <= 0:
        raise HTTPException(400, "Der Betrag muss positiv sein.")
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    if me.balance < betrag:
        raise HTTPException(400, "Nicht genug Bankguthaben.")
    me.balance -= betrag
    me.cash += betrag
    db.add(Transaction(guild_id=guild_id, from_user=me.username, to_user=me.username, amount=betrag, type="Auszahlung"))
    log(db, guild_id, "bank", f"{me.username} hat {betrag} ₡ abgehoben (über Dashboard)")
    db.commit()
    return {"ok": True, "balance": me.balance, "cash": me.cash}


# ---------- Shop ----------
@app.get("/api/shop/items")
def shop_items(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": i.id, "name": i.name, "category": i.category, "price": i.price, "sold": i.sold, "roleId": i.role_id}
            for i in db.query(ShopItem).filter(ShopItem.guild_id == guild_id).all()]


@app.post("/api/shop/items")
def create_item(guild_id: str, name: str, category: str, price: int, role_id: str | None = None, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    item = ShopItem(guild_id=guild_id, name=name, category=category, price=price, role_id=role_id or None)
    db.add(item)
    log(db, guild_id, "shop", f"Neuer Artikel erstellt: {name} ({price} ₡)")
    db.commit()
    return {"ok": True, "id": item.id}


@app.post("/api/shop/items/{item_id}")
def update_item(item_id: int, guild_id: str, name: str, category: str, price: int, role_id: str | None = None, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    item = db.query(ShopItem).filter(ShopItem.id == item_id, ShopItem.guild_id == guild_id).first()
    if not item:
        raise HTTPException(404, "Artikel nicht gefunden")
    item.name, item.category, item.price, item.role_id = name, category, price, (role_id or None)
    log(db, guild_id, "shop", f"Artikel bearbeitet: {name} ({price} ₡)")
    db.commit()
    return {"ok": True}


@app.delete("/api/shop/items/{item_id}")
def delete_item(item_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    item = db.query(ShopItem).filter(ShopItem.id == item_id, ShopItem.guild_id == guild_id).first()
    if not item:
        raise HTTPException(404, "Artikel nicht gefunden")
    log(db, guild_id, "shop", f"Artikel gelöscht: {item.name}")
    db.delete(item)
    db.commit()
    return {"ok": True}


# ---------- Dienstsystem ----------
@app.get("/api/dienst")
def dienst(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": d.id, "fraction": d.name, "onDuty": d.on_duty, "total": d.total,
             "hoursToday": d.hours_today, "channelId": d.channel_id}
            for d in db.query(DutyFraction).filter(DutyFraction.guild_id == guild_id).all()]


@app.get("/api/dienst/me")
def dienst_me(guild_id: str, db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    return {"onDutyFraction": me.on_duty_fraction}


@app.post("/api/dienst/{fraction_id}/toggle")
async def toggle_dienst(fraction_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    f_check = db.query(DutyFraction).filter(DutyFraction.id == fraction_id, DutyFraction.guild_id == guild_id).first()
    if not f_check:
        raise HTTPException(404, "Fraktion nicht gefunden")

    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    try:
        f, status, paid = toggle_duty_for_user(db, guild_id, me, f_check.name)
    except ValueError as e:
        raise HTTPException(400, str(e))

    await post_duty_embed(f, me.username)
    return {"ok": True, "onDutyFraction": me.on_duty_fraction, "paid": paid}


@app.post("/api/dienst")
def create_fraction(guild_id: str, name: str, total: int = 5, channel_id: str | None = None,
                     db: Session = Depends(get_db), user=Depends(require_guild_access)):
    f = DutyFraction(guild_id=guild_id, name=name, total=total, channel_id=channel_id or None)
    db.add(f)
    log(db, guild_id, "system", f"Neue Fraktion angelegt: {name} ({total} Plätze)")
    db.commit()
    return {"ok": True, "id": f.id}


@app.delete("/api/dienst/{fraction_id}")
def delete_fraction(fraction_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    f = db.query(DutyFraction).filter(DutyFraction.id == fraction_id, DutyFraction.guild_id == guild_id).first()
    if not f:
        raise HTTPException(404, "Fraktion nicht gefunden")
    db.delete(f)
    log(db, guild_id, "system", f"Fraktion gelöscht: {f.name}")
    db.commit()
    return {"ok": True}


# ---------- Giveaways ----------
@app.get("/api/giveaways")
def giveaways(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": g.id, "prize": g.prize, "entries": g.entries, "status": g.status,
             "winner": g.winner, "ends": g.ends_at.isoformat() if g.ends_at else None}
            for g in db.query(Giveaway).filter(Giveaway.guild_id == guild_id).all()]


@app.post("/api/giveaways")
async def create_giveaway_dashboard(guild_id: str, preis: str, dauer_minuten: int, channel_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if not bot.is_ready():
        raise HTTPException(503, "Bot ist noch nicht bereit.")
    try:
        channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
    except Exception:
        raise HTTPException(400, "Kanal nicht gefunden. Prüfe die Kanal-ID.")

    ends_at = datetime.now(timezone.utc) + timedelta(minutes=dauer_minuten)
    embed = discord.Embed(
        title="🎉 Giveaway gestartet!",
        description=f"### 🏆 {preis}\n\nReagiere mit {GIVEAWAY_EMOJI}, um teilzunehmen!",
        color=BRAND_COLOR, timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="⏰ Endet", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.set_footer(text="Viel Glück!")
    apply_brand(embed, db, channel.guild)
    message = await channel.send(embed=embed)
    await message.add_reaction(GIVEAWAY_EMOJI)

    g = Giveaway(guild_id=guild_id, prize=preis, status="aktiv", ends_at=ends_at, channel_id=str(channel.id), message_id=str(message.id), participants="")
    db.add(g)
    log(db, guild_id, "system", f"Giveaway über Dashboard gestartet: {preis}")
    db.commit()
    return {"ok": True, "id": g.id}


@app.post("/api/giveaways/{giveaway_id}/end")
async def end_giveaway_dashboard(giveaway_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    g = db.query(Giveaway).filter(Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id).first()
    if not g or g.status != "aktiv":
        raise HTTPException(400, "Giveaway nicht gefunden oder schon beendet.")
    await draw_giveaway_winner(db, g)
    return {"ok": True}


@app.post("/api/giveaways/{giveaway_id}/reroll")
async def reroll_giveaway_dashboard(giveaway_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    g = db.query(Giveaway).filter(Giveaway.id == giveaway_id, Giveaway.guild_id == guild_id).first()
    if not g:
        raise HTTPException(404, "Giveaway nicht gefunden.")
    await draw_giveaway_winner(db, g)
    return {"ok": True}


# ---------- Logs ----------
@app.get("/api/logs")
def logs(guild_id: str, type: str | None = None, db: Session = Depends(get_db)):
    q = db.query(LogEntry).filter(LogEntry.guild_id == guild_id).order_by(LogEntry.created_at.desc())
    if type and type != "alle":
        q = q.filter(LogEntry.type == type)
    return [{"id": l.id, "type": l.type, "text": l.text, "time": l.created_at.isoformat()} for l in q.limit(200).all()]


# ---------- Tickets ----------
@app.get("/api/tickets")
def get_tickets(guild_id: str, db: Session = Depends(get_db)):
    tickets = db.query(Ticket).filter(Ticket.guild_id == guild_id).order_by(Ticket.created_at.desc()).all()
    return [
        {
            "id": t.id, "case_id": t.case_id, "user_id": t.user_id, "username": t.username,
            "subject": t.subject, "category": t.category,
            "status": t.status, "channel_id": t.channel_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "closed_by": t.closed_by,
        }
        for t in tickets
    ]


@app.post("/api/tickets/{ticket_id}/close")
async def close_ticket_dashboard(ticket_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.guild_id == guild_id).first()
    if not ticket:
        raise HTTPException(404, "Ticket nicht gefunden.")
    if ticket.status == "geschlossen":
        return {"ok": True, "already_closed": True}

    closed_by = user.get("username", "Dashboard")
    ticket.status = "geschlossen"
    ticket.closed_at = datetime.now(timezone.utc)
    ticket.closed_by = closed_by
    log(db, guild_id, "system", f"Ticket #{ticket.id} ({ticket.username}) wurde von {closed_by} über das Dashboard geschlossen")
    db.commit()

    guild = bot.get_guild(int(guild_id))
    channel = guild.get_channel(int(ticket.channel_id)) if guild and ticket.channel_id else None
    if channel:
        try:
            await channel.send(f"🔒 Ticket wurde von **{closed_by}** über das Dashboard geschlossen. Der Kanal wird in 5 Sekunden archiviert.")
            await asyncio.sleep(5)
            await channel.delete(reason=f"Ticket geschlossen von {closed_by} (Dashboard)")
        except Exception as e:
            print(f"Ticket-Kanal konnte nicht gelöscht werden: {e}")
    return {"ok": True}


# ---------- Statistiken ----------
@app.get("/api/stats")
def stats(guild_id: str, db: Session = Depends(get_db)):
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    active_users = db.query(func.count(User.id)).filter(User.guild_id == guild_id, User.last_seen >= seven_days_ago).scalar() or 0

    weekday_labels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    weekly_activity = []
    today = datetime.now(timezone.utc).date()
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        count = (
            db.query(func.count(LogEntry.id))
            .filter(LogEntry.guild_id == guild_id, func.date(LogEntry.created_at) == day.isoformat())
            .scalar() or 0
        )
        weekly_activity.append({"day": weekday_labels[day.weekday()], "count": count})

    return {
        "member_count": (bot.get_guild(int(guild_id)).member_count if guild_id.isdigit() and bot.get_guild(int(guild_id)) else (db.query(func.count(User.id)).filter(User.guild_id == guild_id).scalar() or 0)),
        "active_users_7d": active_users,
        "total_balance": compute_total_balance(db, guild_id, bot.get_guild(int(guild_id)) if guild_id.isdigit() else None),
        "shop_sales": db.query(func.sum(ShopItem.sold)).filter(ShopItem.guild_id == guild_id).scalar() or 0,
        "duty_hours_today": db.query(func.sum(DutyFraction.hours_today)).filter(DutyFraction.guild_id == guild_id).scalar() or 0,
        "giveaway_count": db.query(func.count(Giveaway.id)).filter(Giveaway.guild_id == guild_id).scalar() or 0,
        "weekly_activity": weekly_activity,
        "uptime_seconds": (datetime.now(timezone.utc) - BOT_START_TIME).total_seconds(),
    }


# ---------- Benutzerverwaltung ----------
def compute_status(last_seen) -> str:
    if not last_seen:
        return "offline"
    last_seen = last_seen.replace(tzinfo=timezone.utc) if last_seen.tzinfo is None else last_seen
    delta = datetime.now(timezone.utc) - last_seen
    if delta < timedelta(minutes=5):
        return "online"
    if delta < timedelta(minutes=30):
        return "idle"
    return "offline"


@app.get("/api/users")
def users(guild_id: str, db: Session = Depends(get_db)):
    """Zeigt ALLE echten Discord-Mitglieder, nicht nur die, die schon mal
    einen Bot-Befehl genutzt oder sich im Dashboard angemeldet haben.
    Mitglieder ohne DB-Eintrag bekommen Standardwerte (Startguthaben etc.)."""
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    db_users = {u.discord_id: u for u in db.query(User).filter(User.guild_id == guild_id).all()}

    if not guild:
        # Bot gerade nicht verbunden -> Notlösung: nur bekannte DB-Einträge zeigen
        return [
            {"id": u.id, "name": u.username, "role": u.role, "balance": u.balance, "cash": u.cash,
             "status": compute_status(u.last_seen), "joined": u.joined_at.isoformat(),
             "onDutyFraction": u.on_duty_fraction, "afkReason": u.afk_reason}
            for u in db_users.values()
        ]

    result = []
    for member in guild.members:
        if member.bot:
            continue
        u = db_users.get(str(member.id))
        if u:
            result.append({
                "id": u.id, "name": u.username, "role": u.role, "balance": u.balance, "cash": u.cash,
                "status": compute_status(u.last_seen), "joined": u.joined_at.isoformat(),
                "onDutyFraction": u.on_duty_fraction, "afkReason": u.afk_reason,
            })
        else:
            result.append({
                "id": f"{guild_id}:{member.id}", "name": member.display_name, "role": "Mitglied",
                "balance": get_starting_balance(db, guild_id), "cash": 0, "status": "offline",
                "joined": (member.joined_at or datetime.now(timezone.utc)).isoformat(),
                "onDutyFraction": None, "afkReason": None,
            })
    return result


@app.post("/api/users/{user_id}/role")
def update_role(user_id: str, guild_id: str, role: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    target = db.query(User).filter(User.id == user_id, User.guild_id == guild_id).first()
    if not target:
        # Mitglied hat noch keinen DB-Eintrag (z.B. noch nie mit dem Bot interagiert) -> anlegen
        discord_id = user_id.split(":", 1)[1] if ":" in user_id else user_id
        guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        member = guild.get_member(int(discord_id)) if guild else None
        if not member:
            raise HTTPException(404, "Benutzer nicht gefunden")
        target = get_or_create_user_by_id(db, guild_id, discord_id, member.display_name)
    target.role = role
    log(db, guild_id, "system", f"Rolle von {target.username} geändert zu {role}")
    db.commit()
    return {"ok": True}


TEAM_ROLES = ["Support", "Moderator", "Admin", "Owner"]


@app.get("/api/team")
def team_members(guild_id: str, db: Session = Depends(get_db)):
    """Alle Nutzer mit einer Team-Rolle (alles außer 'Mitglied')."""
    members = db.query(User).filter(User.guild_id == guild_id, User.role.in_(TEAM_ROLES)).all()
    return [
        {
            "id": u.id, "name": u.username, "role": u.role,
            "status": compute_status(u.last_seen), "joined": u.joined_at.isoformat(),
        }
        for u in members
    ]


# ---------- To-Do-Liste ----------
@app.get("/api/todos")
def get_todos(guild_id: str, db: Session = Depends(get_db)):
    todos = db.query(Todo).filter(Todo.guild_id == guild_id).order_by(Todo.created_at.desc()).all()
    return [
        {
            "id": t.id, "title": t.title, "status": t.status, "assigned_to": t.assigned_to,
            "created_by": t.created_by, "created_at": t.created_at.isoformat() if t.created_at else None,
            "done_at": t.done_at.isoformat() if t.done_at else None,
        }
        for t in todos
    ]


@app.post("/api/todos")
def create_todo(guild_id: str, title: str, assigned_to: str = "", db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if not title.strip():
        raise HTTPException(400, "Titel darf nicht leer sein.")
    todo = Todo(guild_id=guild_id, title=title.strip(), assigned_to=assigned_to or None, created_by=user.get("username", "Dashboard"))
    db.add(todo)
    log(db, guild_id, "system", f"Aufgabe erstellt: {title.strip()}")
    db.commit()
    return {"ok": True, "id": todo.id}


@app.post("/api/todos/{todo_id}/toggle")
def toggle_todo(todo_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    todo = db.query(Todo).filter(Todo.id == todo_id, Todo.guild_id == guild_id).first()
    if not todo:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    if todo.status == "erledigt":
        todo.status = "offen"
        todo.done_at = None
    else:
        todo.status = "erledigt"
        todo.done_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "status": todo.status}


@app.delete("/api/todos/{todo_id}")
def delete_todo(todo_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    todo = db.query(Todo).filter(Todo.id == todo_id, Todo.guild_id == guild_id).first()
    if not todo:
        raise HTTPException(404, "Aufgabe nicht gefunden.")
    db.delete(todo)
    db.commit()
    return {"ok": True}


@app.post("/api/users/{user_id}/balance")
def adjust_balance(user_id: str, guild_id: str, delta: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    target = db.query(User).filter(User.id == user_id, User.guild_id == guild_id).first()
    if not target:
        discord_id = user_id.split(":", 1)[1] if ":" in user_id else user_id
        guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
        member = guild.get_member(int(discord_id)) if guild else None
        if not member:
            raise HTTPException(404, "Benutzer nicht gefunden")
        target = get_or_create_user_by_id(db, guild_id, discord_id, member.display_name)
    target.balance += delta
    log(db, guild_id, "system", f"Guthaben von {target.username} um {delta} ₡ angepasst")
    db.commit()
    return {"ok": True, "balance": target.balance}


# ---------- AFK-System ----------
@app.get("/api/afk")
def afk_list(guild_id: str, db: Session = Depends(get_db)):
    users_ = db.query(User).filter(User.guild_id == guild_id, User.afk_reason.isnot(None)).all()
    return [
        {"id": u.id, "name": u.username, "reason": u.afk_reason,
         "since": u.afk_since.isoformat() if u.afk_since else None}
        for u in users_
    ]


@app.get("/api/afk/me")
def afk_me(guild_id: str, db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    return {"reason": me.afk_reason, "since": me.afk_since.isoformat() if me.afk_since else None}


@app.post("/api/afk/set")
def afk_set(guild_id: str, grund: str = "Kein Grund angegeben", db: Session = Depends(get_db), user=Depends(require_guild_access)):
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    me.afk_reason = grund
    me.afk_since = datetime.now(timezone.utc)
    log(db, guild_id, "afk", f"{me.username} ist jetzt AFK (über Dashboard): {grund}")
    db.commit()
    return {"ok": True}


@app.post("/api/afk/clear")
def afk_clear(guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    me.afk_reason = None
    me.afk_since = None
    log(db, guild_id, "afk", f"{me.username} hat AFK beendet (über Dashboard)")
    db.commit()
    return {"ok": True}


# ---------- Sicherheit ----------
def simplify_user_agent(ua: str) -> str:
    ua = ua or ""
    browser = "Unbekannter Browser"
    if "Chrome" in ua and "Edg" not in ua:
        browser = "Chrome"
    elif "Firefox" in ua:
        browser = "Firefox"
    elif "Safari" in ua and "Chrome" not in ua:
        browser = "Safari"
    elif "Edg" in ua:
        browser = "Edge"
    os_name = "Unbekanntes Gerät"
    if "Windows" in ua:
        os_name = "Windows"
    elif "Mac OS" in ua:
        os_name = "macOS"
    elif "Android" in ua:
        os_name = "Android"
    elif "iPhone" in ua or "iPad" in ua:
        os_name = "iOS"
    elif "Linux" in ua:
        os_name = "Linux"
    return f"{browser} · {os_name}"


def mask_ip(ip: str) -> str:
    if not ip:
        return "unbekannt"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.xx.xx"
    return ip[:8] + "…"


@app.get("/api/security/sessions")
def security_sessions(guild_id: str, db: Session = Depends(get_db), user=Depends(require_user)):
    # Sicherstellen, dass der eigene Nutzer-Datensatz für diesen Server existiert,
    # damit die eigene Sitzung auf einem frisch verbundenen Server sofort erscheint.
    get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    # Logins sind serverübergreifend, daher zeigen wir hier nur die Logins von
    # Mitgliedern des aktuell ausgewählten Servers.
    member_ids = {u.discord_id for u in db.query(User).filter(User.guild_id == guild_id).all()}
    sessions = db.query(LoginSession).filter(LoginSession.revoked != "yes").order_by(LoginSession.created_at.desc()).limit(100).all()
    filtered = [s for s in sessions if s.user_id in member_ids][:20]
    return [
        {"id": s.id, "user": s.username, "device": simplify_user_agent(s.user_agent), "ip": mask_ip(s.ip),
         "time": s.created_at.isoformat(), "isMine": s.user_id == user["sub"]}
        for s in filtered
    ]


@app.post("/api/security/sessions/{session_id}/revoke")
def revoke_session(session_id: int, guild_id: str, db: Session = Depends(get_db), user=Depends(require_user)):
    session = db.query(LoginSession).get(session_id)
    if not session:
        raise HTTPException(404, "Sitzung nicht gefunden")
    if session.user_id != user["sub"]:
        raise HTTPException(403, "Du kannst nur deine eigenen Sitzungen beenden.")
    session.revoked = "yes"
    log(db, guild_id, "system", f"{session.username} hat eine Sitzung beendet")
    db.commit()
    return {"ok": True}


@app.get("/api/security/overview")
def security_overview(guild_id: str, db: Session = Depends(get_db)):
    admin_count = db.query(func.count(User.id)).filter(User.guild_id == guild_id, User.role.in_(["Admin", "Owner"])).scalar() or 0
    member_ids = {u.discord_id for u in db.query(User).filter(User.guild_id == guild_id).all()}
    logins = db.query(LoginSession).filter(LoginSession.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)).all()
    login_count_24h = len([l for l in logins if l.user_id in member_ids])
    return {"admin_count": admin_count, "logins_24h": login_count_24h}


# ---------- Einstellungen ----------
@app.get("/api/settings")
def get_settings(guild_id: str, db: Session = Depends(get_db)):
    prefix = f"{guild_id}:"
    rows = db.query(Setting).filter(Setting.key.like(f"{prefix}%")).all()
    return {s.key[len(prefix):]: s.value for s in rows}


@app.post("/api/settings")
def update_settings(guild_id: str, payload: dict, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    for key, value in payload.items():
        full_key = gkey(guild_id, key)
        setting = db.query(Setting).get(full_key)
        if setting:
            setting.value = str(value)
        else:
            db.add(Setting(key=full_key, value=str(value)))
    db.commit()
    log(db, guild_id, "system", f"Einstellungen geändert: {', '.join(payload.keys())}")
    db.commit()
    return {"ok": True}


# ---------- Module ----------
@app.get("/api/modules")
def get_modules(guild_id: str, db: Session = Depends(get_db)):
    """Gibt für jedes bekannte Modul zurück, ob es auf diesem Server aktiv ist."""
    return {
        key: {"name": name, "enabled": is_module_enabled(db, guild_id, key)}
        for key, name in MODULE_NAMES.items()
    }


@app.post("/api/modules/{module_key}")
def update_module(module_key: str, payload: dict, guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    """Schaltet ein einzelnes Modul für den Server an oder aus. Erwartet {"enabled": true/false}."""
    if module_key not in MODULE_NAMES:
        raise HTTPException(404, "Unbekanntes Modul.")
    enabled = bool(payload.get("enabled", True))
    full_key = gkey(guild_id, f"modul_{module_key}")
    setting = db.query(Setting).get(full_key)
    value = "ja" if enabled else "nein"
    if setting:
        setting.value = value
    else:
        db.add(Setting(key=full_key, value=value))
    log(db, guild_id, "system", f"{MODULE_NAMES[module_key]} wurde {'aktiviert' if enabled else 'deaktiviert'}")
    db.commit()
    return {"ok": True, "module": module_key, "enabled": enabled}
