import React, { useState, useMemo, useEffect, createContext, useContext } from "react";
import {
  LayoutDashboard, Landmark, ShoppingBag, ShieldHalf, Gift, ScrollText,
  Settings, Users, BarChart3, Lock, Circle, ArrowUpRight, ArrowDownRight,
  Search, Plus, Pencil, Trash2, Power, Clock, Coins, Wallet, TrendingUp,
  KeyRound, LogIn, ChevronRight, Activity, WifiOff, Wifi, Moon,
  Users2, ListChecks, Ticket, Bot as BotIcon, LayoutGrid, RefreshCw
} from "lucide-react";

/* ---------------------------------------------------------
   API-VERBINDUNG
   Trage hier die Adresse deines Backends ein (siehe README).
   Solange die API nicht erreichbar ist, bleiben alle Bereiche
   mit den Beispieldaten unten voll funktionsfähig ("Demo-Modus").
--------------------------------------------------------- */
const API_BASE = "";

// Mehrserver-Unterstützung: welcher Discord-Server gerade im Dashboard
// ausgewählt ist. Wird von allen API-Aufrufen automatisch mitgeschickt.
const GuildState = { id: (typeof localStorage !== "undefined" && localStorage.getItem("guildId")) || null };

function withGuild(path) {
  if (!GuildState.id) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}guild_id=${encodeURIComponent(GuildState.id)}`;
}

async function apiGet(path) {
  const res = await fetch(`${API_BASE}${withGuild(path)}`, { credentials: "include" });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(`${API_BASE}${withGuild(path)}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

