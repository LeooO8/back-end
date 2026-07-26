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
import httpx
import jwt
import discord
from discord import app_commands
from discord.ext import commands
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


def get_starting_balance(db) -> int:
    setting = db.query(Setting).get("startguthaben")
    try:
        return int(setting.value) if setting and setting.value else 500
    except (TypeError, ValueError):
        return 500


def get_or_create_user(db, member: discord.Member) -> User:
    user = db.query(User).get(str(member.id))
    if not user:
        start = get_starting_balance(db)
        user = User(id=str(member.id), username=member.display_name, balance=start)
        db.add(user)
        db.add(LogEntry(type="system", text=f"{member.display_name} wurde neu angelegt (Startguthaben {start} ₡)"))
        db.commit()
    return user


@bot.event
async def on_ready():
    print(f"Bot eingeloggt als {bot.user}")
    try:
        # Einmalig aufräumen: zuvor global angemeldete Befehle wieder entfernen,
        # damit sie sich nicht mit den Server-spezifischen Befehlen doppeln.
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()

        for guild in bot.guilds:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"{len(synced)} Slash-Commands sofort auf '{guild.name}' synchronisiert")
    except Exception as e:
        print(f"Fehler beim Synchronisieren der Slash-Commands: {e}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Sobald der Bot einem neuen Server beitritt, sofort die Slash-Commands dort anmelden."""
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"{len(synced)} Slash-Commands auf neuem Server '{guild.name}' synchronisiert")
    except Exception as e:
        print(f"Fehler beim Synchronisieren auf neuem Server: {e}")


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


@bot.tree.command(name="dienst", description="Schaltet deinen Dienststatus für eine Fraktion um")
@app_commands.describe(fraktion="Name der Fraktion (z.B. Polizei)")
async def duty_cmd(interaction: discord.Interaction, fraktion: str):
    db = SessionLocal()
    try:
        f = db.query(DutyFraction).filter(DutyFraction.name.ilike(f"%{fraktion}%")).first()
        if not f:
            return await interaction.response.send_message(
                "Fraktion nicht gefunden. (Unter Dienstsystem im Dashboard anlegen.)", ephemeral=True
            )
        f.on_duty = 0 if f.on_duty > 0 else min(f.on_duty + 1, f.total or 1)
        db.add(LogEntry(type="dienst", text=f"{interaction.user.display_name} hat den Dienststatus von {f.name} geändert"))
        db.commit()
        status = "im Dienst" if f.on_duty > 0 else "außer Dienst"
        await interaction.response.send_message(f"👮 {f.name} ist jetzt **{status}** ({f.on_duty}/{f.total}).")
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
    return {
        "bot_status": "online" if bot.is_ready() else "startet…",
        "member_count": member_count,
        "on_duty": on_duty,
        "total_balance": total_balance,
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


# ---------- Shop ----------
@app.get("/api/shop/items")
def shop_items(db: Session = Depends(get_db)):
    return [{"id": i.id, "name": i.name, "category": i.category, "price": i.price, "sold": i.sold}
            for i in db.query(ShopItem).all()]


@app.post("/api/shop/items")
def create_item(name: str, category: str, price: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    item = ShopItem(name=name, category=category, price=price)
    db.add(item)
    log(db, "shop", f"Neuer Artikel erstellt: {name} ({price} ₡)")
    db.commit()
    return {"ok": True, "id": item.id}


# ---------- Dienstsystem ----------
@app.get("/api/dienst")
def dienst(db: Session = Depends(get_db)):
    return [{"id": d.id, "fraction": d.name, "onDuty": d.on_duty, "total": d.total,
             "hoursToday": d.hours_today, "channelId": d.channel_id}
            for d in db.query(DutyFraction).all()]


@app.post("/api/dienst/{fraction_id}/toggle")
async def toggle_dienst(fraction_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    f = db.query(DutyFraction).get(fraction_id)
    if not f:
        raise HTTPException(404, "Fraktion nicht gefunden")
    f.on_duty = 0 if f.on_duty > 0 else min(1, f.total)
    log(db, "dienst", f"Dienststatus geändert: {f.name}")
    db.commit()
    await post_duty_embed(f, user.get("username", "Dashboard"))
    return {"ok": True}


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
def adjust_balance(user_id: str, delta: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    target = db.query(User).get(user_id)
    if not target:
        raise HTTPException(404, "Benutzer nicht gefunden")
    target.balance += delta
    log(db, "system", f"Guthaben von {target.username} um {delta} ₡ angepasst")
    db.commit()
    return {"ok": True, "balance": target.balance}


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
