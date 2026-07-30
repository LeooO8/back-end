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
import time
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Literal
import httpx
import jwt
import discord
from discord import app_commands
from discord.ext import commands, tasks
from fastapi import FastAPI, Depends, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db, init_db, SessionLocal
from models import User, Transaction, ShopItem, DutyFraction, Giveaway, LogEntry, Setting, LoginSession

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


# =========================================================
# TEIL 1: DER DISCORD-BOT
# =========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
BOT_START_TIME = datetime.now(timezone.utc)


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
        db.add(LogEntry(guild_id=str(guild_id), type="system", text=f"{username} wurde neu angelegt (Startguthaben {start} ₡)"))
        db.commit()
    user.last_seen = datetime.now(timezone.utc)
    db.commit()
    return user


def get_or_create_user(db, member: discord.Member) -> User:
    return get_or_create_user_by_id(db, str(member.guild.id), str(member.id), member.display_name)


def log(db: Session, guild_id: str, type_: str, text: str):
    db.add(LogEntry(guild_id=str(guild_id), type=type_, text=text))


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
    except Exception as e:
        print(f"Fehler beim Synchronisieren der Slash-Commands: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    """Neues Mitglied bekommt automatisch das eingestellte Startguthaben + Willkommensnachricht."""
    db = SessionLocal()
    try:
        user = get_or_create_user(db, member)

        channel = member.guild.system_channel
        if channel is None:
            for ch in member.guild.text_channels:
                perms = ch.permissions_for(member.guild.me)
                if perms.send_messages:
                    channel = ch
                    break
        if channel:
            try:
                await channel.send(
                    f"👋 Willkommen {member.mention}! Du hast ein Startguthaben von **{user.balance:,} ₡** erhalten.".replace(",", ".")
                )
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
    db.add(LogEntry(guild_id=giveaway.guild_id, type="system",
                     text=f"Giveaway '{giveaway.prize}' beendet — Gewinner: {winner_name or 'niemand teilgenommen'}"))
    db.commit()

    if giveaway.channel_id and bot.is_ready():
        try:
            channel = bot.get_channel(int(giveaway.channel_id)) or await bot.fetch_channel(int(giveaway.channel_id))
            if winner_discord_id:
                await channel.send(f"🎉 Das Giveaway für **{giveaway.prize}** ist vorbei! Herzlichen Glückwunsch <@{winner_discord_id}>!")
            else:
                await channel.send(f"🎉 Das Giveaway für **{giveaway.prize}** ist vorbei — leider hat niemand teilgenommen.")
        except Exception as e:
            print(f"Konnte Giveaway-Ergebnis nicht senden: {e}")


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
    ends_at = datetime.now(timezone.utc) + timedelta(minutes=dauer_minuten)
    embed = discord.Embed(
        title="🎉 Giveaway!",
        description=f"Preis: **{preis}**\nReagiere mit {GIVEAWAY_EMOJI}, um teilzunehmen!\nEndet: <t:{int(ends_at.timestamp())}:R>",
        color=0xF2B705,
    )
    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    await message.add_reaction(GIVEAWAY_EMOJI)

    db = SessionLocal()
    try:
        g = Giveaway(guild_id=str(interaction.guild_id), prize=preis, status="aktiv", ends_at=ends_at,
                     channel_id=str(interaction.channel.id), message_id=str(message.id), participants="")
        db.add(g)
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="system",
                         text=f"{interaction.user.display_name} hat ein Giveaway gestartet: {preis}"))
        db.commit()
    finally:
        db.close()


@bot.tree.command(name="giveaway_beenden", description="[Admin] Beendet ein Giveaway sofort und lost aus")
@app_commands.describe(giveaway_id="Die ID des Giveaways (siehe Dashboard)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_end_cmd(interaction: discord.Interaction, giveaway_id: int):
    db = SessionLocal()
    try:
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
        user = get_or_create_user(db, interaction.user)
        user.afk_reason = grund
        user.afk_since = datetime.now(timezone.utc)
        db.commit()
        await interaction.response.send_message(f"😴 {interaction.user.mention} ist jetzt AFK: {grund}")
    finally:
        db.close()


@bot.tree.command(name="kontostand", description="Zeigt deinen aktuellen Kontostand")
async def balance_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        user = get_or_create_user(db, interaction.user)
        await interaction.response.send_message(f"💰 Dein Kontostand: **{user.balance:,} ₡**".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="ueberweisen", description="Überweist Guthaben an ein anderes Mitglied")
@app_commands.describe(empfaenger="An wen überwiesen werden soll", betrag="Wie viel überwiesen werden soll")
async def transfer_cmd(interaction: discord.Interaction, empfaenger: discord.Member, betrag: int):
    if betrag <= 0:
        return await interaction.response.send_message("Der Betrag muss positiv sein.", ephemeral=True)
    db = SessionLocal()
    try:
        sender = get_or_create_user(db, interaction.user)
        receiver = get_or_create_user(db, empfaenger)
        if sender.balance < betrag:
            return await interaction.response.send_message("❌ Nicht genug Guthaben.", ephemeral=True)
        sender.balance -= betrag
        receiver.balance += betrag
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=sender.username, to_user=receiver.username, amount=betrag, type="Überweisung"))
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="bank", text=f"{sender.username} überwies {betrag} ₡ an {receiver.username}"))
        db.commit()
        await interaction.response.send_message(f"✅ {betrag:,} ₡ an {empfaenger.mention} überwiesen.".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="shop", description="Zeigt alle Artikel im Shop")
async def shop_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        items = db.query(ShopItem).filter(ShopItem.guild_id == str(interaction.guild_id)).all()
        if not items:
            return await interaction.response.send_message("Der Shop ist noch leer.")
        text = "\n".join(f"**{i.name}** — {i.price:,} ₡".replace(",", ".") for i in items)
        await interaction.response.send_message(f"🛒 **Shop-Artikel:**\n{text}")
    finally:
        db.close()


@bot.tree.command(name="kaufen", description="Kauft einen Artikel aus dem Shop")
@app_commands.describe(artikel="Name des Artikels (oder ein Teil davon)")
async def buy_cmd(interaction: discord.Interaction, artikel: str):
    db = SessionLocal()
    try:
        item = db.query(ShopItem).filter(
            ShopItem.guild_id == str(interaction.guild_id), ShopItem.name.ilike(f"%{artikel}%")
        ).first()
        if not item:
            return await interaction.response.send_message("Artikel nicht gefunden.", ephemeral=True)
        user = get_or_create_user(db, interaction.user)
        if user.balance < item.price:
            return await interaction.response.send_message("❌ Nicht genug Guthaben.", ephemeral=True)
        user.balance -= item.price
        item.sold += 1
        db.add(Transaction(guild_id=str(interaction.guild_id), from_user=user.username, to_user="Shop", amount=item.price, type="Kauf"))
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="shop", text=f"{user.username} kaufte '{item.name}'"))
        db.commit()
        await interaction.response.send_message(f"✅ Du hast **{item.name}** gekauft.")
    finally:
        db.close()


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
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="system", text=f"{user.username} hat als {job} gearbeitet und {verdienst} ₡ verdient"))
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
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="system", text=f"{user.username} hat den täglichen Bonus abgeholt"))
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
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="system", text=f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ gegeben"))
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
        db.add(LogEntry(guild_id=str(interaction.guild_id), type="system", text=f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ abgezogen"))
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
            f"💰 Kontostand von {mitglied.mention}: **{target.balance:,} ₡**".replace(",", "."), ephemeral=True
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
        status = "im Dienst" if fraction.on_duty > 0 else "außer Dienst"
        embed = discord.Embed(
            title=f"👮 {fraction.name}",
            description=f"Status geändert von **{changed_by}**",
            color=0x4ADE80 if fraction.on_duty > 0 else 0x7C8798,
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Im Dienst", value=f"{fraction.on_duty}/{fraction.total}", inline=True)
        await channel.send(embed=embed)
    except Exception as e:
        print(f"Konnte Dienst-Embed nicht senden: {e}")


@bot.tree.command(name="dienst", description="Dienst antreten oder abtreten für eine Fraktion")
@app_commands.describe(fraktion="Welche Fraktion")
async def duty_cmd(
    interaction: discord.Interaction,
    fraktion: Literal["Polizei", "Feuerwehr", "Notfallsanitäter", "Rettungsdienst", "LKW", "Bus"],
):
    db = SessionLocal()
    try:
        guild_id = str(interaction.guild_id)
        user = get_or_create_user(db, interaction.user)

        if user.on_duty_fraction and user.on_duty_fraction.lower() != fraktion.lower():
            return await interaction.response.send_message(
                f"❌ Du bist gerade bei **{user.on_duty_fraction}** im Dienst. "
                f"Geh dort zuerst mit /dienst außer Dienst, bevor du bei **{fraktion}** antrittst.",
                ephemeral=True,
            )

        f = db.query(DutyFraction).filter(DutyFraction.guild_id == guild_id, DutyFraction.name.ilike(fraktion)).first()
        if not f:
            f = DutyFraction(guild_id=guild_id, name=fraktion, total=10)
            db.add(f)
            db.commit()

        if user.on_duty_fraction:
            user.on_duty_fraction = None
            f.on_duty = max(0, f.on_duty - 1)
            status = "außer Dienst"
        else:
            if f.total and f.on_duty >= f.total:
                return await interaction.response.send_message(
                    f"❌ Bei **{f.name}** sind schon alle Plätze belegt ({f.on_duty}/{f.total}).", ephemeral=True
                )
            user.on_duty_fraction = fraktion
            f.on_duty += 1
            status = "im Dienst"

        db.add(LogEntry(guild_id=guild_id, type="dienst", text=f"{interaction.user.display_name} ist jetzt {status} bei {f.name}"))
        db.commit()
        await interaction.response.send_message(f"👮 {f.name}: **{status}** ({f.on_duty}/{f.total}).")
        await post_duty_embed(f, interaction.user.display_name)
    finally:
        db.close()


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


@app.on_event("startup")
async def startup():
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
    db.add(LogEntry(guild_id=None, type="login", text=f"{discord_user['username']} hat sich über Discord angemeldet"))

    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unbekannt").split(",")[0].strip()
    user_agent = request.headers.get("user-agent", "unbekannt")
    db.add(LoginSession(guild_id=None, user_id=discord_user["id"], username=discord_user["username"], ip=client_ip, user_agent=user_agent))
    db.commit()
    db.close()

    session_token = jwt.encode(
        {"sub": discord_user["id"], "username": discord_user["username"], "avatar": discord_user.get("avatar"),
         "exp": int(time.time()) + 60 * 60 * 24 * 7},
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
def logout(response: Response):
    response.delete_cookie("session", samesite="none", secure=True)
    return {"ok": True}


def require_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sitzung abgelaufen")


def is_guild_member(guild_id: str, discord_user_id: str) -> bool:
    """Prüft (über den Bot-Cache), ob der Nutzer Mitglied des angegebenen Servers ist."""
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return False
    member = guild.get_member(int(discord_user_id))
    return member is not None


def require_guild_access(guild_id: str, user=Depends(require_user)):
    """Stellt sicher, dass der eingeloggte Nutzer wirklich Mitglied dieses Servers ist,
    bevor er dort etwas verändern darf."""
    if not is_guild_member(guild_id, user["sub"]):
        raise HTTPException(403, "Du bist kein Mitglied dieses Servers.")
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


# ---------- Übersicht ----------
@app.get("/api/overview")
def overview(guild_id: str, db: Session = Depends(get_db)):
    total_balance = db.query(func.sum(User.balance)).filter(User.guild_id == guild_id).scalar() or 0
    member_count = db.query(func.count(User.id)).filter(User.guild_id == guild_id).scalar() or 0
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


# ---------- Bank ----------
@app.get("/api/bank/accounts")
def bank_accounts(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": u.id, "name": u.username, "balance": u.balance, "role": u.role}
            for u in db.query(User).filter(User.guild_id == guild_id).order_by(User.balance.desc()).all()]


@app.get("/api/bank/transactions")
def bank_transactions(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": t.id, "from": t.from_user, "to": t.to_user, "amount": t.amount,
             "type": t.type, "time": t.created_at.isoformat()}
            for t in db.query(Transaction).filter(Transaction.guild_id == guild_id).order_by(Transaction.created_at.desc()).limit(50).all()]


@app.post("/api/bank/transfer")
def bank_transfer_dashboard(guild_id: str, empfaenger_id: str, betrag: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    if betrag <= 0:
        raise HTTPException(400, "Der Betrag muss positiv sein.")
    sender = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    receiver = db.query(User).get(empfaenger_id)
    if not receiver:
        raise HTTPException(404, "Empfänger nicht gefunden")
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


# ---------- Shop ----------
@app.get("/api/shop/items")
def shop_items(guild_id: str, db: Session = Depends(get_db)):
    return [{"id": i.id, "name": i.name, "category": i.category, "price": i.price, "sold": i.sold}
            for i in db.query(ShopItem).filter(ShopItem.guild_id == guild_id).all()]


@app.post("/api/shop/items")
def create_item(guild_id: str, name: str, category: str, price: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    item = ShopItem(guild_id=guild_id, name=name, category=category, price=price)
    db.add(item)
    log(db, guild_id, "shop", f"Neuer Artikel erstellt: {name} ({price} ₡)")
    db.commit()
    return {"ok": True, "id": item.id}


@app.post("/api/shop/items/{item_id}")
def update_item(item_id: int, guild_id: str, name: str, category: str, price: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    item = db.query(ShopItem).filter(ShopItem.id == item_id, ShopItem.guild_id == guild_id).first()
    if not item:
        raise HTTPException(404, "Artikel nicht gefunden")
    item.name, item.category, item.price = name, category, price
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
    f = db.query(DutyFraction).filter(DutyFraction.id == fraction_id, DutyFraction.guild_id == guild_id).first()
    if not f:
        raise HTTPException(404, "Fraktion nicht gefunden")

    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))

    if me.on_duty_fraction and me.on_duty_fraction.lower() != f.name.lower():
        raise HTTPException(400, f"Du bist bereits bei {me.on_duty_fraction} im Dienst. Geh dort zuerst außer Dienst.")

    if me.on_duty_fraction:
        me.on_duty_fraction = None
        f.on_duty = max(0, f.on_duty - 1)
        status = "außer Dienst"
    else:
        if f.total and f.on_duty >= f.total:
            raise HTTPException(400, f"Bei {f.name} sind schon alle Plätze belegt ({f.on_duty}/{f.total}).")
        me.on_duty_fraction = f.name
        f.on_duty += 1
        status = "im Dienst"

    log(db, guild_id, "dienst", f"{me.username} ist jetzt {status} bei {f.name}")
    db.commit()
    await post_duty_embed(f, me.username)
    return {"ok": True, "onDutyFraction": me.on_duty_fraction}


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
        title="🎉 Giveaway!",
        description=f"Preis: **{preis}**\nReagiere mit {GIVEAWAY_EMOJI}, um teilzunehmen!\nEndet: <t:{int(ends_at.timestamp())}:R>",
        color=0xF2B705,
    )
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
        "member_count": db.query(func.count(User.id)).filter(User.guild_id == guild_id).scalar() or 0,
        "active_users_7d": active_users,
        "total_balance": db.query(func.sum(User.balance)).filter(User.guild_id == guild_id).scalar() or 0,
        "shop_sales": db.query(func.sum(ShopItem.sold)).filter(ShopItem.guild_id == guild_id).scalar() or 0,
        "duty_hours_today": db.query(func.sum(DutyFraction.hours_today)).filter(DutyFraction.guild_id == guild_id).scalar() or 0,
        "giveaway_count": db.query(func.count(Giveaway.id)).filter(Giveaway.guild_id == guild_id).scalar() or 0,
        "weekly_activity": weekly_activity,
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
    return [{"id": u.id, "name": u.username, "role": u.role, "balance": u.balance,
             "status": compute_status(u.last_seen), "joined": u.joined_at.isoformat()}
            for u in db.query(User).filter(User.guild_id == guild_id).all()]


@app.post("/api/users/{user_id}/role")
def update_role(user_id: str, guild_id: str, role: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    target = db.query(User).filter(User.id == user_id, User.guild_id == guild_id).first()
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    target.role = role
    log(db, guild_id, "system", f"Rolle von {target.username} geändert zu {role}")
    db.commit()
    return {"ok": True}


@app.post("/api/users/{user_id}/balance")
def adjust_balance(user_id: str, guild_id: str, delta: int, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    target = db.query(User).filter(User.id == user_id, User.guild_id == guild_id).first()
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
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
    log(db, guild_id, "system", f"{me.username} ist jetzt AFK (über Dashboard): {grund}")
    db.commit()
    return {"ok": True}


@app.post("/api/afk/clear")
def afk_clear(guild_id: str, db: Session = Depends(get_db), user=Depends(require_guild_access)):
    me = get_or_create_user_by_id(db, guild_id, user["sub"], user.get("username", "Dashboard"))
    me.afk_reason = None
    me.afk_since = None
    log(db, guild_id, "system", f"{me.username} hat AFK beendet (über Dashboard)")
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
    # Logins sind serverübergreifend, daher zeigen wir hier nur die Logins von
    # Mitgliedern des aktuell ausgewählten Servers.
    member_ids = {u.discord_id for u in db.query(User).filter(User.guild_id == guild_id).all()}
    sessions = db.query(LoginSession).order_by(LoginSession.created_at.desc()).limit(100).all()
    filtered = [s for s in sessions if s.user_id in member_ids][:20]
    return [
        {"user": s.username, "device": simplify_user_agent(s.user_agent), "ip": mask_ip(s.ip),
         "time": s.created_at.isoformat()}
        for s in filtered
    ]


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
    log(db, guild_id, "system", f"Einstellungen geändert: {', '.join(payload.keys())}")
    db.commit()
    return {"ok": True}
