"""
Admin dashboard for Crypto-AlrtBot.

Read-only view over the backend: orchestrator/scheduler state, generated
signals, and analytics. Standard library only (no extra dependencies).

Endpoints:
    GET /                 HTML admin dashboard (auto-refreshes)
    GET /api/status       service + scheduler/worker state
    GET /api/signals      recent signals, newest first (?limit=N, default 50)
    GET /api/analytics    totals, per-symbol/side/strategy/day breakdowns
    GET /api/outcomes     outcome-ledger stats + recent records
    GET /api/paper        paper-portfolio summary
    GET /api/invo         bridge status (no secrets)
    GET /api/version      deployed commit SHA reporter (informational only;
                          "unknown" unless the platform injects it)
    GET /healthz          liveness probe (no auth)

Auth: if ADMIN_TOKEN env var is set, all endpoints except /healthz
require ?token=<ADMIN_TOKEN>. Never exposes env secrets or raw .env.
"""
import glob
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from src.utils.logger import get_logger

logger = get_logger(__name__)

STARTED_AT = time.time()
DATA_DIR = os.getenv("DATA_DIR", "data_store")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _json_safe(obj):
    """Recursively replace non-finite floats (inf/-inf/nan) with None so
    API bodies are always standards-compliant JSON. Analytics semantics
    are untouched — this only affects serialization at the boundary."""
    if isinstance(obj, float):
        try:
            import math as _math
            if not _math.isfinite(obj):
                return None
        except (TypeError, ValueError):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _signal_sort_key(s):
    """Order key for the merged history: unparseable timestamps sort
    first as (0, 0.0) in stable original order — never dropped, nothing
    invented — then parseable signals chronologically as (1, epoch_s).
    Tolerance mirrors the tracker's (epoch ms/s numbers, ISO strings;
    bools rejected). get_status() therefore always sees the genuinely
    latest parseable signal at signals[-1]."""
    ts = s.get("timestamp")
    if isinstance(ts, bool):
        return (0, 0.0)
    if isinstance(ts, (int, float)):
        try:
            return (1, float(ts) / 1000.0 if ts > 100_000_000_000 else float(ts))
        except (TypeError, ValueError):
            return (0, 0.0)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (1, dt.timestamp())
        except ValueError:
            return (0, 0.0)
    return (0, 0.0)


def load_all_signals(include_archive: bool = False):
    """Every persisted signal across daily files, oldest first.

    Active files only by default. Pass include_archive=True for full
    history views (/api/signals, /api/analytics, status totals):
    archive records already present under an active file are skipped so
    an interrupted move can never double-count, and the merged list is
    sorted globally chronological (stable) so newest-first slices and
    last-signal selection stay correct after archival. Archived records
    are read-only history — never mutated here."""
    signals = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "signals_*.json"))):
        for s in _read_json(path, []):
            if isinstance(s, dict) and s.get("signal_id"):
                signals.append(s)
    if include_archive:
        seen = {s["signal_id"] for s in signals}
        for path in sorted(glob.glob(os.path.join(DATA_DIR, "archive", "signals_*.json"))):
            for s in _read_json(path, []):
                if isinstance(s, dict) and s.get("signal_id") and s["signal_id"] not in seen:
                    seen.add(s["signal_id"])
                    signals.append(s)
        signals.sort(key=_signal_sort_key)
    return signals


def load_scheduler_state():
    state = _read_json(os.path.join(DATA_DIR, "scheduler_state.json"), {"published_signals": {}})
    if not isinstance(state, dict):
        return {"published_signals": {}}
    state.setdefault("published_signals", {})
    return state


def compute_analytics(signals):
    by_symbol, by_side, by_strategy, by_day = {}, {}, {}, {}
    conf_sum = 0
    for s in signals:
        by_symbol[s.get("symbol", "?")] = by_symbol.get(s.get("symbol", "?"), 0) + 1
        by_side[s.get("side", "?")] = by_side.get(s.get("side", "?"), 0) + 1
        by_strategy[s.get("strategy_id", "?")] = by_strategy.get(s.get("strategy_id", "?"), 0) + 1
        day = (s.get("timestamp") or "")[:10] or "unknown"
        by_day[day] = by_day.get(day, 0) + 1
        try:
            conf_sum += float(s.get("confidence", 0))
        except (TypeError, ValueError):
            pass
    n = len(signals)
    days = len([d for d in by_day if d != "unknown"]) or 1
    return {
        "total_signals": n,
        "signals_per_day": round(n / days, 2),
        "active_days": len(by_day),
        "avg_confidence": round(conf_sum / n, 1) if n else 0,
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda x: -x[1])),
        "by_side": by_side,
        "by_strategy": dict(sorted(by_strategy.items(), key=lambda x: -x[1])),
        "by_day": dict(sorted(by_day.items())),
    }