async function apiPostQuery(path, params) {
  const merged = { ...(params || {}) };
  if (GuildState.id) merged.guild_id = GuildState.id;
  const qs = new URLSearchParams(merged).toString();
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ""}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    let detail = `API ${path} -> ${res.status}`;
    try { const body = await res.json(); if (body.detail) detail = body.detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(`${API_BASE}${withGuild(path)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return res.json();
}

function selectGuild(id) {
  GuildState.id = id;
  try { localStorage.setItem("guildId", id); } catch (_) {}
  window.location.reload();
}

const LiveContext = createContext({ live: false, user: null });
const useLive = () => useContext(LiveContext);


/* ---------------------------------------------------------
   THEME TOKENS
   bg #0A0E13 · panel #12181F · panelAlt #171F2A · border #232D3B
   text #E7ECF2 · muted #7C8798
   gold #F2B705 (Ökonomie) · cyan #38BDF8 (Status) · green #4ADE80 (on)
   red #FB5B5B (off/danger)
--------------------------------------------------------- */
const C = {
  bg: "#0A0E13",
  panel: "#121821",
  panelAlt: "#171F2A",
  border: "#232D3B",
  text: "#E7ECF2",
  muted: "#7C8798",
  gold: "#F2B705",
  cyan: "#38BDF8",
  green: "#4ADE80",
  red: "#FB5B5B",
};

const fmtMoney = (n) =>
  n.toLocaleString("de-DE", { minimumFractionDigits: 0 }) + " ₡";

/* ---------------------------------------------------------
   MOCK DATA
--------------------------------------------------------- */
const USERS = [];

const TRANSACTIONS = [];

const SHOP_ITEMS = [];

const DUTY = [];

const GIVEAWAYS = [];

const LOGS = [];

const LOG_META = {
  bank: { label: "Bank", color: C.gold },
  shop: { label: "Shop", color: C.cyan },
  dienst: { label: "Dienst", color: C.green },
  login: { label: "Login", color: "#B79CFF" },
  system: { label: "System", color: C.muted },
  afk: { label: "AFK", color: "#F2B705" },
};

const NAV = [
  { key: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { key: "bank", label: "Bank-System", icon: Landmark },
  { key: "shop", label: "Shop-System", icon: ShoppingBag },
  { key: "dienst", label: "Dienstsystem", icon: ShieldHalf },
  { key: "afk", label: "AFK-System", icon: Moon },
  { key: "team", label: "Team", icon: Users2 },
  { key: "giveaway", label: "Giveaways", icon: Gift },
  { key: "todo", label: "To-Do-Liste", icon: ListChecks },
  { key: "logs", label: "Audit Logs", icon: ScrollText },
  { key: "tickets", label: "Tickets", icon: Ticket },
  { key: "stats", label: "Statistiken", icon: BarChart3 },
  { key: "users", label: "Benutzer", icon: Users },
  { key: "security", label: "Sicherheit", icon: Lock },
  { key: "bot", label: "Bot", icon: BotIcon },
  { key: "module", label: "Module", icon: LayoutGrid },
  { key: "settings", label: "Einstellungen", icon: Settings },
];

/* ---------------------------------------------------------
   PRIMITIVES
--------------------------------------------------------- */
function ChannelSelect({ value, onChange, placeholder, style }) {
  const { live } = useLive();
  const [channels, setChannels] = useState([]);
  useEffect(() => {
    if (!live) return;
    apiGet("/api/guild-channels").then(setChannels).catch(() => {});
  }, [live]);
  return (
    <select
      value={value} onChange={(e) => onChange(e.target.value)}
      style={{ background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13, ...style }}
    >
      <option value="">{placeholder || "Kanal wählen…"}</option>
      {channels.map((c) => <option key={c.id} value={c.id}># {c.name}</option>)}
    </select>
  );
}

function RoleSelect({ value, onChange, placeholder, style }) {
  const { live } = useLive();
  const [roles, setRoles] = useState([]);
  useEffect(() => {
    if (!live) return;
    apiGet("/api/guild-roles").then(setRoles).catch(() => {});
  }, [live]);
  return (
    <select
      value={value} onChange={(e) => onChange(e.target.value)}
      style={{ background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13, ...style }}
    >
      <option value="">{placeholder || "Rolle wählen…"}</option>
      {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
    </select>
  );
}

function CategorySelect({ value, onChange, placeholder, style }) {
  const { live } = useLive();
  const [categories, setCategories] = useState([]);
  useEffect(() => {
    if (!live) return;
    apiGet("/api/guild-categories").then(setCategories).catch(() => {});
  }, [live]);
  return (
    <select
      value={value} onChange={(e) => onChange(e.target.value)}
      style={{ background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13, ...style }}
    >
      <option value="">{placeholder || "Kategorie wählen…"}</option>
      {categories.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
    </select>
  );
}

function Panel({ children, style, ...rest }) {
  return (
    <div
      style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 10, ...style }}
      {...rest}
    >
      {children}
    </div>
  );
}

function Badge({ children, color = C.muted, bg }) {
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        fontFamily: "'JetBrains Mono', monospace", fontSize: 11, fontWeight: 600,
        color, background: bg || `${color}1A`, border: `1px solid ${color}40`,
        padding: "3px 8px", borderRadius: 5, letterSpacing: 0.3,
      }}
    >
      {children}
    </span>
  );
}

function StatusDot({ status }) {
  const col = status === "online" ? C.green : status === "idle" ? C.gold : C.muted;
  return <Circle size={8} style={{ fill: col, color: col }} />;
}

function SectionTitle({ eyebrow, title, action }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 20 }}>
      <div>
        <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: C.gold, letterSpacing: 1.5, marginBottom: 4, textTransform: "uppercase" }}>
          {eyebrow}
        </div>
        <h1 style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 28, fontWeight: 700, color: C.text, margin: 0, letterSpacing: 0.3 }}>
          {title}
        </h1>
      </div>
      {action}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, delta, deltaUp, accent = C.gold }) {
  return (
    <Panel style={{ padding: 18, flex: 1, minWidth: 160 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{ width: 34, height: 34, borderRadius: 8, background: `${accent}1A`, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Icon size={17} color={accent} />
        </div>
        {delta && (
          <span style={{ display: "flex", alignItems: "center", gap: 2, fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: deltaUp ? C.green : C.red }}>
            {deltaUp ? <ArrowUpRight size={13} /> : <ArrowDownRight size={13} />}{delta}
          </span>
        )}
      </div>
      <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 26, fontWeight: 700, color: C.text, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 12.5, color: C.muted, marginTop: 6 }}>{label}</div>
    </Panel>
  );
}

function Th({ children, align }) {
  return (
    <th style={{
      textAlign: align || "left", fontFamily: "'JetBrains Mono', monospace", fontSize: 10.5,
      color: C.muted, textTransform: "uppercase", letterSpacing: 1, padding: "0 14px 10px",
      fontWeight: 500, borderBottom: `1px solid ${C.border}`,
    }}>
      {children}
    </th>
  );
}
function Td({ children, align, style }) {
  return (
    <td style={{ padding: "13px 14px", fontSize: 13.5, color: C.text, textAlign: align || "left", borderBottom: `1px solid ${C.border}`, ...style }}>
      {children}
    </td>
  );
}

function IconBtn({ icon: Icon, danger, ...rest }) {
  return (
    <button
      {...rest}
      style={{
        width: 28, height: 28, borderRadius: 6, border: `1px solid ${C.border}`,
        background: "transparent", display: "inline-flex", alignItems: "center", justifyContent: "center",
        color: danger ? C.red : C.muted, cursor: "pointer",
        ...rest.style,
      }}>
      <Icon size={13} />
    </button>
  );
}

function PrimaryBtn({ children, icon: Icon, ...rest }) {
  return (
    <button
      {...rest}
      style={{
        display: "flex", alignItems: "center", gap: 7, background: C.gold, color: "#1A1400",
        border: "none", borderRadius: 7, padding: "9px 15px", fontWeight: 700, fontSize: 13,
        fontFamily: "'Rajdhani', sans-serif", letterSpacing: 0.3, cursor: "pointer",
        ...rest.style,
      }}>
      {Icon && <Icon size={15} />} {children}
    </button>
  );
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      style={{
        display: "flex", alignItems: "center", gap: 9, background: "transparent",
        border: "none", cursor: "pointer", padding: 0,
      }}
    >
      <span style={{
        width: 38, height: 21, borderRadius: 99, position: "relative", flexShrink: 0,
        background: checked ? C.green : C.border, transition: "background 0.15s",
      }}>
        <span style={{
          position: "absolute", top: 2, left: checked ? 19 : 2, width: 17, height: 17,
          borderRadius: 99, background: "#0A0E13", transition: "left 0.15s",
        }} />
      </span>
      {label && <span style={{ fontSize: 13, color: C.text }}>{label}</span>}
    </button>
  );
}

function ComingSoonPanel({ eyebrow, title, description, icon: Icon }) {
  return (
    <>
      <SectionTitle eyebrow={eyebrow} title={title} />
      <Panel style={{ padding: 32, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 10, maxWidth: 560 }}>
        {Icon && (
          <div style={{ width: 40, height: 40, borderRadius: 9, background: `${C.gold}1A`, display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 4 }}>
            <Icon size={19} color={C.gold} />
          </div>
        )}
        <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text }}>
          Dieser Bereich ist noch nicht angebunden
        </div>
        <div style={{ fontSize: 13, color: C.muted, lineHeight: 1.5 }}>
          {description}
        </div>
      </Panel>
    </>
  );
}

/* ---------------------------------------------------------
   HUD TICKER (signature element)
--------------------------------------------------------- */
function Ticker({ overview }) {
  const { live, user, myGuilds } = useLive();
  const items = [
    { label: "BOT-STATUS", value: overview ? overview.bot_status?.toUpperCase() : "ONLINE", color: C.green },
    { label: "MITGLIEDER", value: overview ? overview.member_count.toLocaleString("de-DE") : "0", color: C.text },
    { label: "IM DIENST", value: overview ? overview.on_duty : "7", color: C.cyan },
    { label: "GESAMTGUTHABEN", value: fmtMoney(overview ? overview.total_balance : 0), color: C.gold },
    { label: "UPTIME", value: overview ? formatUptime(overview.uptime_seconds) : "0T 0H 0M", color: C.text },
  ];
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20,
      background: C.panelAlt, borderBottom: `1px solid ${C.border}`,
      padding: "9px 24px", fontFamily: "'JetBrains Mono', monospace",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 28, overflowX: "auto" }}>
        {items.map((it, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, whiteSpace: "nowrap" }}>
            <span style={{ width: 6, height: 6, borderRadius: 99, background: it.color, boxShadow: `0 0 6px ${it.color}` }} />
            <span style={{ fontSize: 10, color: C.muted, letterSpacing: 1 }}>{it.label}</span>
            <span style={{ fontSize: 12, color: it.color, fontWeight: 700 }}>{it.value}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0 }}>
        <span style={{
          display: "flex", alignItems: "center", gap: 6, fontSize: 10.5, letterSpacing: 0.5,
          color: live ? C.green : C.muted, border: `1px solid ${live ? C.green : C.border}`,
          borderRadius: 5, padding: "3px 8px",
        }}>
          {live ? <Wifi size={11} /> : <WifiOff size={11} />}
          {live ? "LIVE VERBUNDEN" : "DEMO-DATEN"}
        </span>
        {live && user ? (
          <>
            <span style={{ fontSize: 11.5, color: C.text }}>{user.username}</span>
            <button
              onClick={() => {
                apiPost("/auth/logout", {}).finally(() => window.location.reload());
              }}
              style={{
                display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: C.muted,
                background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "5px 10px",
                cursor: "pointer", fontFamily: "'Inter', sans-serif",
              }}
            >
              Abmelden
            </button>
            {myGuilds && myGuilds.length > 1 && (
              <button
                onClick={() => { GuildState.id = null; try { localStorage.removeItem("guildId"); } catch (_) {} window.location.reload(); }}
                style={{
                  display: "flex", alignItems: "center", gap: 5, fontSize: 11, color: C.muted,
                  background: "transparent", border: `1px solid ${C.border}`, borderRadius: 5, padding: "5px 10px",
                  cursor: "pointer", fontFamily: "'Inter', sans-serif",
                }}
              >
                Server wechseln
              </button>
            )}
          </>
        ) : (
          <a href={`${API_BASE}/auth/login`} style={{
            display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, color: "#1A1400",
            background: C.gold, borderRadius: 5, padding: "5px 10px", fontWeight: 700,
            textDecoration: "none", fontFamily: "'Rajdhani', sans-serif",
          }}>
            <LogIn size={12} /> Mit Discord anmelden
          </a>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------
   SECTIONS
--------------------------------------------------------- */
function formatUptime(seconds) {
  if (!seconds || seconds < 0) return "0M";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${days}T ${hours}H ${minutes}M`;
}

function DashboardSection() {
  const { live, user } = useLive();
  const [overview, setOverview] = useState(null);
  const [bannerUrl, setBannerUrl] = useState("");

  useEffect(() => {
    if (!live) return;
    apiGet("/api/overview").then(setOverview).catch(() => {});
    apiGet("/api/settings").then((s) => setBannerUrl(s.welcome_banner_url || "")).catch(() => {});
  }, [live]);

  const logs = overview?.recent_logs || [];
  const bannerStyle = bannerUrl
    ? {
        backgroundImage: `linear-gradient(90deg, rgba(10,14,19,0.55), rgba(10,14,19,0.85)), url(${bannerUrl})`,
        backgroundSize: "cover", backgroundPosition: "center",
      }
    : { background: `linear-gradient(90deg, ${C.gold}22, ${C.cyan}11)` };

  return (
    <>
      <SectionTitle eyebrow="Systemübersicht" title="Dashboard" />
      {live && user && (
        <Panel style={{
          position: "relative", overflow: "hidden", marginBottom: 18, minHeight: 120,
          ...bannerStyle, borderColor: `${C.gold}40`,
        }}>
          <div style={{ position: "relative", padding: "22px 24px", display: "flex", alignItems: "center", gap: 14, height: "100%" }}>
            {user.avatar ? (
              <img
                src={`https://cdn.discordapp.com/avatars/${user.sub}/${user.avatar}.png?size=128`}
                alt={user.username}
                style={{ width: 42, height: 42, borderRadius: 10, boxShadow: "0 0 0 3px rgba(0,0,0,0.4)", flexShrink: 0, objectFit: "cover" }}
              />
            ) : (
              <div style={{
                width: 42, height: 42, borderRadius: 10, background: `linear-gradient(135deg, ${C.gold}, ${C.cyan})`,
                display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800,
                fontFamily: "'Rajdhani', sans-serif", color: "#0A0E13", fontSize: 18, flexShrink: 0,
                boxShadow: "0 0 0 3px rgba(0,0,0,0.4)",
              }}>
                {user.username?.[0]?.toUpperCase() || "?"}
              </div>
            )}
            <div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontSize: 22, fontWeight: 800, color: "#fff", letterSpacing: 0.3, textShadow: "0 2px 8px rgba(0,0,0,0.6)" }}>
                Willkommen, {user.username}!
              </div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: C.gold, letterSpacing: 1, marginTop: 4, textShadow: "0 1px 4px rgba(0,0,0,0.6)" }}>
                SERVER CONTROL PANEL
              </div>
            </div>
          </div>
        </Panel>
      )}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 14 }}>
        <StatCard icon={Users} label="Mitglieder gesamt" value={overview ? overview.member_count : "0"} accent={C.cyan} />
        <StatCard icon={ShieldHalf} label="Aktive Dienste" value={overview ? overview.on_duty : "0"} accent={C.green} />
        <StatCard icon={Coins} label="Gesamtguthaben" value={fmtMoney(overview ? overview.total_balance : 0)} accent={C.gold} />
        <StatCard icon={Activity} label="Bot-Uptime" value={formatUptime(overview?.uptime_seconds)} accent={C.text} />
      </div>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Panel style={{ padding: 18, flex: 2 }}>
          <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 14 }}>
            Live-Systemstatus
          </div>
          {[
            { name: "Discord Gateway", ok: overview?.bot_status === "online" },
            { name: "Bank-System", ok: true },
            { name: "Shop-System", ok: true },
            { name: "Dienstsystem", ok: true },
            { name: "Datenbank", ok: true },
            { name: "OAuth2 Login", ok: true },
          ].map((s, i) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "9px 0", borderBottom: i < 5 ? `1px solid ${C.border}` : "none" }}>
              <span style={{ fontSize: 13.5, color: C.text }}>{s.name}</span>
              <Badge color={s.ok ? C.green : C.red}><StatusDot status={s.ok ? "online" : "offline"} /> {s.ok ? "Betriebsbereit" : "Gestört"}</Badge>
            </div>
          ))}
        </Panel>
        <Panel style={{ padding: 18, flex: 1 }}>
          <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 14 }}>
            Letzte Aktionen
          </div>
          {logs.length === 0 ? (
            <div style={{ fontSize: 12.5, color: C.muted }}>Noch keine Aktionen.</div>
          ) : logs.map((l) => (
            <div key={l.id} style={{ display: "flex", gap: 9, alignItems: "flex-start", padding: "8px 0", borderBottom: `1px solid ${C.border}` }}>
              <span style={{ width: 6, height: 6, marginTop: 5, borderRadius: 99, background: LOG_META[l.type]?.color || C.muted, flexShrink: 0 }} />
              <div>
                <div style={{ fontSize: 12.5, color: C.text, lineHeight: 1.4 }}>{l.text}</div>
                <div style={{ fontSize: 10.5, color: C.muted, marginTop: 2, fontFamily: "'JetBrains Mono', monospace" }}>{new Date(l.time).toLocaleString("de-DE")}</div>
              </div>
            </div>
          ))}
        </Panel>
      </div>
    </>
  );
}

