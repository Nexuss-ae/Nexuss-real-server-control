import os, asyncio, sqlite3, secrets, logging
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from contextlib import asynccontextmanager
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeSerializer, BadSignature

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("servercontrol")

BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0") or 0)
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-this-to-a-long-random-secret")
DB_PATH = os.getenv("DATABASE_PATH", "./data/servercontrol.db")
COOKIE_NAME = "servercontrol_session"
serializer = URLSafeSerializer(SESSION_SECRET, salt="servercontrol-session-v1")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS birthdays (user_id TEXT PRIMARY KEY, month INTEGER NOT NULL, day INTEGER NOT NULL, display_name TEXT, updated_by TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, moderator_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS modlog (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, target_id TEXT, moderator_id TEXT, reason TEXT, details TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS role_panels (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', channel_id TEXT, message_id TEXT, created_by TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS role_panel_options (id INTEGER PRIMARY KEY AUTOINCREMENT, panel_id INTEGER NOT NULL, role_id TEXT NOT NULL, label TEXT NOT NULL, emoji TEXT, UNIQUE(panel_id, role_id));
        CREATE TABLE IF NOT EXISTS scheduled_events (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, starts_at TEXT NOT NULL, channel_id TEXT, description TEXT, created_by TEXT, reminded INTEGER NOT NULL DEFAULT 0);
        ''')
        defaults = {"birthday_channel_id":"", "welcome_channel_id":"", "log_channel_id":"", "birthday_public":"1", "welcome_enabled":"1", "welcome_message":"Willkommen {mention} auf dem Server!", "timezone":"Europe/Berlin", "self_roles":"[]"}
        for k,v in defaults.items(): c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k,v))

def setting(key, default=""):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

def set_setting(key, value):
    with db() as c: c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

def log_action(action, target_id=None, moderator_id=None, reason="", details=""):
    with db() as c:
        c.execute("INSERT INTO modlog(action,target_id,moderator_id,reason,details,created_at) VALUES(?,?,?,?,?,?)", (action, str(target_id or ""), str(moderator_id or ""), reason[:500], details[:1000], datetime.now(timezone.utc).isoformat()))

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def send_log(guild: discord.Guild, message: str):
    channel_id = setting("log_channel_id")
    if channel_id:
        ch = guild.get_channel(int(channel_id))
        if ch:
            try: await ch.send(message[:1900])
            except discord.HTTPException: pass

@bot.event
async def on_ready():
    log.info("Bot connected as %s", bot.user)
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)
        else:
            await bot.tree.sync()
    except Exception: log.exception("Slash command sync failed")
    if not birthday_loop.is_running(): birthday_loop.start()

@bot.event
async def on_member_join(member: discord.Member):
    if setting("welcome_enabled", "1") == "1":
        channel_id = setting("welcome_channel_id")
        channel = member.guild.get_channel(int(channel_id)) if channel_id else None
        if channel:
            msg = setting("welcome_message", "Willkommen {mention} auf dem Server!")
            try: await channel.send(msg.replace("{mention}", member.mention).replace("{server}", member.guild.name))
            except discord.HTTPException: pass
    await send_log(member.guild, f"📥 Mitglied beigetreten: {member.mention} (`{member.id}`)")

@bot.event
async def on_member_remove(member: discord.Member):
    await send_log(member.guild, f"📤 Mitglied verlassen: **{discord.utils.escape_markdown(member.display_name)}** (`{member.id}`)")

@tasks.loop(minutes=30)
async def birthday_loop():
    if not bot.is_ready(): return
    try:
        try: tz = ZoneInfo(setting("timezone", "Europe/Berlin"))
        except ZoneInfoNotFoundError: tz = timezone.utc
        today = datetime.now(tz).date()
        channel_id = setting("birthday_channel_id")
        if not channel_id: return
        for guild in bot.guilds:
            channel = guild.get_channel(int(channel_id))
            if not channel: continue
            with db() as c:
                rows = c.execute("SELECT user_id, display_name FROM birthdays WHERE month=? AND day=?", (today.month, today.day)).fetchall()
            for row in rows:
                member = guild.get_member(int(row["user_id"]))
                name = member.mention if member else (row["display_name"] or f"Mitglied {row['user_id']}")
                marker = f"birthday:{today.isoformat()}:{row['user_id']}"
                with db() as c:
                    already = c.execute("SELECT 1 FROM modlog WHERE details=?", (marker,)).fetchone()
                    if already: continue
                    c.execute("INSERT INTO modlog(action,target_id,moderator_id,reason,details,created_at) VALUES(?,?,?,?,?,?)", ("birthday", row["user_id"], "system", "Geburtstagserinnerung", marker, datetime.now(timezone.utc).isoformat()))
                try: await channel.send(f"🎂 Alles Gute zum Geburtstag, {name}! 🥳")
                except discord.HTTPException: pass
    except Exception: log.exception("Birthday loop failed")

@birthday_loop.before_loop
async def before_birthday_loop(): await bot.wait_until_ready()

@bot.tree.command(name="timeout", description="Gibt einem Mitglied einen zeitbegrenzten Timeout")
@app_commands.describe(mitglied="Mitglied", minuten="Dauer in Minuten (1 bis 40320)", grund="Grund")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_cmd(interaction: discord.Interaction, mitglied: discord.Member, minuten: app_commands.Range[int,1,40320], grund: str="Kein Grund angegeben"):
    if mitglied.id == interaction.user.id or mitglied == interaction.guild.owner:
        await interaction.response.send_message("Diese Person kann nicht auf diese Weise getimeoutet werden.", ephemeral=True); return
    try:
        await mitglied.timeout(timedelta(minutes=minuten), reason=f"{interaction.user}: {grund}")
        log_action("timeout", mitglied.id, interaction.user.id, grund, f"minutes={minuten}")
        await interaction.response.send_message(f"⏳ {mitglied.mention} wurde für {minuten} Minuten getimeoutet. Grund: {grund}", ephemeral=True)
        await send_log(interaction.guild, f"⏳ Timeout: {mitglied.mention} · {minuten} Min. · von {interaction.user.mention} · {grund}")
    except discord.Forbidden: await interaction.response.send_message("Mir fehlen die nötigen Rechte oder meine Bot-Rolle steht zu niedrig.", ephemeral=True)

@bot.tree.command(name="warn", description="Verwarnt ein Mitglied")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn_cmd(interaction: discord.Interaction, mitglied: discord.Member, grund: str):
    with db() as c: c.execute("INSERT INTO warnings(user_id,moderator_id,reason,created_at) VALUES(?,?,?,?)", (str(mitglied.id), str(interaction.user.id), grund, datetime.now(timezone.utc).isoformat()))
    log_action("warn", mitglied.id, interaction.user.id, grund)
    await interaction.response.send_message(f"⚠️ {mitglied.mention} wurde verwarnt. Grund: {grund}", ephemeral=True)
    await send_log(interaction.guild, f"⚠️ Verwarnung: {mitglied.mention} · von {interaction.user.mention} · {grund}")

@bot.tree.command(name="warnings", description="Zeigt die aktiven Verwarnungen eines Mitglieds")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction, mitglied: discord.Member):
    with db() as c: rows = c.execute("SELECT reason,created_at FROM warnings WHERE user_id=? AND active=1 ORDER BY id DESC LIMIT 10", (str(mitglied.id),)).fetchall()
    body = "\n".join(f"• {r['reason']} — {r['created_at'][:10]}" for r in rows) or "Keine aktiven Verwarnungen."
    await interaction.response.send_message(f"**Verwarnungen für {mitglied.display_name}:**\n{body}", ephemeral=True)

@bot.tree.command(name="birthday", description="Trägt deinen Geburtstag ein (Tag und Monat, ohne Jahr)")
@app_commands.describe(tag="Tag (1–31)", monat="Monat (1–12)")
async def birthday_cmd(interaction: discord.Interaction, tag: app_commands.Range[int,1,31], monat: app_commands.Range[int,1,12]):
    try: date(2024, monat, tag)
    except ValueError:
        await interaction.response.send_message("Dieses Datum gibt es nicht.", ephemeral=True); return
    with db() as c: c.execute("INSERT INTO birthdays(user_id,month,day,display_name,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET month=excluded.month,day=excluded.day,display_name=excluded.display_name,updated_by=excluded.updated_by,updated_at=excluded.updated_at", (str(interaction.user.id), monat, tag, interaction.user.display_name, str(interaction.user.id), datetime.now(timezone.utc).isoformat()))
    await interaction.response.send_message(f"🎂 Dein Geburtstag ist gespeichert: {tag:02d}.{monat:02d}. Es wird kein Geburtsjahr gespeichert.", ephemeral=True)

@bot.tree.command(name="serverinfo", description="Zeigt grundlegende Serverinformationen")
async def serverinfo_cmd(interaction: discord.Interaction):
    g = interaction.guild
    await interaction.response.send_message(f"**{g.name}**\nMitglieder: {g.member_count}\nRollen: {len(g.roles)}\nKanäle: {len(g.channels)}", ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions): msg = "Du hast nicht die nötigen Discord-Berechtigungen."
    else:
        log.exception("Slash command error", exc_info=error); msg = "Der Befehl ist fehlgeschlagen. Prüfe die Bot-Rechte und Logs."
    try:
        if interaction.response.is_done(): await interaction.followup.send(msg, ephemeral=True)
        else: await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = None
    if BOT_TOKEN:
        task = asyncio.create_task(bot.start(BOT_TOKEN))
    yield
    if task:
        await bot.close()
        task.cancel()
        try: await task
        except (asyncio.CancelledError, Exception): pass

app = FastAPI(title="Server Control", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def session_user(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token: return None
    try: return serializer.loads(token)
    except BadSignature: return None

async def require_admin(request: Request):
    user = session_user(request)
    if not user: raise HTTPException(401, "Bitte zuerst mit Discord anmelden.")
    if not GUILD_ID: raise HTTPException(503, "DISCORD_GUILD_ID ist noch nicht konfiguriert.")
    if not user.get("guild_admin"):
        raise HTTPException(403, "Du brauchst Administrator- oder Server-Management-Rechte auf dem konfigurierten Server.")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://discord.com/api/v10/guilds/{GUILD_ID}/members/{user['id']}", headers={"Authorization": f"Bot {BOT_TOKEN}"})
    if r.status_code != 200: raise HTTPException(403, "Du bist kein Mitglied des konfigurierten Servers oder der Bot kann dich nicht prüfen.")
    return user

@app.get("/health")
async def health(): return {"ok": True, "bot_ready": bot.is_ready(), "bot_configured": bool(BOT_TOKEN)}

@app.get("/login")
async def login():
    if not CLIENT_ID or not CLIENT_SECRET: raise HTTPException(503, "Discord OAuth ist noch nicht konfiguriert.")
    state = secrets.token_urlsafe(24)
    url = "https://discord.com/oauth2/authorize?" + httpx.QueryParams({"client_id":CLIENT_ID,"response_type":"code","redirect_uri":PUBLIC_URL+"/callback","scope":"identify guilds","state":state,"prompt":"consent"}).__str__()
    response = RedirectResponse(url, status_code=302)
    response.set_cookie("oauth_state", state, httponly=True, secure=PUBLIC_URL.startswith("https://"), samesite="lax", max_age=600)
    return response

@app.get("/callback")
async def callback(request: Request, code: str, state: str):
    expected = request.cookies.get("oauth_state")
    if not expected or not secrets.compare_digest(expected, state): raise HTTPException(400, "Ungültiger OAuth-Status. Bitte erneut anmelden.")
    async with httpx.AsyncClient(timeout=15) as client:
        token_r = await client.post("https://discord.com/api/v10/oauth2/token", data={"client_id":CLIENT_ID,"client_secret":CLIENT_SECRET,"grant_type":"authorization_code","code":code,"redirect_uri":PUBLIC_URL+"/callback"}, headers={"Content-Type":"application/x-www-form-urlencoded"})
        if token_r.status_code != 200: raise HTTPException(400, "Discord-Anmeldung fehlgeschlagen.")
        token = token_r.json()
        user_r = await client.get("https://discord.com/api/v10/users/@me", headers={"Authorization":f"Bearer {token['access_token']}"})
        guilds_r = await client.get("https://discord.com/api/v10/users/@me/guilds", headers={"Authorization":f"Bearer {token['access_token']}"})
        if user_r.status_code != 200: raise HTTPException(400, "Discord-Profil konnte nicht gelesen werden.")
        user = user_r.json()
        guild_admin = False
        if guilds_r.status_code == 200 and GUILD_ID:
            for g in guilds_r.json():
                if int(g.get("id", 0)) == GUILD_ID and (int(g.get("permissions", "0")) & (0x8 | 0x20)):
                    guild_admin = True
                    break
    payload = {"id":user["id"],"username":user.get("global_name") or user.get("username"),"avatar":user.get("avatar"),"guild_admin":guild_admin}
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(COOKIE_NAME, serializer.dumps(payload), httponly=True, secure=PUBLIC_URL.startswith("https://"), samesite="lax", max_age=3600)
    response.delete_cookie("oauth_state")
    return response

@app.get("/logout")
async def logout():
    r = RedirectResponse("/", status_code=302); r.delete_cookie(COOKIE_NAME); return r

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = session_user(request)
    if user:
        try:
            await require_admin(request)
            with db() as c:
                birthdays = c.execute("SELECT user_id,month,day,display_name FROM birthdays ORDER BY month,day").fetchall()
                logs = c.execute("SELECT action,target_id,moderator_id,reason,created_at FROM modlog ORDER BY id DESC LIMIT 12").fetchall()
                warnings_count = c.execute("SELECT COUNT(*) FROM warnings WHERE active=1").fetchone()[0]
            roles = []
            guild = bot.get_guild(GUILD_ID) if GUILD_ID else None
            if guild:
                roles = [{"id":str(r.id),"name":r.name,"color":str(r.color)} for r in sorted(guild.roles, key=lambda x:x.position, reverse=True) if not r.is_default() and not r.managed]
            return templates.TemplateResponse("dashboard.html", {"request":request,"user":user,"guild_name":guild.name if guild else "Discord-Server","members":guild.member_count if guild else "—","roles":roles,"birthdays":birthdays,"logs":logs,"warnings_count":warnings_count,"settings":{"welcome_channel_id":setting("welcome_channel_id"),"birthday_channel_id":setting("birthday_channel_id"),"log_channel_id":setting("log_channel_id"),"welcome_message":setting("welcome_message"),"welcome_enabled":setting("welcome_enabled")}})
        except HTTPException as e:
            return templates.TemplateResponse(
    request=request,
    name="login.html",
    context={"request": request, "user": user, "error": e.detail},
    status_code=e.status_code
)
    return templates.TemplateResponse(
    request=request,
    name="login.html",
    context={"request": request, "user": None, "error": None}
)

@app.get("/api/roles")
async def api_roles(request: Request):
    await require_admin(request)
    guild = bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503, "Bot ist noch nicht mit dem Server verbunden.")
    return [{"id":str(r.id),"name":r.name,"position":r.position,"managed":r.managed} for r in sorted(guild.roles,key=lambda r:r.position,reverse=True) if not r.is_default()]

@app.post("/api/roles/assign")
async def assign_role(request: Request, user_id: str=Form(...), role_id: str=Form(...), action: str=Form("add")):
    user = await require_admin(request)
    guild = bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503,"Bot ist nicht bereit.")
    try: member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id)); role = guild.get_role(int(role_id))
    except (ValueError, discord.NotFound): raise HTTPException(404,"Mitglied oder Rolle nicht gefunden.")
    if not role or role.is_default() or role.managed: raise HTTPException(400,"Diese Rolle kann nicht verwaltet werden.")
    me = guild.me
    if not me or role >= me.top_role: raise HTTPException(400,"Die Bot-Rolle muss in der Discord-Rollenliste über der Zielrolle stehen.")
    try:
        if action == "remove": await member.remove_roles(role, reason=f"Dashboard von {user['username']}")
        else: await member.add_roles(role, reason=f"Dashboard von {user['username']}")
    except discord.Forbidden: raise HTTPException(403,"Discord verweigert die Aktion. Prüfe die Bot-Berechtigungen.")
    log_action("role_remove" if action=="remove" else "role_add", member.id, user['id'], role.name)
    return RedirectResponse("/#members", status_code=303)

@app.post("/api/moderation/timeout")
async def web_timeout(request: Request, user_id: str=Form(...), minutes: int=Form(...), reason: str=Form("Kein Grund angegeben")):
    user = await require_admin(request); guild=bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503,"Bot ist nicht bereit.")
    if minutes < 1 or minutes > 40320: raise HTTPException(400,"Timeout muss zwischen 1 und 40320 Minuten liegen.")
    try: member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
    except (ValueError,discord.NotFound): raise HTTPException(404,"Mitglied nicht gefunden.")
    try: await member.timeout(timedelta(minutes=minutes), reason=f"{user['username']}: {reason}")
    except discord.Forbidden: raise HTTPException(403,"Bot hat keine Berechtigung für diesen Timeout.")
    log_action("timeout",member.id,user['id'],reason,f"minutes={minutes}"); await send_log(guild,f"⏳ Timeout: {member.mention} · {minutes} Min. · {reason}")
    return RedirectResponse("/#moderation",status_code=303)

@app.post("/api/moderation/ban")
async def web_ban(request: Request, user_id: str=Form(...), reason: str=Form("Kein Grund angegeben"), delete_days: int=Form(0)):
    user=await require_admin(request); guild=bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503,"Bot ist nicht bereit.")
    try: target=discord.Object(id=int(user_id))
    except ValueError: raise HTTPException(400,"Ungültige Discord-ID.")
    if delete_days not in (0,1,2,3,4,5,6,7): raise HTTPException(400,"Nachrichtenlöschung muss zwischen 0 und 7 Tagen liegen.")
    try: await guild.ban(target,reason=f"{user['username']}: {reason}",delete_message_seconds=delete_days*86400)
    except discord.Forbidden: raise HTTPException(403,"Bot hat keine Berechtigung für den Ban.")
    log_action("ban",user_id,user['id'],reason,f"delete_days={delete_days}"); await send_log(guild,f"🔨 Ban: <@{user_id}> · von {user['username']} · {reason}")
    return RedirectResponse("/#moderation",status_code=303)

@app.post("/api/moderation/unban")
async def web_unban(request: Request, user_id: str=Form(...), reason: str=Form("Ban aufgehoben")):
    user=await require_admin(request); guild=bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503,"Bot ist nicht bereit.")
    try: await guild.unban(discord.Object(id=int(user_id)),reason=f"{user['username']}: {reason}")
    except discord.NotFound: raise HTTPException(404,"Diese User-ID ist nicht gebannt.")
    except discord.Forbidden: raise HTTPException(403,"Bot hat keine Berechtigung für Unban.")
    log_action("unban",user_id,user['id'],reason); await send_log(guild,f"🔓 Unban: <@{user_id}> · {reason}")
    return RedirectResponse("/#moderation",status_code=303)

@app.post("/api/birthdays")
async def add_birthday(request: Request, user_id: str=Form(...), day: int=Form(...), month: int=Form(...), display_name: str=Form("")):
    user=await require_admin(request)
    try: date(2024,month,day)
    except ValueError: raise HTTPException(400,"Ungültiges Datum.")
    if not user_id.isdigit(): raise HTTPException(400,"Ungültige Discord-ID.")
    with db() as c: c.execute("INSERT INTO birthdays(user_id,month,day,display_name,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET month=excluded.month,day=excluded.day,display_name=excluded.display_name,updated_by=excluded.updated_by,updated_at=excluded.updated_at",(user_id,month,day,display_name[:100],user['id'],datetime.now(timezone.utc).isoformat()))
    log_action("birthday_set",user_id,user['id'],f"{day:02d}.{month:02d}")
    return RedirectResponse("/#birthdays",status_code=303)

@app.post("/api/birthdays/delete")
async def delete_birthday(request: Request, user_id: str=Form(...)):
    user=await require_admin(request)
    with db() as c: c.execute("DELETE FROM birthdays WHERE user_id=?",(user_id,))
    log_action("birthday_delete",user_id,user['id'],"Eintrag gelöscht")
    return RedirectResponse("/#birthdays",status_code=303)

@app.post("/api/settings")
async def save_settings(request: Request, welcome_channel_id: str=Form(""), birthday_channel_id: str=Form(""), log_channel_id: str=Form(""), welcome_message: str=Form("Willkommen {mention} auf dem Server!"), welcome_enabled: str=Form("0")):
    await require_admin(request)
    for key,val in [("welcome_channel_id",welcome_channel_id),("birthday_channel_id",birthday_channel_id),("log_channel_id",log_channel_id),("welcome_message",welcome_message[:500]),("welcome_enabled","1" if welcome_enabled=="1" else "0")]: set_setting(key,val.strip())
    return RedirectResponse("/#settings",status_code=303)

@app.get("/api/members")
async def api_members(request: Request, q: str=""):
    await require_admin(request); guild=bot.get_guild(GUILD_ID)
    if not guild: raise HTTPException(503,"Bot ist nicht bereit.")
    if not guild.chunked:
        try: await guild.chunk(cache=True)
        except Exception: pass
    members=sorted(guild.members,key=lambda m:m.display_name.casefold())
    if q: members=[m for m in members if q.casefold() in m.display_name.casefold() or q in str(m.id)]
    return [{"id":str(m.id),"name":m.display_name,"username":m.name,"bot":m.bot,"roles":[r.name for r in m.roles if not r.is_default()]} for m in members[:100]]

@app.get("/api/logs")
async def api_logs(request: Request):
    await require_admin(request)
    with db() as c: rows=c.execute("SELECT action,target_id,moderator_id,reason,details,created_at FROM modlog ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]