def load_outcomes():
    ledger = _read_json(os.path.join(DATA_DIR, "outcomes.json"), {})
    return ledger if isinstance(ledger, dict) else {}


def load_invo_status():
    """Invo bridge status: session age + activity tail. Never exposes tokens."""
    import time as _time
    sess = _read_json(os.path.join(DATA_DIR, "invo_session.json"), {})
    upd = sess.get("updated_at", 0) or 0
    try:
        age_h = round((_time.time() - float(upd)) / 3600.0, 1) if upd else None
    except (TypeError, ValueError):
        age_h = None
    try:
        from src.invo.session import read_activity
        activity = read_activity(DATA_DIR, limit=30)
    except Exception:
        activity = []
    shares = _read_json(os.path.join(DATA_DIR, "invo_shares.json"), {})
    return {
        "has_session": bool(sess.get("access_token")),
        "session_age_hours": age_h,
        "last_otp_at": sess.get("last_otp_at"),
        "shared_setups": len(shares) if isinstance(shares, dict) else 0,
        "activity": activity,
    }


def compute_outcome_stats(ledger):
    recs = list(ledger.values())
    final = [r for r in recs if r.get("final")]
    entered = [r for r in final if r.get("entry_triggered")]
    entered_total = [r for r in recs if r.get("entry_triggered")]
    wins = [r for r in entered if (r.get("net_realized_r") or 0) > 0]
    total_r = round(sum(r.get("net_realized_r") or 0 for r in entered), 3)
    gross_win = sum(r.get("net_realized_r") or 0 for r in entered if (r.get("net_realized_r") or 0) > 0)
    gross_loss = sum(-(r.get("net_realized_r") or 0) for r in entered if (r.get("net_realized_r") or 0) < 0)
    equity, cum = [], 0.0
    for r in sorted(entered, key=lambda x: str(x.get("signal_timestamp") or "")):
        cum = round(cum + (r.get("net_realized_r") or 0), 3)
        equity.append({"t": r.get("signal_timestamp"), "cum_r": cum, "id": r.get("signal_id")})
    return {
        "tracked": len(recs),
        "final": len(final),
        "open": len(recs) - len(final),
        "entered": len(entered),
        "entered_total": len(entered_total),
        "wins": len(wins),
        "win_rate": round(len(wins) / len(entered), 3) if entered else 0,
        "total_net_r": total_r,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else (None if not gross_win else float("inf")),
        "equity_curve": equity,
    }


def load_scan_history(limit=10):
    history = _read_json(os.path.join(DATA_DIR, "scan_history.json"), [])
    if not isinstance(history, list):
        return []
    return history[-limit:]


def _parse_ts(ts):
    """UTC timestamp string -> epoch seconds, or None when unreadable."""
    try:
        from datetime import timezone as _tz
        return datetime.fromisoformat(
            str(ts).replace("Z", "+00:00")).replace(
            tzinfo=_tz.utc).timestamp()
    except (TypeError, ValueError):
        return None