function BankSection() {
  const { live } = useLive();
  const [startBalance, setStartBalance] = useState(500);
  const [saved, setSaved] = useState(false);
  const [accounts, setAccounts] = useState(USERS);
  const [txns, setTxns] = useState(TRANSACTIONS);
  const [toId, setToId] = useState("");
  const [amount, setAmount] = useState("");
  const [depositAmount, setDepositAmount] = useState("");
  const [withdrawAmount, setWithdrawAmount] = useState("");

  const refresh = () => {
    apiGet("/api/bank/accounts").then(setAccounts).catch(() => {});
    apiGet("/api/bank/transactions").then(setTxns).catch(() => {});
  };

  useEffect(() => {
    if (!live) return;
    refresh();
    apiGet("/api/settings").then((s) => {
      if (s.startguthaben) setStartBalance(Number(s.startguthaben));
    }).catch(() => {});
  }, [live]);

  const saveStartBalance = () => {
    apiPost("/api/settings", { startguthaben: startBalance })
      .then(() => { setSaved(true); setTimeout(() => setSaved(false), 2000); })
      .catch((err) => alert(err.message || "Speichern fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const doTransfer = () => {
    if (!toId || !amount) return;
    if (!live) { alert("Überweisungen sind nur im Live-Modus möglich."); return; }
    apiPostQuery("/api/bank/transfer", { empfaenger_id: toId, betrag: amount })
      .then(() => { setToId(""); setAmount(""); refresh(); })
      .catch((err) => alert(err.message || "Überweisung fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const doDeposit = () => {
    if (!depositAmount) return;
    if (!live) { alert("Nur im Live-Modus möglich."); return; }
    apiPostQuery("/api/bank/deposit", { betrag: depositAmount })
      .then(() => { setDepositAmount(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const doWithdraw = () => {
    if (!withdrawAmount) return;
    if (!live) { alert("Nur im Live-Modus möglich."); return; }
    apiPostQuery("/api/bank/withdraw", { betrag: withdrawAmount })
      .then(() => { setWithdrawAmount(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const adjustBalance = (u) => {
    const input = prompt(`Guthaben von ${u.name} anpassen — Betrag eingeben (z.B. 500 oder -200):`);
    if (input === null || input.trim() === "") return;
    const delta = Number(input);
    if (Number.isNaN(delta)) return alert("Bitte eine Zahl eingeben.");
    if (!live) {
      setAccounts(accounts.map((x) => x.id === u.id ? { ...x, balance: x.balance + delta } : x));
      return;
    }
    apiPostQuery(`/api/users/${u.id}/balance`, { delta }).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  return (
    <>
      <SectionTitle eyebrow="Wirtschaft" title="Bank-System" />
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 20 }}>
        <StatCard icon={Wallet} label="Gesamtguthaben aller Nutzer"
          value={fmtMoney(accounts.reduce((sum, a) => sum + (a.balance || 0), 0))} accent={C.gold} />
        <Panel style={{ padding: 18, flex: 1, minWidth: 220 }}>
          <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 10 }}>Startguthaben (frei einstellbar)</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input
              type="number" value={startBalance} onChange={(e) => setStartBalance(e.target.value)}
              style={{ flex: 1, minWidth: 0, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
            />
            <PrimaryBtn onClick={saveStartBalance}>{saved ? "Gespeichert ✓" : "Speichern"}</PrimaryBtn>
          </div>
        </Panel>
        <Panel style={{ padding: 18, flex: 1, minWidth: 260 }}>
          <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 10 }}>Überweisen</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select
              value={toId} onChange={(e) => setToId(e.target.value)}
              style={{ flex: 1.4, minWidth: 120, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
            >
              <option value="">Empfänger wählen…</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
            <input
              type="number" placeholder="Betrag" value={amount} onChange={(e) => setAmount(e.target.value)}
              style={{ flex: 1, minWidth: 80, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
            />
            <PrimaryBtn onClick={doTransfer}>Senden</PrimaryBtn>
          </div>
        </Panel>
      </div>

      {live && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 20 }}>
          <Panel style={{ padding: 18, flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 10 }}>Einzahlen (Bargeld → Bank)</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input
                type="number" placeholder="Betrag" value={depositAmount} onChange={(e) => setDepositAmount(e.target.value)}
                style={{ flex: 1, minWidth: 0, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
              />
              <PrimaryBtn onClick={doDeposit}>Einzahlen</PrimaryBtn>
            </div>
          </Panel>
          <Panel style={{ padding: 18, flex: 1, minWidth: 220 }}>
            <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 10 }}>Auszahlen (Bank → Bargeld)</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input
                type="number" placeholder="Betrag" value={withdrawAmount} onChange={(e) => setWithdrawAmount(e.target.value)}
                style={{ flex: 1, minWidth: 0, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
              />
              <PrimaryBtn onClick={doWithdraw}>Auszahlen</PrimaryBtn>
            </div>
          </Panel>
        </div>
      )}

      <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 10 }}>Konten</div>
      <Panel style={{ overflow: "hidden", marginBottom: 20 }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><Th>Benutzer</Th><Th>Bankguthaben</Th><Th>Bargeld</Th><Th>Rolle</Th><Th align="right">Aktion</Th></tr></thead>
          <tbody>
            {accounts.map((u) => (
              <tr key={u.id}>
                <Td>{u.name}</Td>
                <Td style={{ fontFamily: "'JetBrains Mono', monospace", color: C.gold }}>{fmtMoney(u.balance)}</Td>
                <Td style={{ fontFamily: "'JetBrains Mono', monospace", color: C.cyan }}>{fmtMoney(u.cash || 0)}</Td>
                <Td><span style={{ color: C.muted }}>{u.role}</span></Td>
                <Td align="right"><IconBtn icon={Pencil} onClick={() => adjustBalance(u)} /></Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>

      <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 10 }}>Transaktionsverlauf</div>
      <Panel style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><Th>Von</Th><Th>An</Th><Th>Typ</Th><Th align="right">Betrag</Th><Th align="right">Zeit</Th></tr></thead>
          <tbody>
            {txns.map((t) => (
              <tr key={t.id}>
                <Td>{t.from}</Td><Td>{t.to}</Td>
                <Td><Badge color={C.cyan}>{t.type}</Badge></Td>
                <Td align="right" style={{ fontFamily: "'JetBrains Mono', monospace", color: C.gold }}>{fmtMoney(t.amount)}</Td>
                <Td align="right" style={{ color: C.muted, fontSize: 12 }}>{t.time}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </>
  );
}

function ShopSection() {
  const { live } = useLive();
  const [items, setItems] = useState(SHOP_ITEMS);
  const [newName, setNewName] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [newPrice, setNewPrice] = useState("");
  const [newRoleId, setNewRoleId] = useState("");

  const refresh = () => apiGet("/api/shop/items").then(setItems).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const cats = [...new Set(items.map((i) => i.category).filter(Boolean))];

  const createItem = () => {
    if (!newName.trim() || !newCategory.trim() || !newPrice) return;
    if (!live) {
      setItems([...items, { id: Date.now(), name: newName, category: newCategory, price: Number(newPrice), sold: 0, roleId: newRoleId }]);
      setNewName(""); setNewCategory(""); setNewPrice(""); setNewRoleId("");
      return;
    }
    apiPostQuery("/api/shop/items", { name: newName, category: newCategory, price: newPrice, role_id: newRoleId || "" })
      .then(() => { setNewName(""); setNewCategory(""); setNewPrice(""); setNewRoleId(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const editItem = (it) => {
    const name = prompt("Name des Artikels:", it.name);
    if (name === null) return;
    const category = prompt("Kategorie:", it.category);
    if (category === null) return;
    const priceStr = prompt("Preis (₡):", it.price);
    if (priceStr === null) return;
    const price = Number(priceStr);
    if (Number.isNaN(price)) return alert("Bitte einen gültigen Preis eingeben.");
    if (!live) {
      setItems(items.map((x) => x.id === it.id ? { ...x, name, category, price } : x));
      return;
    }
    apiPostQuery(`/api/shop/items/${it.id}`, { name, category, price, role_id: it.roleId || "" }).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const removeItem = (it) => {
    if (!confirm(`"${it.name}" wirklich löschen?`)) return;
    if (!live) { setItems(items.filter((x) => x.id !== it.id)); return; }
    apiDelete(`/api/shop/items/${it.id}`).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  return (
    <>
      <SectionTitle eyebrow="Wirtschaft" title="Shop-System" />
      <Panel style={{ padding: 16, marginBottom: 8, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Artikelname"
          style={{ flex: 1.4, minWidth: 160, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <input
          value={newCategory} onChange={(e) => setNewCategory(e.target.value)} placeholder="Kategorie (z.B. Rollen)"
          style={{ flex: 1, minWidth: 140, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <input
          type="number" value={newPrice} onChange={(e) => setNewPrice(e.target.value)} placeholder="Preis"
          style={{ width: 110, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
        />
        <RoleSelect value={newRoleId} onChange={setNewRoleId} placeholder="Rolle vergeben (optional)" style={{ flex: 1, minWidth: 180 }} />
        <PrimaryBtn icon={Plus} onClick={createItem}>Artikel erstellen</PrimaryBtn>
      </Panel>
      <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 18 }}>
        Wenn eine Rolle hinterlegt ist, bekommt der Käufer sie beim Kauf automatisch — der Bot braucht dafür die Berechtigung "Rollen verwalten" und muss über der Rolle stehen.
      </div>
      {cats.length > 0 && (
        <div style={{ display: "flex", gap: 8, marginBottom: 18, flexWrap: "wrap" }}>
          {cats.map((c) => <Badge key={c} color={C.gold}>{c}</Badge>)}
        </div>
      )}
      {items.length === 0 ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Noch keine Artikel im Shop.</div></Panel>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 14 }}>
          {items.map((it) => (
            <Panel key={it.id} style={{ padding: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
                <Badge color={C.cyan}>{it.category}</Badge>
                <div style={{ display: "flex", gap: 6 }}>
                  <IconBtn icon={Pencil} onClick={() => editItem(it)} />
                  <IconBtn icon={Trash2} danger onClick={() => removeItem(it)} />
                </div>
              </div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 17, color: C.text, marginBottom: 6 }}>{it.name}</div>
              <div style={{ fontFamily: "'JetBrains Mono', monospace", color: C.gold, fontSize: 15, fontWeight: 700 }}>{fmtMoney(it.price)}</div>
              <div style={{ fontSize: 11.5, color: C.muted, marginTop: 4 }}>{it.sold}× verkauft{it.roleId ? " · vergibt Rolle" : ""}</div>
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

function DienstSection() {
  const { live } = useLive();
  const [duty, setDuty] = useState(DUTY);
  const [newName, setNewName] = useState("");
  const [newTotal, setNewTotal] = useState(5);
  const [newChannelId, setNewChannelId] = useState("");
  const [myFraction, setMyFraction] = useState(null);

  const refresh = () => {
    apiGet("/api/dienst").then(setDuty).catch(() => {});
    apiGet("/api/dienst/me").then((r) => setMyFraction(r.onDutyFraction)).catch(() => {});
  };

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const toggle = (d) => {
    if (!live) {
      setDuty(duty.map((x) => x.id === d.id ? { ...x, onDuty: x.onDuty > 0 ? 0 : Math.min(1, x.total) } : x));
      return;
    }
    apiPostQuery(`/api/dienst/${d.id}/toggle`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const createFraction = () => {
    if (!newName.trim()) return;
    if (!live) {
      setDuty([...duty, { id: Date.now(), fraction: newName, onDuty: 0, total: Number(newTotal), hoursToday: 0, channelId: newChannelId }]);
      setNewName(""); setNewTotal(5); setNewChannelId("");
      return;
    }
    apiPostQuery("/api/dienst", { name: newName, total: newTotal, channel_id: newChannelId || "" })
      .then(() => { setNewName(""); setNewTotal(5); setNewChannelId(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const removeFraction = (d) => {
    if (!live) { setDuty(duty.filter((x) => x.id !== d.id)); return; }
    apiDelete(`/api/dienst/${d.id}`).then(refresh)
      .catch(() => alert("Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  return (
    <>
      <SectionTitle eyebrow="Fraktionen" title="Dienstsystem" />
      {live && myFraction && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, marginBottom: 16, padding: "10px 14px",
          background: `${C.green}14`, border: `1px solid ${C.green}40`, borderRadius: 8,
          fontSize: 13, color: C.green, fontFamily: "'JetBrains Mono', monospace",
        }}>
          <Power size={14} /> Du bist aktuell im Dienst bei <strong>{myFraction}</strong>
        </div>
      )}
      <Panel style={{ padding: 16, marginBottom: 8, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Name der Fraktion (z.B. Polizei)"
          style={{ flex: 2, minWidth: 180, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <input
          type="number" value={newTotal} onChange={(e) => setNewTotal(e.target.value)} placeholder="Plätze"
          style={{ width: 90, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <ChannelSelect
          value={newChannelId} onChange={setNewChannelId} placeholder="Kanal für Dienst-Embeds (optional)"
          style={{ flex: 1, minWidth: 220 }}
        />
        <PrimaryBtn icon={Plus} onClick={createFraction}>Fraktion anlegen</PrimaryBtn>
      </Panel>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px,1fr))", gap: 14 }}>
        {duty.map((d) => (
          <Panel key={d.id || d.fraction} style={{ padding: 18 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text }}>{d.fraction}</div>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={() => toggle(d)}
                  style={{
                    display: "flex", alignItems: "center", gap: 5, border: `1px solid ${d.onDuty > 0 ? C.green : C.border}`,
                    background: d.onDuty > 0 ? `${C.green}1A` : "transparent", color: d.onDuty > 0 ? C.green : C.muted,
                    borderRadius: 6, padding: "5px 9px", fontSize: 11, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace",
                  }}>
                  <Power size={12} /> {d.onDuty > 0 ? "IM DIENST" : "AUSSER DIENST"}
                </button>
                <IconBtn icon={Trash2} danger onClick={() => removeFraction(d)} />
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, color: C.muted, marginBottom: 6 }}>
              <span>{d.onDuty} / {d.total} im Dienst</span>
              <span><Clock size={11} style={{ display: "inline", marginRight: 3 }} />{d.hoursToday}h gesamt</span>
            </div>
            {d.channelId && (
              <div style={{ fontSize: 10.5, color: C.muted, marginBottom: 8, fontFamily: "'JetBrains Mono', monospace" }}>
                📢 Embed-Kanal: {d.channelId}
              </div>
            )}
            <div style={{ height: 6, background: C.panelAlt, borderRadius: 99, overflow: "hidden" }}>
              <div style={{ width: `${(d.onDuty / d.total) * 100}%`, height: "100%", background: C.cyan }} />
            </div>
          </Panel>
        ))}
      </div>
    </>
  );
}


function AfkSection() {
  const { live } = useLive();
  const [list, setList] = useState([]);
  const [myAfk, setMyAfk] = useState(null);
  const [reason, setReason] = useState("");

  const refresh = () => {
    apiGet("/api/afk").then(setList).catch(() => {});
    apiGet("/api/afk/me").then(setMyAfk).catch(() => {});
  };

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const setAfk = () => {
    if (!live) return alert("Nur im Live-Modus möglich.");
    apiPostQuery("/api/afk/set", { grund: reason || "Kein Grund angegeben" })
      .then(() => { setReason(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const clearAfk = () => {
    if (!live) return alert("Nur im Live-Modus möglich.");
    apiPostQuery("/api/afk/clear", {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  return (
    <>
      <SectionTitle eyebrow="Status" title="AFK-System" />

      {live && myAfk?.reason ? (
        <Panel style={{ padding: 16, marginBottom: 18, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", background: `${C.gold}0F`, borderColor: `${C.gold}40` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: C.gold, fontSize: 13 }}>
            <Moon size={16} /> Du bist aktuell AFK: <strong>{myAfk.reason}</strong>
          </div>
          <PrimaryBtn onClick={clearAfk}>AFK beenden</PrimaryBtn>
        </Panel>
      ) : (
        <Panel style={{ padding: 16, marginBottom: 18, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <input
            value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Grund (optional)"
            style={{ flex: 1, minWidth: 200, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
          />
          <PrimaryBtn icon={Moon} onClick={setAfk}>AFK setzen</PrimaryBtn>
        </Panel>
      )}

      <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 10 }}>
        Aktuell AFK ({list.length})
      </div>
      <Panel style={{ overflow: "hidden" }}>
        {list.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Niemand ist gerade AFK.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><Th>Benutzer</Th><Th>Grund</Th><Th align="right">Seit</Th></tr></thead>
            <tbody>
              {list.map((u) => (
                <tr key={u.id}>
                  <Td>{u.name}</Td>
                  <Td style={{ color: C.muted }}>{u.reason}</Td>
                  <Td align="right" style={{ color: C.muted, fontSize: 12 }}>{u.since ? new Date(u.since).toLocaleString("de-DE") : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

function GiveawaySection() {
  const { live } = useLive();
  const [list, setList] = useState(GIVEAWAYS);
  const [preis, setPreis] = useState("");
  const [dauer, setDauer] = useState(60);
  const [channelId, setChannelId] = useState("");

  const refresh = () => apiGet("/api/giveaways").then(setList).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
    const interval = setInterval(refresh, 15000);
    return () => clearInterval(interval);
  }, [live]);

  const create = () => {
    if (!preis.trim() || !dauer || !channelId.trim()) return;
    if (!live) return alert("Nur im Live-Modus möglich.");
    apiPostQuery("/api/giveaways", { preis, dauer_minuten: dauer, channel_id: channelId })
      .then(() => { setPreis(""); setDauer(60); setChannelId(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const end = (g) => {
    if (!confirm(`Giveaway "${g.prize}" jetzt beenden und auslosen?`)) return;
    apiPostQuery(`/api/giveaways/${g.id}/end`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen"));
  };

  const reroll = (g) => {
    if (!confirm(`Neuen Gewinner für "${g.prize}" auslosen?`)) return;
    apiPostQuery(`/api/giveaways/${g.id}/reroll`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen"));
  };

  return (
    <>
      <SectionTitle eyebrow="Community" title="Giveaway-System" />
      <Panel style={{ padding: 16, marginBottom: 8, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={preis} onChange={(e) => setPreis(e.target.value)} placeholder="Preis (z.B. VIP Gold Rolle)"
          style={{ flex: 1.4, minWidth: 180, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <input
          type="number" value={dauer} onChange={(e) => setDauer(e.target.value)} placeholder="Minuten"
          style={{ width: 100, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontFamily: "'JetBrains Mono', monospace", fontSize: 13 }}
        />
        <ChannelSelect
          value={channelId} onChange={setChannelId} placeholder="Kanal wählen…"
          style={{ flex: 1, minWidth: 160 }}
        />
        <PrimaryBtn icon={Gift} onClick={create}>Giveaway erstellen</PrimaryBtn>
      </Panel>
      <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 18 }}>
        Teilnahme läuft über eine 🎉-Reaktion auf die gepostete Nachricht.
      </div>
      {list.length === 0 ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Noch keine Giveaways.</div></Panel>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px,1fr))", gap: 14 }}>
          {list.map((g) => (
            <Panel key={g.id} style={{ padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                <Badge color={g.status === "aktiv" ? C.green : C.muted}>{g.status === "aktiv" ? "Aktiv" : "Beendet"}</Badge>
                <span style={{ fontSize: 10.5, color: C.muted, fontFamily: "'JetBrains Mono', monospace" }}>#{g.id}</span>
              </div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 17, color: C.text, marginBottom: 8 }}>{g.prize}</div>
              <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 4 }}>{g.entries} Teilnahmen</div>
              <div style={{ fontSize: 12.5, color: C.muted, marginBottom: 12 }}>
                {g.status === "aktiv" ? `Endet: ${g.ends ? new Date(g.ends).toLocaleString("de-DE") : "—"}` : `Gewinner: ${g.winner || "niemand teilgenommen"}`}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {g.status === "aktiv" ? (
                  <PrimaryBtn onClick={() => end(g)}>Jetzt beenden</PrimaryBtn>
                ) : (
                  <PrimaryBtn onClick={() => reroll(g)}>Neu auslosen</PrimaryBtn>
                )}
              </div>
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

function LogsSection() {
  const { live } = useLive();
  const [filter, setFilter] = useState("alle");
  const [logList, setLogList] = useState(LOGS);
  const types = ["alle", ...Object.keys(LOG_META)];

  useEffect(() => {
    if (!live) return;
    apiGet(`/api/logs?type=${filter}`).then(setLogList).catch(() => {});
  }, [live, filter]);

  const filtered = live ? logList : (filter === "alle" ? LOGS : LOGS.filter((l) => l.type === filter));
  return (
    <>
      <SectionTitle eyebrow="Protokoll" title="Audit Logs" />
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        {types.map((t) => (
          <button key={t} onClick={() => setFilter(t)} style={{
            padding: "6px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer",
            fontFamily: "'JetBrains Mono', monospace", textTransform: "capitalize",
            border: `1px solid ${filter === t ? C.gold : C.border}`,
            background: filter === t ? `${C.gold}1A` : "transparent",
            color: filter === t ? C.gold : C.muted,
          }}>
            {t === "alle" ? "Alle" : LOG_META[t].label}
          </button>
        ))}
      </div>
      <Panel style={{ overflow: "hidden" }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Keine Einträge gefunden.</div>
        ) : filtered.map((l, i) => (
          <div key={l.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderBottom: i < filtered.length - 1 ? `1px solid ${C.border}` : "none" }}>
            <Badge color={(LOG_META[l.type] || LOG_META.system).color}>{(LOG_META[l.type] || LOG_META.system).label}</Badge>
            <span style={{ flex: 1, fontSize: 13, color: C.text }}>{l.text}</span>
            <span style={{ fontSize: 11.5, color: C.muted, fontFamily: "'JetBrains Mono', monospace" }}>
              {/^\d{4}-\d{2}-\d{2}/.test(l.time) ? new Date(l.time).toLocaleString("de-DE") : l.time}
            </span>
          </div>
        ))}
      </Panel>
    </>
  );
}

function StatsSection() {
  const { live } = useLive();
  const [stats, setStats] = useState(null);

  useEffect(() => {
    if (!live) return;
    apiGet("/api/stats").then(setStats).catch(() => {});
  }, [live]);

  const days = stats?.weekly_activity?.map((d) => d.day) || ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
  const counts = stats?.weekly_activity?.map((d) => d.count) || [0, 0, 0, 0, 0, 0, 0];
  const max = Math.max(1, ...counts);

  return (
    <>
      <SectionTitle eyebrow="Auswertung" title="Statistiken" />
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 18 }}>
        <StatCard icon={Users} label="Mitglieder" value={stats ? stats.member_count : "0"} accent={C.cyan} />
        <StatCard icon={Activity} label="Aktive Nutzer (7T)" value={stats ? stats.active_users_7d : "0"} accent={C.green} />
        <StatCard icon={Coins} label="Gesamtvermögen" value={fmtMoney(stats ? stats.total_balance : 0)} accent={C.gold} />
        <StatCard icon={ShoppingBag} label="Shop-Verkäufe" value={stats ? stats.shop_sales : "0"} accent={C.cyan} />
        <StatCard icon={Clock} label="Dienststunden (gesamt)" value={`${stats ? stats.duty_hours_today : 0}h`} accent={C.green} />
        <StatCard icon={Gift} label="Giveaways gesamt" value={stats ? stats.giveaway_count : "0"} accent={C.gold} />
        <StatCard icon={Activity} label="Bot-Uptime" value={formatUptime(stats?.uptime_seconds)} accent={C.text} />
      </div>
      <Panel style={{ padding: 20 }}>
        <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 18 }}>
          Aktivität diese Woche (Anzahl protokollierter Aktionen)
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 14, height: 140 }}>
          {counts.map((c, i) => (
            <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 10.5, color: C.muted, fontFamily: "'JetBrains Mono', monospace" }}>{c}</div>
              <div style={{ width: "100%", height: `${Math.max(2, (c / max) * 100)}%`, borderRadius: "4px 4px 0 0", background: `linear-gradient(to top, ${C.gold}, ${C.cyan})` }} />
              <span style={{ fontSize: 11, color: C.muted, fontFamily: "'JetBrains Mono', monospace" }}>{days[i]}</span>
            </div>
          ))}
        </div>
      </Panel>
    </>
  );
}

function UsersSection() {
  const { live } = useLive();
  const [q, setQ] = useState("");
  const [userList, setUserList] = useState(USERS);

  const refresh = () => apiGet("/api/users").then(setUserList).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const filtered = userList.filter((u) => u.name.toLowerCase().includes(q.toLowerCase()));

  return (
    <>
      <SectionTitle eyebrow="Übersicht" title="Benutzer" />
      <div style={{ position: "relative", marginBottom: 16, maxWidth: 320 }}>
        <Search size={15} style={{ position: "absolute", left: 11, top: 10, color: C.muted }} />
        <input
          value={q} onChange={(e) => setQ(e.target.value)} placeholder="Benutzer suchen…"
          style={{ width: "100%", background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 7, padding: "9px 12px 9px 32px", color: C.text, fontSize: 13 }}
        />
      </div>
      {filtered.length === 0 ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Keine Benutzer gefunden.</div></Panel>
      ) : (
      <Panel style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><Th>Benutzer</Th><Th>Status</Th><Th>Rolle</Th><Th>Kontostand</Th><Th>Dienst</Th><Th>AFK</Th><Th>Beigetreten</Th></tr></thead>
          <tbody>
            {filtered.map((u) => (
              <tr key={u.id}>
                <Td>
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <div style={{ width: 26, height: 26, borderRadius: 7, background: C.panelAlt, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: C.gold, fontFamily: "'Rajdhani', sans-serif" }}>
                      {u.name?.[0]?.toUpperCase() || "?"}
                    </div>
                    {u.name}
                  </div>
                </Td>
                <Td><span style={{ display: "flex", alignItems: "center", gap: 6 }}><StatusDot status={u.status} /><span style={{ color: C.muted, fontSize: 12, textTransform: "capitalize" }}>{u.status}</span></span></Td>
                <Td style={{ color: C.muted, fontSize: 12.5 }}>{u.role}</Td>
                <Td style={{ fontFamily: "'JetBrains Mono', monospace", color: C.gold }}>{fmtMoney(u.balance)}</Td>
                <Td>{u.onDutyFraction ? <Badge color={C.green}>{u.onDutyFraction}</Badge> : <span style={{ color: C.muted, fontSize: 12 }}>—</span>}</Td>
                <Td>{u.afkReason ? <Badge color={C.gold}>AFK</Badge> : <span style={{ color: C.muted, fontSize: 12 }}>—</span>}</Td>
                <Td style={{ color: C.muted, fontSize: 12 }}>{new Date(u.joined).toLocaleDateString("de-DE")}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      )}
    </>
  );
}

function SecuritySection() {
  const { live } = useLive();
  const [sessions, setSessions] = useState([]);
  const [overview, setOverview] = useState(null);

  const refresh = () => apiGet("/api/security/sessions").then(setSessions).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
    apiGet("/api/security/overview").then(setOverview).catch(() => {});
  }, [live]);

  const revokeSession = (s) => {
    if (!confirm("Diese Sitzung wirklich beenden? Das Gerät muss sich danach neu anmelden.")) return;
    apiPostQuery(`/api/security/sessions/${s.id}/revoke`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen"));
  };

  return (
    <>
      <SectionTitle eyebrow="Zugriff" title="Sicherheit" />
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 18 }}>
        <Panel style={{ padding: 18, flex: 1, minWidth: 220 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <LogIn size={16} color={C.cyan} />
            <span style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, color: C.text }}>Discord Login</span>
          </div>
          <div style={{ fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>
            Anmeldung über OAuth2 · {overview ? overview.logins_24h : 0} Anmeldungen in den letzten 24h
          </div>
          <div style={{ marginTop: 10 }}><Badge color={C.green}>Aktiv</Badge></div>
        </Panel>
        <Panel style={{ padding: 18, flex: 1, minWidth: 220 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <KeyRound size={16} color={C.gold} />
            <span style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, color: C.text }}>Admin-Berechtigungen</span>
          </div>
          <div style={{ fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>
            {overview ? overview.admin_count : 0} Nutzer mit Admin/Owner-Rolle (unter Benutzerverwaltung einstellbar)
          </div>
        </Panel>
      </div>
      <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 10 }}>Login-Verlauf</div>
      {sessions.length === 0 ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Noch keine Logins aufgezeichnet.</div></Panel>
      ) : (
      <Panel style={{ overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><Th>Benutzer</Th><Th>Gerät</Th><Th>IP-Adresse</Th><Th>Zeitpunkt</Th><Th align="right">Aktion</Th></tr></thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id}>
                <Td>{s.user}</Td><Td style={{ color: C.muted }}>{s.device}</Td>
                <Td style={{ fontFamily: "'JetBrains Mono', monospace", color: C.muted, fontSize: 12 }}>{s.ip}</Td>
                <Td style={{ color: C.muted, fontSize: 12 }}>{new Date(s.time).toLocaleString("de-DE")}</Td>
                <Td align="right">
                  {s.isMine ? (
                    <button
                      onClick={() => revokeSession(s)}
                      style={{ background: "transparent", border: `1px solid ${C.red}60`, color: C.red, borderRadius: 6, padding: "5px 10px", fontSize: 11.5, cursor: "pointer" }}
                    >
                      Beenden
                    </button>
                  ) : (
                    <span style={{ color: C.muted, fontSize: 11 }}>—</span>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
      )}
    </>
  );
}

const SETTINGS_GROUPS = [
  { title: "Bot-Einstellungen", fields: [["log_kanal", "Log-Kanal", "channel"]] },
  { title: "Bank-Einstellungen", fields: [["startguthaben", "Startguthaben"], ["max_ueberweisung", "Max. Überweisungsbetrag"], ["zinssatz_taeglich", "Zinssatz (täglich, %)"]] },
  { title: "Shop-Einstellungen", fields: [["shop_kaufbestaetigung", "Kaufbestätigung erforderlich (ja/nein)"]] },
  { title: "Dienst-Einstellungen", fields: [["dienst_verguetung", "Vergütung pro Stunde"], ["dienst_auto_ende", "Automatischer Dienstende nach (Minuten)"]] },
  { title: "Ticket-Einstellungen", fields: [["ticket_kategorie", "Kategorie für Ticket-Kanäle", "category"], ["ticket_support_rolle", "Support-Rolle (sieht alle Tickets)", "role"]] },
  { title: "Rollen & Kanäle", fields: [["admin_rolle", "Admin-Rolle", "role"]] },
  { title: "Design", fields: [["welcome_banner_url", "Dashboard-Banner (Bild-URL)"]] },
];

function SettingsSection() {
  const { live } = useLive();
  const [values, setValues] = useState({});
  const [savedGroup, setSavedGroup] = useState(null);

  useEffect(() => {
    if (!live) return;
    apiGet("/api/settings").then(setValues).catch(() => {});
  }, [live]);

  const setField = (key, val) => setValues((v) => ({ ...v, [key]: val }));

  const saveGroup = (group) => {
    const payload = {};
    group.fields.forEach(([key]) => { payload[key] = values[key] ?? ""; });
    if (!live) return alert("Nur im Live-Modus möglich.");
    apiPost("/api/settings", payload)
      .then(() => { setSavedGroup(group.title); setTimeout(() => setSavedGroup(null), 2000); })
      .catch((err) => alert(err.message || "Speichern fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  return (
    <>
      <SectionTitle eyebrow="Konfiguration" title="Einstellungen" />
      <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 14 }}>
        Änderungen gelten sofort, nachdem du auf "Speichern" geklickt hast — pro Gruppe einzeln.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px,1fr))", gap: 14 }}>
        {SETTINGS_GROUPS.map((g) => (
          <Panel key={g.title} style={{ padding: 18 }}>
            <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 15, color: C.text, marginBottom: 14 }}>{g.title}</div>
            {g.fields.map(([key, label, type]) => (
              <div key={key} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 5 }}>{label}</div>
                {type === "channel" ? (
                  <ChannelSelect value={values[key] ?? ""} onChange={(v) => setField(key, v)} style={{ width: "100%" }} />
                ) : type === "category" ? (
                  <CategorySelect value={values[key] ?? ""} onChange={(v) => setField(key, v)} style={{ width: "100%" }} />
                ) : type === "role" ? (
                  <RoleSelect value={values[key] ?? ""} onChange={(v) => setField(key, v)} style={{ width: "100%" }} />
                ) : (
                  <input
                    value={values[key] ?? ""} onChange={(e) => setField(key, e.target.value)}
                    style={{ width: "100%", background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 12.5, fontFamily: "'JetBrains Mono', monospace" }}
                    placeholder="—"
                  />
                )}
              </div>
            ))}
            <PrimaryBtn onClick={() => saveGroup(g)}>{savedGroup === g.title ? "Gespeichert ✓" : "Speichern"}</PrimaryBtn>
          </Panel>
        ))}
      </div>
    </>
  );
}

function ModuleSection() {
  const { live } = useLive();
  const [modules, setModules] = useState({});
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    apiGet("/api/modules").then((m) => { setModules(m); setLoading(false); }).catch(() => setLoading(false));
  };

  useEffect(() => {
    if (!live) { setLoading(false); return; }
    refresh();
  }, [live]);

  const toggleModule = (key, current) => {
    if (!live) {
      setModules((m) => ({ ...m, [key]: { ...m[key], enabled: !current } }));
      return;
    }
    // Optimistisch umschalten, bei Fehler zurücksetzen
    setModules((m) => ({ ...m, [key]: { ...m[key], enabled: !current } }));
    apiPost(`/api/modules/${key}`, { enabled: !current }).catch((err) => {
      setModules((m) => ({ ...m, [key]: { ...m[key], enabled: current } }));
      alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?");
    });
  };

  const entries = Object.entries(modules);

  return (
    <>
      <SectionTitle eyebrow="Feature-Verwaltung" title="Module" />
      <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 14 }}>
        Schalte einzelne Systeme pro Server an oder aus. Deaktivierte Module lehnen die zugehörigen Befehle mit einer klaren Meldung ab.
      </div>
      {loading ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Lädt…</div></Panel>
      ) : entries.length === 0 ? (
        <Panel style={{ padding: 18 }}><div style={{ fontSize: 13, color: C.muted }}>Keine Module gefunden — bist du mit Discord angemeldet und ein Server ausgewählt?</div></Panel>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px,1fr))", gap: 14 }}>
          {entries.map(([key, mod]) => (
            <Panel key={key} style={{ padding: 18, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 15, color: C.text }}>{mod.name}</div>
                <Badge color={mod.enabled ? C.green : C.red} bg={undefined}>
                  <StatusDot status={mod.enabled ? "online" : "offline"} /> {mod.enabled ? "Aktiv" : "Deaktiviert"}
                </Badge>
              </div>
              <Toggle checked={mod.enabled} onChange={() => toggleModule(key, mod.enabled)} />
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

const TEAM_ROLES = ["Support", "Moderator", "Admin", "Owner"];

function TeamSection() {
  const { live } = useLive();
  const [team, setTeam] = useState([]);
  const [allUsers, setAllUsers] = useState([]);
  const [search, setSearch] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const refresh = () => {
    apiGet("/api/team").then(setTeam).catch(() => {});
    apiGet("/api/users").then(setAllUsers).catch(() => {});
  };

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const changeRole = (member, role) => {
    if (!live) { setTeam(team.map((x) => x.id === member.id ? { ...x, role } : x)); return; }
    apiPostQuery(`/api/users/${member.id}/role`, { role }).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const removeFromTeam = (member) => {
    if (!confirm(`${member.name} wirklich aus dem Team entfernen?`)) return;
    changeRole(member, "Mitglied");
  };

  const addToTeam = (u, role) => {
    if (!live) { setTeam([...team, { id: u.id, name: u.name, role, status: u.status, joined: u.joined }]); setShowAdd(false); return; }
    apiPostQuery(`/api/users/${u.id}/role`, { role }).then(() => { refresh(); setShowAdd(false); setSearch(""); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const teamIds = new Set(team.map((t) => t.id));
  const searchResults = search.trim()
    ? allUsers.filter((u) => !teamIds.has(u.id) && u.name.toLowerCase().includes(search.toLowerCase())).slice(0, 8)
    : [];

  return (
    <>
      <SectionTitle
        eyebrow="Verwaltung" title="Team"
        action={<PrimaryBtn icon={Plus} onClick={() => setShowAdd((v) => !v)}>Team-Mitglied hinzufügen</PrimaryBtn>}
      />
      {showAdd && (
        <Panel style={{ padding: 16, marginBottom: 14 }}>
          <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 8 }}>Nutzer suchen und Team-Rolle vergeben</div>
          <input
            value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name eingeben…"
            style={{ width: "100%", background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13, marginBottom: 10 }}
          />
          {searchResults.map((u) => (
            <div key={u.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 4px", borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontSize: 13, color: C.text }}>{u.name}</span>
              <div style={{ display: "flex", gap: 6 }}>
                {TEAM_ROLES.map((r) => (
                  <button
                    key={r} onClick={() => addToTeam(u, r)}
                    style={{ fontSize: 11, padding: "4px 9px", borderRadius: 5, border: `1px solid ${C.border}`, background: "transparent", color: C.muted, cursor: "pointer" }}
                  >
                    als {r}
                  </button>
                ))}
              </div>
            </div>
          ))}
          {search.trim() && searchResults.length === 0 && (
            <div style={{ fontSize: 12.5, color: C.muted, padding: "6px 4px" }}>Keine passenden Mitglieder gefunden.</div>
          )}
        </Panel>
      )}
      <Panel style={{ padding: 0, overflow: "hidden" }}>
        {team.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Noch keine Team-Mitglieder. Admin/Owner werden automatisch über die Discord-Rolle synchronisiert, weitere Rollen kannst du hier manuell vergeben.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr><Th>Name</Th><Th>Status</Th><Th>Rolle</Th><Th align="right">Aktion</Th></tr>
            </thead>
            <tbody>
              {team.map((m) => (
                <tr key={m.id}>
                  <Td>{m.name}</Td>
                  <Td><Badge color={m.status === "online" ? C.green : m.status === "idle" ? C.gold : C.muted}><StatusDot status={m.status} /> {m.status}</Badge></Td>
                  <Td>
                    <select
                      value={m.role} onChange={(e) => changeRole(m, e.target.value)}
                      style={{ background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "5px 8px", color: C.text, fontSize: 12.5 }}
                    >
                      {TEAM_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </Td>
                  <Td align="right"><IconBtn icon={Trash2} danger onClick={() => removeFromTeam(m)} /></Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

function TodoSection() {
  const { live } = useLive();
  const [todos, setTodos] = useState([]);
  const [title, setTitle] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [filter, setFilter] = useState("offen");

  const refresh = () => apiGet("/api/todos").then(setTodos).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const addTodo = () => {
    if (!title.trim()) return;
    if (!live) {
      setTodos([{ id: Date.now(), title, status: "offen", assigned_to: assignedTo || null, created_by: "Du" }, ...todos]);
      setTitle(""); setAssignedTo("");
      return;
    }
    apiPostQuery("/api/todos", { title, assigned_to: assignedTo }).then(() => { setTitle(""); setAssignedTo(""); refresh(); })
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const toggleTodo = (t) => {
    if (!live) { setTodos(todos.map((x) => x.id === t.id ? { ...x, status: x.status === "erledigt" ? "offen" : "erledigt" } : x)); return; }
    apiPostQuery(`/api/todos/${t.id}/toggle`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const removeTodo = (t) => {
    if (!live) { setTodos(todos.filter((x) => x.id !== t.id)); return; }
    apiDelete(`/api/todos/${t.id}`).then(refresh)
      .catch(() => alert("Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const filtered = todos.filter((t) => filter === "alle" ? true : t.status === filter);

  return (
    <>
      <SectionTitle eyebrow="Organisation" title="To-Do-Liste" />
      <Panel style={{ padding: 16, marginBottom: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <input
          value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Neue Aufgabe…"
          onKeyDown={(e) => e.key === "Enter" && addTodo()}
          style={{ flex: 2, minWidth: 200, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <input
          value={assignedTo} onChange={(e) => setAssignedTo(e.target.value)} placeholder="Zugewiesen an (optional)"
          onKeyDown={(e) => e.key === "Enter" && addTodo()}
          style={{ flex: 1, minWidth: 160, background: C.panelAlt, border: `1px solid ${C.border}`, borderRadius: 6, padding: "8px 10px", color: C.text, fontSize: 13 }}
        />
        <PrimaryBtn icon={Plus} onClick={addTodo}>Hinzufügen</PrimaryBtn>
      </Panel>
      <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
        {["offen", "erledigt", "alle"].map((f) => (
          <button
            key={f} onClick={() => setFilter(f)}
            style={{
              padding: "6px 12px", borderRadius: 6, fontSize: 12.5, cursor: "pointer",
              border: `1px solid ${filter === f ? C.gold : C.border}`,
              background: filter === f ? `${C.gold}14` : "transparent",
              color: filter === f ? C.gold : C.muted,
            }}
          >
            {f === "offen" ? "Offen" : f === "erledigt" ? "Erledigt" : "Alle"}
          </button>
        ))}
      </div>
      <Panel style={{ padding: 0, overflow: "hidden" }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Keine Aufgaben in dieser Ansicht.</div>
        ) : filtered.map((t, i) => (
          <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px", borderBottom: i < filtered.length - 1 ? `1px solid ${C.border}` : "none" }}>
            <button
              onClick={() => toggleTodo(t)}
              style={{
                width: 20, height: 20, borderRadius: 5, flexShrink: 0, cursor: "pointer",
                border: `1.5px solid ${t.status === "erledigt" ? C.green : C.border}`,
                background: t.status === "erledigt" ? C.green : "transparent",
                display: "flex", alignItems: "center", justifyContent: "center", color: "#0A0E13", fontSize: 12, fontWeight: 700,
              }}
            >
              {t.status === "erledigt" ? "✓" : ""}
            </button>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13.5, color: t.status === "erledigt" ? C.muted : C.text, textDecoration: t.status === "erledigt" ? "line-through" : "none" }}>
                {t.title}
              </div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>
                {t.assigned_to ? `Zugewiesen an ${t.assigned_to} · ` : ""}von {t.created_by}
              </div>
            </div>
            <IconBtn icon={Trash2} danger onClick={() => removeTodo(t)} />
          </div>
        ))}
      </Panel>
    </>
  );
}

function TicketsSection() {
  const { live } = useLive();
  const [tickets, setTickets] = useState([]);
  const [filter, setFilter] = useState("offen");

  const refresh = () => apiGet("/api/tickets").then(setTickets).catch(() => {});

  useEffect(() => {
    if (!live) return;
    refresh();
  }, [live]);

  const closeTicket = (t) => {
    if (!live) { setTickets(tickets.map((x) => x.id === t.id ? { ...x, status: "geschlossen" } : x)); return; }
    if (!confirm(`Ticket #${t.id} von ${t.username} wirklich schließen? Der Kanal wird gelöscht.`)) return;
    apiPost(`/api/tickets/${t.id}/close`, {}).then(refresh)
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const filtered = tickets.filter((t) => filter === "alle" ? true : t.status === filter);

  return (
    <>
      <SectionTitle eyebrow="Support" title="Tickets" />
      <div style={{ fontSize: 11.5, color: C.muted, marginBottom: 14 }}>
        Nutzer öffnen Tickets über <code style={{ color: C.gold }}>/ticket</code> im Discord-Server. Vergiss nicht, unter Einstellungen eine Kategorie und optional eine Support-Rolle festzulegen.
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
        {["offen", "geschlossen", "alle"].map((f) => (
          <button
            key={f} onClick={() => setFilter(f)}
            style={{
              padding: "6px 12px", borderRadius: 6, fontSize: 12.5, cursor: "pointer",
              border: `1px solid ${filter === f ? C.gold : C.border}`,
              background: filter === f ? `${C.gold}14` : "transparent",
              color: filter === f ? C.gold : C.muted,
            }}
          >
            {f === "offen" ? "Offen" : f === "geschlossen" ? "Geschlossen" : "Alle"}
          </button>
        ))}
      </div>
      <Panel style={{ padding: 0, overflow: "hidden" }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 18, fontSize: 13, color: C.muted }}>Keine Tickets in dieser Ansicht.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <Th>#</Th><Th>Nutzer</Th><Th>Betreff</Th><Th>Status</Th><Th>Erstellt</Th><Th align="right">Aktion</Th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <tr key={t.id}>
                  <Td>{t.id}</Td>
                  <Td>{t.username}</Td>
                  <Td>{t.subject || "—"}</Td>
                  <Td>
                    <Badge color={t.status === "offen" ? C.green : C.muted}>
                      <StatusDot status={t.status === "offen" ? "online" : "offline"} /> {t.status === "offen" ? "Offen" : "Geschlossen"}
                    </Badge>
                  </Td>
                  <Td>{t.created_at ? new Date(t.created_at).toLocaleString("de-DE") : "—"}</Td>
                  <Td align="right">
                    {t.status === "offen" ? (
                      <PrimaryBtn style={{ padding: "5px 10px", fontSize: 11.5 }} onClick={() => closeTicket(t)}>Schließen</PrimaryBtn>
                    ) : (
                      <span style={{ fontSize: 11.5, color: C.muted }}>von {t.closed_by || "—"}</span>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

function BotSection() {
  const { live } = useLive();
  const [overview, setOverview] = useState(null);
  const [maintenance, setMaintenance] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const refresh = () => {
    apiGet("/api/overview").then(setOverview).catch(() => {});
    apiGet("/api/settings").then((s) => setMaintenance(["ja", "yes", "true", "1", "an"].includes((s.wartungsmodus || "").toLowerCase()))).catch(() => {});
  };

  useEffect(() => {
    if (!live) return;
    refresh();
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [live]);

  const toggleMaintenance = (val) => {
    if (!live) { setMaintenance(val); return; }
    apiPost("/api/settings", { wartungsmodus: val ? "ja" : "nein" }).then(() => setMaintenance(val))
      .catch((err) => alert(err.message || "Fehlgeschlagen — bist du mit Discord angemeldet?"));
  };

  const doSync = () => {
    if (!live) { alert("Nur im Live-Modus möglich."); return; }
    setSyncing(true);
    apiPost("/api/bot/sync", {})
      .then((r) => alert(`✅ ${r.synced_count} Befehle synchronisiert.`))
      .catch((err) => alert(err.message || "Sync fehlgeschlagen — bist du mit Discord angemeldet?"))
      .finally(() => setSyncing(false));
  };

  return (
    <>
      <SectionTitle eyebrow="Systemstatus" title="Bot" />
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginBottom: 14 }}>
        <StatCard icon={Activity} label="Status" value={overview?.bot_status === "online" ? "Online" : "Offline"} accent={overview?.bot_status === "online" ? C.green : C.red} />
        <StatCard icon={Clock} label="Uptime" value={formatUptime(overview?.uptime_seconds)} accent={C.text} />
        <StatCard icon={Users} label="Mitglieder" value={overview ? overview.member_count : "0"} accent={C.cyan} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px,1fr))", gap: 14 }}>
        <Panel style={{ padding: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <RefreshCw size={16} color={C.cyan} />
            <span style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, color: C.text }}>Befehle synchronisieren</span>
          </div>
          <div style={{ fontSize: 12.5, color: C.muted, lineHeight: 1.5, marginBottom: 12 }}>
            Registriert alle Slash-Commands neu bei Discord. Nötig, wenn neue Befehle nicht auftauchen.
          </div>
          <PrimaryBtn icon={RefreshCw} onClick={doSync} disabled={syncing}>{syncing ? "Synchronisiere…" : "Jetzt synchronisieren"}</PrimaryBtn>
        </Panel>

        <Panel style={{ padding: 18 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <WifiOff size={16} color={C.gold} />
              <span style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, color: C.text }}>Wartungsmodus</span>
            </div>
            <Toggle checked={maintenance} onChange={toggleMaintenance} />
          </div>
          <div style={{ fontSize: 12.5, color: C.muted, lineHeight: 1.5 }}>
            Blockt alle Befehle für normale Mitglieder (Administratoren können den Bot weiter nutzen).
          </div>
        </Panel>
      </div>
    </>
  );
}

const SECTIONS = {
  dashboard: DashboardSection, bank: BankSection, shop: ShopSection, dienst: DienstSection,
  afk: AfkSection, team: TeamSection, giveaway: GiveawaySection, todo: TodoSection, logs: LogsSection,
  tickets: TicketsSection, stats: StatsSection, users: UsersSection, security: SecuritySection,
  bot: BotSection, module: ModuleSection, settings: SettingsSection,
};

/* ---------------------------------------------------------
   APP
--------------------------------------------------------- */
function GuildSelector({ guilds }) {
  return (
    <>
      <SectionTitle eyebrow="Mehrserver-Unterstützung" title="Server auswählen" />
      <div style={{ fontSize: 13, color: C.muted, marginBottom: 18, maxWidth: 560 }}>
        Der Bot läuft auf mehreren Servern. Wähle, für welchen Server du gerade das Dashboard sehen und verwalten möchtest —
        jeder Server hat seine eigenen, komplett getrennten Daten.
      </div>
      {guilds.length === 0 ? (
        <Panel style={{ padding: 18 }}>
          <div style={{ fontSize: 13, color: C.muted }}>
            Der Bot ist auf keinem gemeinsamen Server mit deinem Discord-Konto zu finden.
          </div>
        </Panel>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px,1fr))", gap: 14 }}>
          {guilds.map((g) => (
            <Panel key={g.id} style={{ padding: 18, cursor: "pointer" }} onClick={() => selectGuild(g.id)}>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 16, color: C.text, marginBottom: 10 }}>
                {g.name}
              </div>
              <PrimaryBtn onClick={() => selectGuild(g.id)}>Auswählen</PrimaryBtn>
            </Panel>
          ))}
        </div>
      )}
    </>
  );
}

export default function DiscordBotDashboard() {
  const [active, setActive] = useState("dashboard");
  const [live, setLive] = useState(false);
  const [user, setUser] = useState(null);
  const [overview, setOverview] = useState(null);
  const [myGuilds, setMyGuilds] = useState(null); // null = noch nicht geladen
  const [guildInfo, setGuildInfo] = useState(null);
  const Active = SECTIONS[active];

  useEffect(() => {
    let cancelled = false;
    // Verbindungscheck: klappt der Aufruf, ist die API erreichbar -> Live-Modus.
    apiGet("/api/overview")
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        setLive(true);
      })
      .catch(() => {
        // Backend nicht erreichbar (oder noch kein Server ausgewählt) -> Demo-Modus.
        setLive(false);
      });
    if (GuildState.id) {
      apiGet("/api/guild-info").then((gi) => !cancelled && setGuildInfo(gi)).catch(() => {});
    }
    apiGet("/auth/me")
      .then((u) => {
        if (cancelled) return;
        setUser(u);
        apiGet("/api/my-guilds").then((gs) => !cancelled && setMyGuilds(gs)).catch(() => !cancelled && setMyGuilds([]));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const needsGuildSelection = user && myGuilds && myGuilds.length > 0 &&
    !myGuilds.some((g) => g.id === GuildState.id);

  return (
    <LiveContext.Provider value={{ live, user, myGuilds }}>
    <div style={{ background: C.bg, minHeight: "100vh", color: C.text, fontFamily: "'Inter', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; }
        html, body, #root { margin: 0; padding: 0; width: 100%; min-height: 100%; background: ${C.bg}; }
        input:focus, select:focus { outline: none; border-color: ${C.gold} !important; }
        ::-webkit-scrollbar { height: 6px; width: 6px; }
        ::-webkit-scrollbar-thumb { background: ${C.border}; border-radius: 99px; }

        .app-layout { display: flex; }
        .app-sidebar { width: 232px; flex-shrink: 0; }
        .app-main { flex: 1; min-width: 0; padding: 28px 32px; max-width: 1180px; }

        @media (max-width: 860px) {
          .app-layout { flex-direction: column; }
          .app-sidebar { width: 100%; min-height: auto !important; overflow-x: auto; padding: 12px !important; }
          .app-sidebar-nav { display: flex; flex-direction: row !important; gap: 4px; }
          .app-sidebar-nav button { white-space: nowrap; width: auto !important; flex-shrink: 0; }
          .app-main { padding: 16px !important; max-width: 100%; }
        }
      `}</style>

      <Ticker overview={overview} />

      <div className="app-layout">
        {/* Sidebar */}
        <div className="app-sidebar" style={{ borderRight: `1px solid ${C.border}`, minHeight: "calc(100vh - 40px)", padding: "20px 12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 8px", marginBottom: 26 }}>
            {guildInfo?.icon_url ? (
              <img
                src={guildInfo.icon_url} alt={guildInfo.name || "Server"}
                style={{ width: 34, height: 34, borderRadius: 8, flexShrink: 0, objectFit: "cover" }}
              />
            ) : (
              <div style={{ width: 34, height: 34, borderRadius: 8, background: `linear-gradient(135deg, ${C.gold}, ${C.cyan})`, display: "flex", alignItems: "center", justifyContent: "center", fontWeight: 800, fontFamily: "'Rajdhani', sans-serif", color: "#0A0E13", fontSize: 15, flexShrink: 0 }}>
                ⌁
              </div>
            )}
            <div>
              <div style={{ fontFamily: "'Rajdhani', sans-serif", fontWeight: 700, fontSize: 15, letterSpacing: 0.3 }}>DIENSTKONTROLLE</div>
              <div style={{ fontSize: 10, color: C.muted, fontFamily: "'JetBrains Mono', monospace" }}>Server Control Panel</div>
            </div>
          </div>

          <div className="app-sidebar-nav">
          {NAV.map((n) => {
            const Icon = n.icon;
            const isActive = active === n.key;
            return (
              <button
                key={n.key}
                onClick={() => setActive(n.key)}
                style={{
                  display: "flex", alignItems: "center", gap: 10, width: "100%",
                  padding: "9px 10px", marginBottom: 2, borderRadius: 7, border: "none",
                  background: isActive ? `${C.gold}14` : "transparent",
                  color: isActive ? C.gold : C.muted, cursor: "pointer",
                  fontSize: 13.5, fontFamily: "'Inter', sans-serif", fontWeight: isActive ? 600 : 500,
                  borderLeft: isActive ? `2px solid ${C.gold}` : "2px solid transparent",
                  textAlign: "left",
                }}
              >
                <Icon size={16} />
                {n.label}
                {isActive && <ChevronRight size={13} style={{ marginLeft: "auto" }} />}
              </button>
            );
          })}
          </div>
        </div>

        {/* Main */}
        <div className="app-main">
          {needsGuildSelection ? <GuildSelector guilds={myGuilds} /> : <Active />}
        </div>
      </div>
    </div>
    </LiveContext.Provider>
  );
}
