"""
Ein einziger Service, der ZWEI Dinge gleichzeitig macht:
1. Den echten Discord-Bot (discord.py) - reagiert auf Befehle im Server
2. Die Dashboard-API (FastAPI) - liefert Daten fürs Web-Dashboard

Beide teilen sich dieselbe Datenbank, laufen im selben Prozess und werden
zusammen als EIN Dienst gehostet (z.B. auf Railway). Das hält die
Einrichtung für dich so einfach wie möglich.
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
from models import User, Transaction, ShopItem, DutyFraction, Giveaway, LogEntry, Setting

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

# =========================================================
# TEIL 1: DER DISCORD-BOT
# =========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
BOT_START_TIME = datetime.now(timezone.utc)


def get_starting_balance(db) -> int:
    setting = db.query(Setting).get("startguthaben")
    try:
        return int(setting.value) if setting and setting.value else 500
    except (TypeError, ValueError):
        return 500


def get_or_create_user_by_id(db, user_id: str, username: str) -> User:
    user = db.query(User).get(user_id)
    if not user:
        start = get_starting_balance(db)
        user = User(id=user_id, username=username, balance=start)
        db.add(user)
        db.add(LogEntry(type="system", text=f"{username} wurde neu angelegt (Startguthaben {start} ₡)"))
        db.commit()
    return user


def get_or_create_user(db, member: discord.Member) -> User:
    return get_or_create_user_by_id(db, str(member.id), member.display_name)


@bot.event
async def on_ready():
    print(f"Bot eingeloggt als {bot.user}")
    try:
        # Einmalig aufräumen: zuvor global bei Discord angemeldete Befehle entfernen,
        # damit sie sich nicht mit den Server-spezifischen Befehlen doppeln.
        # (Nutzt einen direkten API-Aufruf, damit die eigentliche Befehlsliste im Bot unangetastet bleibt.)
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
    """Sobald der Bot einem neuen Server beitritt, sofort die Slash-Commands dort anmelden."""
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
    winner_id = random.choice(ids) if ids else None
    winner_name = None
    if winner_id:
        winner_user = db.query(User).get(winner_id)
        winner_name = winner_user.username if winner_user else winner_id
    giveaway.status = "beendet"
    giveaway.winner = winner_name
    db.add(LogEntry(type="system", text=f"Giveaway '{giveaway.prize}' beendet — Gewinner: {winner_name or 'niemand teilgenommen'}"))
    db.commit()

    if giveaway.channel_id and bot.is_ready():
        try:
            channel = bot.get_channel(int(giveaway.channel_id)) or await bot.fetch_channel(int(giveaway.channel_id))
            if winner_id:
                await channel.send(f"🎉 Das Giveaway für **{giveaway.prize}** ist vorbei! Herzlichen Glückwunsch <@{winner_id}>!")
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
        g = Giveaway(prize=preis, status="aktiv", ends_at=ends_at, channel_id=str(interaction.channel.id), message_id=str(message.id), participants="")
        db.add(g)
        db.add(LogEntry(type="system", text=f"{interaction.user.display_name} hat ein Giveaway gestartet: {preis}"))
        db.commit()
    finally:
        db.close()


@bot.tree.command(name="giveaway_beenden", description="[Admin] Beendet ein Giveaway sofort und lost aus")
@app_commands.describe(giveaway_id="Die ID des Giveaways (siehe Dashboard)")
@app_commands.checks.has_permissions(administrator=True)
async def giveaway_end_cmd(interaction: discord.Interaction, giveaway_id: int):
    db = SessionLocal()
    try:
        g = db.query(Giveaway).get(giveaway_id)
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
        g = db.query(Giveaway).get(giveaway_id)
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
    if message.author.bot:
        return

    db = SessionLocal()
    try:
        # Eigenes AFK entfernen, sobald man wieder schreibt
        me = db.query(User).get(str(message.author.id))
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
            target = db.query(User).get(str(mentioned.id))
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
        db.add(Transaction(from_user=sender.username, to_user=receiver.username, amount=betrag, type="Überweisung"))
        db.add(LogEntry(type="bank", text=f"{sender.username} überwies {betrag} ₡ an {receiver.username}"))
        db.commit()
        await interaction.response.send_message(f"✅ {betrag:,} ₡ an {empfaenger.mention} überwiesen.".replace(",", "."))
    finally:
        db.close()


@bot.tree.command(name="shop", description="Zeigt alle Artikel im Shop")
async def shop_cmd(interaction: discord.Interaction):
    db = SessionLocal()
    try:
        items = db.query(ShopItem).all()
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
        item = db.query(ShopItem).filter(ShopItem.name.ilike(f"%{artikel}%")).first()
        if not item:
            return await interaction.response.send_message("Artikel nicht gefunden.", ephemeral=True)
        user = get_or_create_user(db, interaction.user)
        if user.balance < item.price:
            return await interaction.response.send_message("❌ Nicht genug Guthaben.", ephemeral=True)
        user.balance -= item.price
        item.sold += 1
        db.add(Transaction(from_user=user.username, to_user="Shop", amount=item.price, type="Kauf"))
        db.add(LogEntry(type="shop", text=f"{user.username} kaufte '{item.name}'"))
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
        db.add(Transaction(from_user=f"Job:{job}", to_user=user.username, amount=verdienst, type="Arbeit"))
        db.add(LogEntry(type="system", text=f"{user.username} hat als {job} gearbeitet und {verdienst} ₡ verdient"))
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
        db.add(Transaction(from_user="Täglicher Bonus", to_user=user.username, amount=DAILY_AMOUNT, type="Daily"))
        db.add(LogEntry(type="system", text=f"{user.username} hat den täglichen Bonus abgeholt"))
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
        db.add(Transaction(from_user=f"Admin:{interaction.user.display_name}", to_user=target.username, amount=betrag, type="Admin-Gutschrift"))
        db.add(LogEntry(type="system", text=f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ gegeben"))
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
        db.add(Transaction(from_user=target.username, to_user=f"Admin:{interaction.user.display_name}", amount=betrag, type="Admin-Abzug"))
        db.add(LogEntry(type="system", text=f"{interaction.user.display_name} hat {mitglied.display_name} {betrag} ₡ abgezogen"))
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
        user = get_or_create_user(db, interaction.user)

        # Ist der Nutzer schon bei einer ANDEREN Fraktion im Dienst?
        if user.on_duty_fraction and user.on_duty_fraction.lower() != fraktion.lower():
            return await interaction.response.send_message(
                f"❌ Du bist gerade bei **{user.on_duty_fraction}** im Dienst. "
                f"Geh dort zuerst mit /dienst außer Dienst, bevor du bei **{fraktion}** antrittst.",
                ephemeral=True,
            )

        f = db.query(DutyFraction).filter(DutyFraction.name.ilike(fraktion)).first()
        if not f:
            f = DutyFraction(name=fraktion, total=10)
            db.add(f)
            db.commit()

        if user.on_duty_fraction:
            # Nutzer war bei genau dieser Fraktion im Dienst -> jetzt abtreten
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

        db.add(LogEntry(type="dienst", text=f"{interaction.user.display_name} ist jetzt {status} bei {f.name}"))
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


def log(db: Session, type_: str, text: str):
    db.add(LogEntry(type=type_, text=text))


@app.on_event("startup")
async def startup():
    init_db()
    # Bot im Hintergrund starten, damit er parallel zur API läuft
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
async def callback(code: str):
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
    role = "Mitglied"
    existing = db.query(User).get(discord_user["id"])
    if existing:
        role = existing.role
    db.add(LogEntry(type="login", text=f"{discord_user['username']} hat sich über Discord angemeldet"))
    db.commit()
    db.close()

    session_token = jwt.encode(
        {"sub": discord_user["id"], "username": discord_user["username"], "role": role,
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
    response.delete_cookie("session")
    return {"ok": True}


def require_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "Nicht angemeldet")
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sitzung abgelaufen")


def require_admin(user=Depends(require_user)):
    if user.get("role") not in ("Owner", "Admin"):
        raise HTTPException(403, "Keine Berechtigung")
    return user


# ---------- Übersicht ----------
@app.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    total_balance = db.query(func.sum(User.balance)).scalar() or 0
    member_count = db.query(func.count(User.id)).scalar() or 0
    on_duty = db.query(func.sum(DutyFraction.on_duty)).scalar() or 0
    recent = db.query(LogEntry).order_by(LogEntry.created_at.desc()).limit(5).all()
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
def bank_accounts(db: Session = Depends(get_db)):
    return [{"id": u.id, "name": u.username, "balance": u.balance, "role": u.role}
            for u in db.query(User).order_by(User.balance.desc()).all()]


@app.get("/api/bank/transactions")
def bank_transactions(db: Session = Depends(get_db)):
    return [{"id": t.id, "from": t.from_user, "to": t.to_user, "amount": t.amount,
             "type": t.type, "time": t.created_at.isoformat()}
            for t in db.query(Transaction).order_by(Transaction.created_at.desc()).limit(50).all()]


@app.post("/api/bank/transfer")
def bank_transfer_dashboard(empfaenger_id: str, betrag: int, db: Session = Depends(get_db), user=Depends(require_user)):
    if betrag <= 0:
        raise HTTPException(400, "Der Betrag muss positiv sein.")
    sender = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))
    receiver = db.query(User).get(empfaenger_id)
    if not receiver:
        raise HTTPException(404, "Empfänger nicht gefunden")
    if sender.id == receiver.id:
        raise HTTPException(400, "Du kannst nicht an dich selbst überweisen.")
    if sender.balance < betrag:
        raise HTTPException(400, "Nicht genug Guthaben.")
    sender.balance -= betrag
    receiver.balance += betrag
    db.add(Transaction(from_user=sender.username, to_user=receiver.username, amount=betrag, type="Überweisung"))
    log(db, "bank", f"{sender.username} überwies {betrag} ₡ an {receiver.username} (über Dashboard)")
    db.commit()
    return {"ok": True, "balance": sender.balance}


# ---------- Shop ----------
@app.get("/api/shop/items")
def shop_items(db: Session = Depends(get_db)):
    return [{"id": i.id, "name": i.name, "category": i.category, "price": i.price, "sold": i.sold}
            for i in db.query(ShopItem).all()]


@app.post("/api/shop/items")
def create_item(name: str, category: str, price: int, db: Session = Depends(get_db), user=Depends(require_user)):
    item = ShopItem(name=name, category=category, price=price)
    db.add(item)
    log(db, "shop", f"Neuer Artikel erstellt: {name} ({price} ₡)")
    db.commit()
    return {"ok": True, "id": item.id}


@app.post("/api/shop/items/{item_id}")
def update_item(item_id: int, name: str, category: str, price: int, db: Session = Depends(get_db), user=Depends(require_user)):
    item = db.query(ShopItem).get(item_id)
    if not item:
        raise HTTPException(404, "Artikel nicht gefunden")
    item.name, item.category, item.price = name, category, price
    log(db, "shop", f"Artikel bearbeitet: {name} ({price} ₡)")
    db.commit()
    return {"ok": True}


@app.delete("/api/shop/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    item = db.query(ShopItem).get(item_id)
    if not item:
        raise HTTPException(404, "Artikel nicht gefunden")
    log(db, "shop", f"Artikel gelöscht: {item.name}")
    db.delete(item)
    db.commit()
    return {"ok": True}


# ---------- Dienstsystem ----------
@app.get("/api/dienst")
def dienst(db: Session = Depends(get_db)):
    return [{"id": d.id, "fraction": d.name, "onDuty": d.on_duty, "total": d.total,
             "hoursToday": d.hours_today, "channelId": d.channel_id}
            for d in db.query(DutyFraction).all()]


@app.get("/api/dienst/me")
def dienst_me(db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))
    return {"onDutyFraction": me.on_duty_fraction}


@app.post("/api/dienst/{fraction_id}/toggle")
async def toggle_dienst(fraction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    f = db.query(DutyFraction).get(fraction_id)
    if not f:
        raise HTTPException(404, "Fraktion nicht gefunden")

    me = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))

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

    log(db, "dienst", f"{me.username} ist jetzt {status} bei {f.name}")
    db.commit()
    await post_duty_embed(f, me.username)
    return {"ok": True, "onDutyFraction": me.on_duty_fraction}


@app.post("/api/dienst")
def create_fraction(name: str, total: int = 5, channel_id: str | None = None,
                     db: Session = Depends(get_db), user=Depends(require_user)):
    f = DutyFraction(name=name, total=total, channel_id=channel_id or None)
    db.add(f)
    log(db, "system", f"Neue Fraktion angelegt: {name} ({total} Plätze)")
    db.commit()
    return {"ok": True, "id": f.id}


@app.delete("/api/dienst/{fraction_id}")
def delete_fraction(fraction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    f = db.query(DutyFraction).get(fraction_id)
    if not f:
        raise HTTPException(404, "Fraktion nicht gefunden")
    db.delete(f)
    log(db, "system", f"Fraktion gelöscht: {f.name}")
    db.commit()
    return {"ok": True}



# ---------- Giveaways ----------
@app.get("/api/giveaways")
def giveaways(db: Session = Depends(get_db)):
    return [{"id": g.id, "prize": g.prize, "entries": g.entries, "status": g.status,
             "winner": g.winner, "ends": g.ends_at.isoformat() if g.ends_at else None}
            for g in db.query(Giveaway).all()]


@app.post("/api/giveaways")
async def create_giveaway_dashboard(preis: str, dauer_minuten: int, channel_id: str, db: Session = Depends(get_db), user=Depends(require_user)):
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

    g = Giveaway(prize=preis, status="aktiv", ends_at=ends_at, channel_id=str(channel.id), message_id=str(message.id), participants="")
    db.add(g)
    log(db, "system", f"Giveaway über Dashboard gestartet: {preis}")
    db.commit()
    return {"ok": True, "id": g.id}


@app.post("/api/giveaways/{giveaway_id}/end")
async def end_giveaway_dashboard(giveaway_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    g = db.query(Giveaway).get(giveaway_id)
    if not g or g.status != "aktiv":
        raise HTTPException(400, "Giveaway nicht gefunden oder schon beendet.")
    await draw_giveaway_winner(db, g)
    return {"ok": True}


@app.post("/api/giveaways/{giveaway_id}/reroll")
async def reroll_giveaway_dashboard(giveaway_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    g = db.query(Giveaway).get(giveaway_id)
    if not g:
        raise HTTPException(404, "Giveaway nicht gefunden.")
    await draw_giveaway_winner(db, g)
    return {"ok": True}


# ---------- Logs ----------
@app.get("/api/logs")
def logs(type: str | None = None, db: Session = Depends(get_db)):
    q = db.query(LogEntry).order_by(LogEntry.created_at.desc())
    if type and type != "alle":
        q = q.filter(LogEntry.type == type)
    return [{"id": l.id, "type": l.type, "text": l.text, "time": l.created_at.isoformat()} for l in q.limit(200).all()]


# ---------- Statistiken ----------
@app.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    return {
        "member_count": db.query(func.count(User.id)).scalar() or 0,
        "total_balance": db.query(func.sum(User.balance)).scalar() or 0,
        "shop_sales": db.query(func.sum(ShopItem.sold)).scalar() or 0,
        "duty_hours_today": db.query(func.sum(DutyFraction.hours_today)).scalar() or 0,
        "giveaway_count": db.query(func.count(Giveaway.id)).scalar() or 0,
    }


# ---------- Benutzerverwaltung ----------
@app.get("/api/users")
def users(db: Session = Depends(get_db)):
    return [{"id": u.id, "name": u.username, "role": u.role, "balance": u.balance,
             "status": u.status, "joined": u.joined_at.isoformat()} for u in db.query(User).all()]


@app.post("/api/users/{user_id}/balance")
def adjust_balance(user_id: str, delta: int, db: Session = Depends(get_db), user=Depends(require_user)):
    target = db.query(User).get(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    target.balance += delta
    log(db, "system", f"Guthaben von {target.username} um {delta} ₡ angepasst")
    db.commit()
    return {"ok": True, "balance": target.balance}


# ---------- AFK-System ----------
@app.get("/api/afk")
def afk_list(db: Session = Depends(get_db)):
    users = db.query(User).filter(User.afk_reason.isnot(None)).all()
    return [
        {"id": u.id, "name": u.username, "reason": u.afk_reason,
         "since": u.afk_since.isoformat() if u.afk_since else None}
        for u in users
    ]


@app.get("/api/afk/me")
def afk_me(db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))
    return {"reason": me.afk_reason, "since": me.afk_since.isoformat() if me.afk_since else None}


@app.post("/api/afk/set")
def afk_set(grund: str = "Kein Grund angegeben", db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))
    me.afk_reason = grund
    me.afk_since = datetime.now(timezone.utc)
    log(db, "system", f"{me.username} ist jetzt AFK (über Dashboard): {grund}")
    db.commit()
    return {"ok": True}


@app.post("/api/afk/clear")
def afk_clear(db: Session = Depends(get_db), user=Depends(require_user)):
    me = get_or_create_user_by_id(db, user["sub"], user.get("username", "Dashboard"))
    me.afk_reason = None
    me.afk_since = None
    log(db, "system", f"{me.username} hat AFK beendet (über Dashboard)")
    db.commit()
    return {"ok": True}


# ---------- Einstellungen ----------
@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    return {s.key: s.value for s in db.query(Setting).all()}


@app.post("/api/settings")
def update_settings(payload: dict, db: Session = Depends(get_db), user=Depends(require_user)):
    for key, value in payload.items():
        setting = db.query(Setting).get(key)
        if setting:
            setting.value = str(value)
        else:
            db.add(Setting(key=key, value=str(value)))
    log(db, "system", f"Einstellungen geändert: {', '.join(payload.keys())}")
    db.commit()
    return {"ok": True}