def compute_scan_health(scans, cadence_s, now_ts):
    """Pure scheduler-health verdict from scan heartbeats.

    Layers: process liveness is NOT judged here (HTTP reachability proves
    that); zero-candidate ok scans stay HEALTHY (idle market != failure).
    Returns {state, detail, last_scan_age_s}. States: HEALTHY, STALE,
    ERROR, UNKNOWN, ATTENTION. Never raises on malformed input.
    """
    try:
        cadence = float(cadence_s)
    except (TypeError, ValueError):
        cadence = 900.0
    if not cadence or cadence <= 0:
        cadence = 900.0
    if not scans:
        return {"state": "UNKNOWN",
                "detail": "no scan history — cannot assess scheduler",
                "last_scan_age_s": None}
    last = scans[-1] if isinstance(scans[-1], dict) else {}
    ts = _parse_ts(last.get("timestamp"))
    if ts is None:
        return {"state": "UNKNOWN",
                "detail": "scan timestamp unreadable — treated as unknown",
                "last_scan_age_s": None}
    age = now_ts - ts
    if age < -300:
        return {"state": "UNKNOWN",
                "detail": "scan clock ahead of server — possible clock skew",
                "last_scan_age_s": round(age, 1)}
    status = last.get("status")
    if status == "ok":
        if age <= 2 * cadence:
            return {"state": "HEALTHY",
                    "detail": "last scan ok",
                    "last_scan_age_s": round(age, 1)}
        return {"state": "STALE",
                "detail": "no scan for a while — scheduler may be down; "
                           "web server OK",
                "last_scan_age_s": round(age, 1)}
    if status == "failed":
        if age <= 2 * cadence:
            return {"state": "ERROR",
                    "detail": "last scan failed after retries — scheduler "
                               "alive, scans erroring",
                    "last_scan_age_s": round(age, 1)}
        return {"state": "STALE",
                "detail": "no successful scan for a while — scheduler may "
                           "be down; last recorded scan failed",
                "last_scan_age_s": round(age, 1)}
    return {"state": "ATTENTION",
            "detail": f"unrecognized scan status {status!r} — needs review",
            "last_scan_age_s": round(age, 1)}


def _scan_cadence_s():
    """Configured scan cadence in seconds (900 default; never raises)."""
    try:
        cadence = int(float(os.getenv("SCAN_INTERVAL_SECONDS", "900")))
    except (TypeError, ValueError):
        return 900
    return cadence if cadence > 0 else 900


def get_status():
    state = load_scheduler_state()
    signals = load_all_signals(include_archive=True)
    scans = load_scan_history(10)
    last = signals[-1] if signals else None
    now = datetime.now(timezone.utc)
    last_ts = None
    age_min = None
    if last and last.get("timestamp"):
        try:
            last_ts = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
            age_min = round((now - last_ts).total_seconds() / 60.0, 1)
        except ValueError:
            pass
    try:
        from src.config import CONFIG
        monitored = CONFIG.get_tradable_symbols()
    except Exception:
        monitored = []
    return {
        "service": "crypto-alrtbot",
        "dashboard_uptime_seconds": int(time.time() - STARTED_AT),
        "now_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_scan_cadence_s": _scan_cadence_s(),
        "monitored_symbols": monitored,
        "scheduler_cooldowns": state.get("published_signals", {}),
        "scheduler_cooldown_hours": 6.0,
        "scan_health": compute_scan_health(
            scans, _scan_cadence_s(), now.timestamp()),
        "total_signals_persisted": len(signals),
        "recent_scans": scans,
        "last_scan": scans[-1] if scans else None,
        "last_signal": (
            {
                "signal_id": last.get("signal_id"),
                "symbol": last.get("symbol"),
                "side": last.get("side"),
                "timestamp": last.get("timestamp"),
                "confidence": last.get("confidence"),
                "age_minutes": age_min,
            }
            if last
            else None
        ),
    }


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Crypto-AlrtBot — Admin</title>
<style>
:root{--bg:#0b0e14;--card:#151a24;--line:#232b3b;--txt:#e6e9f0;--dim:#8b93a7;--green:#3ddc84;--red:#ff5c5c;--amber:#ffb454;--blue:#4da3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{padding:20px;max-width:1100px;margin:auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--dim);margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.card .v{font-size:24px;font-weight:700}.card .k{color:var(--dim);font-size:12px}
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:10px;border:1px solid var(--line)}
table{width:100%;min-width:640px;border-collapse:collapse;background:var(--card)}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px;white-space:nowrap}th{color:var(--dim);font-weight:600;background:#111623}td:first-child{white-space:normal}
.long{color:var(--green);font-weight:700}.short{color:var(--red);font-weight:700}
.bar-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:13px}.bar-label{width:170px;flex:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--dim)}.bar{height:10px;background:var(--blue);border-radius:5px;min-width:2px}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;background:#1e2636;border:1px solid var(--line)}
.tab:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
.skel{color:var(--dim);animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:.45}50%{opacity:1}}
.ok{color:var(--green)}.warn{color:var(--amber)}
.refresh{color:var(--dim);font-size:12px}
nav{position:sticky;top:0;z-index:10;background:rgba(11,14,20,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(6px)}
.nav-inner{max-width:1100px;margin:auto;padding:10px 20px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.brand{font-weight:800;margin-right:10px;white-space:nowrap}
.tab{background:transparent;border:1px solid transparent;color:var(--dim);padding:7px 13px;border-radius:8px;font-size:13px;cursor:pointer}
.tab:hover{color:var(--txt)}.tab.active{color:var(--txt);background:#1b2334;border-color:var(--line)}
.page{display:none}.page.active{display:block}
.mt{margin-top:12px}
@media (max-width:640px){
  .wrap{padding:12px}
  h1{font-size:17px}
  .grid{grid-template-columns:repeat(2,1fr);gap:8px}
  .card{padding:10px}.card .v{font-size:19px}
  .tab{padding:6px 9px;font-size:12px}
  .bar-label{width:110px}
  th,td{padding:7px 8px;font-size:12px}
}
</style>
</head>
<body>
<nav><div class="nav-inner">
<span class="brand">🤖 AlrtBot Admin</span>
<button class="tab active" data-page="overview">Overview</button>
<button class="tab" data-page="signals">Signals</button>
<button class="tab" data-page="results">Results</button>
<button class="tab" data-page="paper">Paper</button>
<button class="tab" data-page="invo">Invo</button>
<button class="tab" data-page="workers">Workers</button>
<span id="health" class="pill" style="margin-left:auto" title="Scheduler/scan health (web-server reachability is separate)">…</span>
<span id="upd" class="refresh"></span>
</div></nav>
<div class="wrap">
<div class="sub">Backend overview: scheduler workers, signals & analytics · auto-refreshes every 60s</div>

<section class="page active" id="page-overview">
<h2>Workers / Status <span class="refresh" id="asof-overview"></span></h2>
<div class="grid" id="status-cards"></div>
<h2>Signals per Symbol</h2>
<div class="card"><div id="bars-symbol"></div></div>
<h2>Signals per Day</h2>
<div class="card"><div id="bars-day"></div></div>
</section>

<section class="page" id="page-signals">
<h2>Recent Signals <span class="refresh" id="asof-signals"></span></h2>
<div class="table-scroll"><table><thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Side</th><th>Entry</th><th>SL</th><th>TP</th><th>Conf</th><th>Strategy</th><th>Regime</th></tr></thead><tbody id="rows"></tbody></table></div>
</section>

<section class="page" id="page-results">
<h2>Live Results (outcome ledger) <span class="refresh" id="asof-results"></span></h2>
<div class="grid" id="outcome-cards"></div>
<div class="card mt"><div id="equity"></div></div>
<div class="table-scroll mt"><table><thead><tr><th>Signal</th><th>Symbol</th><th>Side</th><th>Entry Hit</th><th>First Event</th><th>Net R</th><th>Status</th><th>Liq Price</th><th>Hold Time</th><th>Final</th></tr></thead><tbody id="orows"></tbody></table></div>
</section>

<section class="page" id="page-paper">
<h2>Paper Trading (virtual $1,000 — live test, no real money) <span class="refresh" id="asof-paper"></span></h2>
<div class="grid" id="paper-cards"></div>
<div class="card mt"><div id="paper-equity"></div></div>
<div class="table-scroll mt"><table><thead><tr><th>Settled</th><th>Symbol</th><th>Side</th><th>Event</th><th>Net R</th><th>P&amp;L $</th><th>Hold Time</th><th>Equity $</th></tr></thead><tbody id="prows"></tbody></table></div>
</section>

<section class="page" id="page-invo">
<h2>Invo Auto-Share (paper trades, no real money) <span class="refresh" id="asof-invo"></span></h2>
<div class="grid" id="invo-cards"></div>
<h2>Bridge Activity Log</h2>
<div class="table-scroll"><table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Detail</th></tr></thead><tbody id="invo-rows"></tbody></table></div>
</section>

<section class="page" id="page-workers">
<h2>Worker Scans (proof every cycle ran) <span class="refresh" id="asof-workers"></span></h2>
<div class="table-scroll"><table><thead><tr><th>Time (UTC)</th><th>Type</th><th>Status</th><th>Candidates</th><th>Published</th></tr></thead><tbody id="scans"></tbody></table></div>
<h2>Cooldowns (dedup ledger)</h2>
<div class="card"><div id="cooldowns"></div></div>
</section>
<div class="sub mt">Metric definitions — win rate counts entered closed trades only · total net R sums entered finals (unit R) · paper P&amp;L is settled trades (unit $) · signals/day divides by days with ≥1 signal · hover any card for its definition. Health pill: scheduler/scan state only; web-server reachability is separate. Version: informational; “unknown” unless the platform injects it.</div>
</div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
async function get(p){ const sep = p.includes('?') ? '&' : '?'; const url = TOKEN ? (p + sep + 'token=' + encodeURIComponent(TOKEN)) : p; const r = await fetch(url); if(!r.ok) throw new Error(r.status); return r.json(); }
function bars(el, obj){ const e=document.getElementById(el); const ks=Object.keys(obj); if(!ks.length){e.innerHTML='<span class="warn">no data</span>';return;} const mx=Math.max(...ks.map(k=>obj[k])); e.innerHTML=ks.map(k=>'<div class="bar-row"><div class="bar-label">'+k+'</div><div class="bar" style="width:'+Math.max(2,Math.round(obj[k]/mx*280))+'px"></div><div>'+obj[k]+'</div></div>').join(''); }
var LAST=[null,null,null,null,null,null];
var LASTT=[null,null,null,null,null,null];
var LASTE=[null,null,null,null,null,null];
var FIRSTLOAD=true;
function checkShape(i,d){
  if(d==null||typeof d!=='object') return i===1 ? Array.isArray(d) : false;
  if(i===1) return Array.isArray(d);
  if(i===2) return !!d.by_symbol;
  if(i===3) return !!d.stats;
  if(i===4) return 'equity' in d;
  if(i===5) return 'activity' in d;
  return true;
}
async function fetchOne(i,url){
  try{
    var d = await get(url);
    if(!checkShape(i,d)) throw new Error('unexpected response shape');
    LAST[i]=d; LASTT[i]=new Date(); LASTE[i]=null;
  }catch(e){ LASTE[i]=String(e.message||e); }
}
function staleNote(i){ return LASTE[i] ? ' · refresh failed ('+LASTE[i]+'), showing '+(LASTT[i]?('data from '+LASTT[i].toLocaleTimeString()):'no data yet') : ''; }
function allAuth(){ return LASTE.every(function(e){return e==='401';}); }
function skeletonize(){ ['status-cards','rows','outcome-cards','paper-cards','invo-cards','scans'].forEach(function(id){ var e=document.getElementById(id); if(e) e.innerHTML='<div class="card skel">loading…</div>'; }); }
async function load(){
  try{
    if(FIRSTLOAD){ skeletonize(); FIRSTLOAD=false; }
    await Promise.all([fetchOne(0,'/api/status'), fetchOne(1,'/api/signals?limit=50'), fetchOne(2,'/api/analytics'), fetchOne(3,'/api/outcomes'), fetchOne(4,'/api/paper'), fetchOne(5,'/api/invo')]);
    var st=LAST[0], sig=LAST[1], an=LAST[2], oc=LAST[3], pp=LAST[4], iv=LAST[5];
    var fails=LASTE.filter(function(e){return e;}).length;
    if(!st&&!sig&&!an&&!oc&&!pp&&!iv){
      document.getElementById('upd').textContent = allAuth() ? '· missing or invalid access — reload with authorized access' : '· error loading (service unreachable)';
      return;
    }
    document.getElementById('upd').textContent = '· updated ' + new Date().toLocaleTimeString() + (fails?(' · '+fails+' section'+(fails>1?'s':'')+' stale'):'');
    if(st){
    var h = st.scan_health || {state:'UNKNOWN', detail:'no health data'};
    var hdot = h.state==='HEALTHY' ? 'var(--green)' : (h.state==='ERROR' ? 'var(--red)' : (h.state==='UNKNOWN' ? 'var(--dim)' : 'var(--amber)'));
    var hp = document.getElementById('health');
    hp.innerHTML = '<span style="color:'+hdot+'">●</span> ' + h.state;
    hp.title = h.detail + ' (web-server reachability is separate)';
    asof('asof-overview', (st.now_utc ? '· data as of ' + st.now_utc : '') + staleNote(0));
    asof('asof-workers', (st.last_scan && st.last_scan.timestamp ? '· last scan ' + st.last_scan.timestamp + ' (' + (st.last_scan.status||'') + ')' : '') + staleNote(0));
    }
    if(sig){ asof('asof-signals', (sig.length && sig[0].timestamp ? '· newest ' + sig[0].timestamp : '') + staleNote(1)); }
    if(oc){ asof('asof-results', ((oc.records||[]).length ? '· newest ' + (((oc.records||[])[0].signal_timestamp)||'') : '') + staleNote(3)); }
    if(pp){ asof('asof-paper', ((pp.recent_trades||[]).length ? '· newest ' + ((((pp.recent_trades||[])[0].settled_at)||'').slice(0,16)) : '') + staleNote(4)); }
    if(iv){ asof('asof-invo', (function(){ var m=0; (iv.activity||[]).forEach(function(a){ if(a.ts&&a.ts>m) m=a.ts; }); return m ? '· newest ' + new Date(m*1000).toISOString().slice(0,16).replace('T',' ') + ' UTC' : ''; })() + staleNote(5)); }
    if(st&&an){
    const last = st.last_signal;
    document.getElementById('status-cards').innerHTML =
      card(st.total_signals_persisted, 'signals persisted', 'Total signal records in signals data files') +
      card(an.signals_per_day, 'signals / day', 'Total signals ÷ days with ≥1 signal. Sparse histories inflate this rate.') +
      card(an.avg_confidence, 'avg confidence', 'Mean confidence across all persisted signals') +
      card((an.by_side.LONG||0) + ' / ' + (an.by_side.SHORT||0), 'LONG / SHORT', 'Count split across all persisted signals') +
      card(last ? (last.symbol + ' ' + last.side) : '—', 'last signal' + (last && last.age_minutes!=null ? ' ('+last.age_minutes+'m ago)' : ''), 'Newest persisted signal and its age') +
      card(st.monitored_symbols.length + ' coins', 'monitored universe', 'Tradable symbols in config (not live prices)');
    bars('bars-symbol', an.by_symbol); bars('bars-day', an.by_day);
    }
    if(sig){
    document.getElementById('rows').innerHTML = sig.map(s=>'<tr><td>'+(s.timestamp||'')+'</td><td><b>'+s.symbol+'</b></td><td class="'+(s.side==='LONG'?'long':'short')+'">'+s.side+'</td><td>'+s.entry+'</td><td>'+s.stop_loss+'</td><td>'+(s.tp ?? s.tp1)+'</td><td>'+s.confidence+'</td><td>'+(s.strategy_id||'')+'</td><td>'+(s.btc_regime||'')+'</td></tr>').join('') || '<tr><td colspan="9">no signals yet</td></tr>';
    }
    if(st){
    const scans = (st.recent_scans || []).slice().reverse();
    document.getElementById('scans').innerHTML = scans.map(s=>'<tr><td>'+s.timestamp+'</td><td>'+s.scan_type+'</td><td class="'+(s.status==='ok'?'ok':'warn')+'">'+s.status+'</td><td>'+s.candidates+'</td><td>'+s.published+'</td></tr>').join('') || '<tr><td colspan="5">no scans recorded yet</td></tr>';
    }
    if(oc){
    const s = oc.stats;
    document.getElementById('outcome-cards').innerHTML =
      card(s.tracked, 'signals tracked', 'Outcome records tracked (final + open)') +
      card(s.final + ' / ' + s.open, 'final / open', 'Final (resolved/expired) vs still-open outcomes') +
      card(s.entered ? Math.round(s.win_rate*100)+'%' : '—', 'win rate (entered)', 'Wins ÷ entered closed trades (n=' + s.entered + '). Expired-unentered excluded.') +
      card((s.total_net_r>0?'+':'') + s.total_net_r + 'R', 'total net R', 'Sum of net R over entered closed trades (unit R; open excluded)') +
      card(s.profit_factor==null ? '—' : s.profit_factor, 'profit factor', 'Gross win ÷ gross loss. ‘—’ = no closed entered trades.') +
      card(s.entered_total + ' (' + s.entered + ' closed)', 'entered trades', 'Entered trades: total (closed finals)');
    const eq = s.equity_curve || [];
    document.getElementById('equity').innerHTML = eq.length ? ('<div class="bar-label">Cumulative net R — entered finals: <b class="'+(eq[eq.length-1].cum_r>=0?'ok':'warn')+'">'+eq[eq.length-1].cum_r+'R</b> over '+eq.length+' closed trades</div>' + eq.slice(-40).map(p=>'<div class="bar-row"><div class="bar-label">'+(p.t||'').slice(0,16)+'</div><div class="bar" style="width:'+Math.min(280,Math.abs(p.cum_r)*8+2)+'px;'+(p.cum_r<0?'background:var(--red)':'')+'"></div><div>'+p.cum_r+'R</div></div>').join('')) : '<span class="warn">no closed trades yet — ledger fills as signals resolve</span>';
    document.getElementById('orows').innerHTML = (oc.records||[]).map(r=>'<tr><td>'+(r.signal_id||'')+'</td><td><b>'+r.symbol+'</b></td><td class="'+(r.side==='LONG'?'long':'short')+'">'+r.side+'</td><td>'+(r.entry_triggered?'yes':'no')+'</td><td>'+(r.first_event||'—')+'</td><td>'+r.net_realized_r+'R</td><td class="'+(r.status==='LIQUIDATED'?'warn':'')+'">'+r.status+'</td><td>'+(r.liq_price==null?'—':r.liq_price)+'</td><td>'+(r.holding_time||'—')+'</td><td>'+(r.final?'yes':'open')+'</td></tr>').join('') || '<tr><td colspan="10">no tracked outcomes yet</td></tr>';
    }
    if(pp){
    document.getElementById('paper-cards').innerHTML =
      card('$' + pp.equity, 'paper equity', 'Virtual-bank equity now (unit $)') +
      card((pp.total_pnl>=0?'+':'') + '$' + pp.total_pnl + ' (' + pp.return_pct + '%)', 'paper P&L', 'Equity minus $1,000 start (unit $, %)') +
      card(pp.trades_settled + ' (' + pp.wins + 'W' + (pp.liquidations? ', ' + pp.liquidations + ' LIQ' : '') + ')', 'settled (wins)', 'Settled trades (wins W, liquidations LIQ)') +
      card(pp.trades_entered ? Math.round(pp.win_rate*100)+'%' : '—', 'paper win rate', 'Wins ÷ settled trades') +
      card('$' + pp.max_drawdown_usd, 'max DD', 'Max drawdown from peak equity (unit $)') +
      card('$' + pp.starting_equity + ' bank', 'virtual bank', 'Starting virtual bank (unit $)');
    const pe = pp.equity_curve || [];
    document.getElementById('paper-equity').innerHTML = pe.length ? ('<div class="bar-label">Cumulative paper P&amp;L $ — settled trades: <b class="'+(pe[pe.length-1].cum_pnl>=0?'ok':'warn')+'">'+(pe[pe.length-1].cum_pnl>=0?'+':'')+'$'+pe[pe.length-1].cum_pnl+'</b> over '+pe.length+' settled trades</div>' + pe.slice(-40).map(p=>'<div class="bar-row"><div class="bar-label">'+(p.t||'').slice(0,16)+'</div><div class="bar" style="width:'+Math.min(280,Math.abs(p.cum_pnl)*4+2)+'px;'+(p.cum_pnl<0?'background:var(--red)':'')+'"></div><div>$'+p.cum_pnl+'</div></div>').join('')) : '<span class="warn">no settled paper trades yet — positions settle when outcomes finalize</span>';
    document.getElementById('prows').innerHTML = (pp.recent_trades||[]).map(t=>'<tr><td>'+(t.settled_at||'').slice(0,16)+'</td><td><b>'+t.symbol+'</b></td><td class="'+(t.side==='LONG'?'long':'short')+'">'+t.side+'</td><td>'+(t.first_event||'—')+'</td><td>'+t.net_r+'R</td><td>'+t.pnl_usd+'</td><td>'+(t.holding_time||'—')+'</td><td>'+t.equity_after+'</td></tr>').join('') || '<tr><td colspan="8">no settled paper trades yet</td></tr>';
    }
    if(iv){
    document.getElementById('invo-cards').innerHTML =
      card(iv.has_session ? 'active' : 'no session', 'bridge session', 'Active = login token on disk') +
      card(iv.session_age_hours==null ? '—' : iv.session_age_hours + 'h', 'session age', 'Hours since session refresh') +
      card(iv.shared_setups, 'setups shared', 'Paper setups shared to Invo portfolio') +
      card(iv.last_otp_at ? new Date(iv.last_otp_at*1000).toLocaleString() : 'never', 'last OTP login', 'Last email-OTP login event');
    document.getElementById('invo-rows').innerHTML = (iv.activity||[]).slice().reverse().map(a=>{
      const d = a.detail||{};
      const shortId = d.baseId ? '…'+String(d.baseId).slice(-6) : '';
      const det = [d.symbol, d.side, d.reason, d.email, shortId,
        d.priceTarget!=null ? 'TP '+d.priceTarget : '',
        d.stopLoss!=null ? 'SL '+d.stopLoss : '',
        d.closingPrice!=null ? 'close@'+d.closingPrice : '',
        d.remainingSim!=null ? 'sim '+Math.round(d.remainingSim*100)/100 : '',
        Array.isArray(d.baseIds)? d.baseIds.length + ' baseIds' : '',
        d.response ? String(d.response).slice(0,90) : '',
        d.error ? String(d.error).slice(0,90) : ''].filter(Boolean).join(' · ');
      const t = a.ts ? new Date(a.ts*1000).toLocaleString() : '';
      return '<tr><td>'+t+'</td><td>'+a.event+'</td><td>'+det+'</td></tr>';
    }).join('') || '<tr><td colspan="3">no bridge activity yet (bridge is disabled until you enable it)</td></tr>';
    }
    if(st){
    const cd = st.scheduler_cooldowns || {}; const keys = Object.keys(cd);
    document.getElementById('cooldowns').innerHTML = keys.length ? keys.map(k=>'<span class="pill">'+k+' → '+cd[k]+'</span> ').join('') : '<span class="warn">ledger empty — nothing suppressed</span>';
    }
  }catch(e){ document.getElementById('upd').textContent = '· error loading ('+e.message+(TOKEN?'':', hint: ?token=ADMIN_TOKEN')+')'; }
}
function card(v,k,t){ return '<div class="card"'+(t?' title="'+t.replace(/"/g,'&quot;')+'"':'')+'><div class="v">'+v+'</div><div class="k">'+k+'</div></div>'; }
function asof(id,t){ var e=document.getElementById(id); if(e) e.textContent = t||''; }
function showPage(name){
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active', b.dataset.page===name));
  document.querySelectorAll('.page').forEach(s=>s.classList.toggle('active', s.id==='page-'+name));
  try{ history.replaceState(null,'',location.pathname+location.search+'#'+name); }catch(e){}
}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click', ()=>showPage(b.dataset.page)));
(function(){ const h=(location.hash||'').replace('#',''); if(h && document.getElementById('page-'+h)) showPage(h); })();
load(); setInterval(load, 60000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AlrtBotAdmin/1.0"

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.address_string(), fmt % args)

    def _auth_ok(self, query):
        if not ADMIN_TOKEN:
            return True
        return query.get("token", [""])[0] == ADMIN_TOKEN

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        if path == "/healthz":
            self._send(200, json.dumps({"ok": True}))
            return
        if not self._auth_ok(query):
            self._send(401, json.dumps({"error": "unauthorized (bad/missing token)"}))
            return

        if path == "/":
            self._send(200, PAGE, "text/html")
        elif path == "/api/version":
            sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")
            self._send(200, json.dumps({
                "commit_sha": sha,
                "source": "railway-env" if sha else "unknown",
            }))
        elif path == "/api/status":
            self._send(200, json.dumps(get_status()))
        elif path == "/api/signals":
            try:
                limit = max(1, min(int(query.get("limit", ["50"])[0]), 500))
            except ValueError:
                limit = 50
            signals = load_all_signals(include_archive=True)
            self._send(200, json.dumps(list(reversed(signals[-limit:]))))
        elif path == "/api/analytics":
            self._send(200, json.dumps(compute_analytics(load_all_signals(include_archive=True))))
        elif path == "/api/invo":
            self._send(200, json.dumps(load_invo_status()))
        elif path == "/api/paper":
            try:
                from src.papertrade.portfolio import PaperPortfolio
                self._send(200, json.dumps(PaperPortfolio().summary()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        elif path == "/api/outcomes":
            ledger = load_outcomes()
            self._send(200, json.dumps(_json_safe({
                "stats": compute_outcome_stats(ledger),
                "records": sorted(ledger.values(), key=lambda r: str(r.get("signal_timestamp") or ""), reverse=True)[:200],
            })))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def run(host="0.0.0.0", port=None):
    port = int(port or os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Admin dashboard listening on {host}:{port} (auth={'token' if ADMIN_TOKEN else 'open'})")
    server.serve_forever()


if __name__ == "__main__":
    run()
