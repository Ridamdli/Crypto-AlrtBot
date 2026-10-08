"""
Admin dashboard for Crypto-AlrtBot.

Read-only view over the backend: orchestrator/scheduler state, generated
signals, and analytics. Standard library only (no extra dependencies).

Endpoints:
    GET /                 HTML admin dashboard (auto-refreshes)
    GET /api/status       service + scheduler/worker state
    GET /api/signals      recent signals, newest first (?limit=N, default 50)
    GET /api/analytics    totals, per-symbol/side/strategy/day breakdowns
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


def load_all_signals():
    """Load every persisted signal across daily files, oldest first."""
    signals = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "signals_*.json"))):
        for s in _read_json(path, []):
            if isinstance(s, dict) and s.get("signal_id"):
                signals.append(s)
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


def get_status():
    state = load_scheduler_state()
    signals = load_all_signals()
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
        "monitored_symbols": monitored,
        "scheduler_cooldowns": state.get("published_signals", {}),
        "scheduler_cooldown_hours": 6.0,
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
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:20px;max-width:1100px;margin:auto}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.sub{color:var(--dim);margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}.card .v{font-size:24px;font-weight:700}.card .k{color:var(--dim);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);font-size:13px}th{color:var(--dim);font-weight:600;background:#111623}
.long{color:var(--green);font-weight:700}.short{color:var(--red);font-weight:700}
.bar-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:13px}.bar-label{width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--dim)}.bar{height:10px;background:var(--blue);border-radius:5px;min-width:2px}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;background:#1e2636;border:1px solid var(--line)}
.ok{color:var(--green)}.warn{color:var(--amber)}
.refresh{color:var(--dim);font-size:12px}
</style>
</head>
<body>
<h1>🤖 Crypto-AlrtBot — Admin Dashboard</h1>
<div class="sub">Backend overview: scheduler workers, signals & analytics · auto-refreshes every 60s <span id="upd" class="refresh"></span></div>

<h2>Workers / Status</h2>
<div class="grid" id="status-cards"></div>

<h2>Signals per Symbol</h2>
<div class="card"><div id="bars-symbol"></div></div>

<h2>Signals per Day</h2>
<div class="card"><div id="bars-day"></div></div>

<h2>Live Results (outcome ledger)</h2>
<div class="grid" id="outcome-cards"></div>
<div class="card" style="margin-top:12px"><div id="equity"></div></div>
<table style="margin-top:12px"><thead><tr><th>Signal</th><th>Symbol</th><th>Side</th><th>Entry Hit</th><th>First Event</th><th>Net R</th><th>Status</th><th>Liq Price</th><th>Hold Time</th><th>Final</th></tr></thead><tbody id="orows"></tbody></table>

<h2>Paper Trading (virtual $1,000 — live test, no real money)</h2>
<div class="grid" id="paper-cards"></div>
<div class="card" style="margin-top:12px"><div id="paper-equity"></div></div>
<table style="margin-top:12px"><thead><tr><th>Settled</th><th>Symbol</th><th>Side</th><th>Event</th><th>Net R</th><th>P&amp;L $</th><th>Hold Time</th><th>Equity $</th></tr></thead><tbody id="prows"></tbody></table>

<h2>Recent Signals</h2>
<table><thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Side</th><th>Entry</th><th>SL</th><th>TP1</th><th>Conf</th><th>Strategy</th><th>Regime</th></tr></thead><tbody id="rows"></tbody></table>

<h2>Worker Scans (proof every cycle ran)</h2>
<table><thead><tr><th>Time (UTC)</th><th>Type</th><th>Status</th><th>Candidates</th><th>Published</th></tr></thead><tbody id="scans"></tbody></table>

<h2>Cooldowns (dedup ledger)</h2>
<div class="card"><div id="cooldowns"></div></div>

<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
async function get(p){ const sep = p.includes('?') ? '&' : '?'; const url = TOKEN ? (p + sep + 'token=' + encodeURIComponent(TOKEN)) : p; const r = await fetch(url); if(!r.ok) throw new Error(r.status); return r.json(); }
function bars(el, obj){ const e=document.getElementById(el); const ks=Object.keys(obj); if(!ks.length){e.innerHTML='<span class="warn">no data</span>';return;} const mx=Math.max(...ks.map(k=>obj[k])); e.innerHTML=ks.map(k=>'<div class="bar-row"><div class="bar-label">'+k+'</div><div class="bar" style="width:'+Math.max(2,Math.round(obj[k]/mx*280))+'px"></div><div>'+obj[k]+'</div></div>').join(''); }
async function load(){
  try{
    const [st, sig, an, oc, pp] = await Promise.all([get('/api/status'), get('/api/signals?limit=50'), get('/api/analytics'), get('/api/outcomes'), get('/api/paper')]);
    document.getElementById('upd').textContent = '· updated ' + new Date().toLocaleTimeString();
    const last = st.last_signal;
    document.getElementById('status-cards').innerHTML =
      card(st.total_signals_persisted, 'signals persisted') +
      card(an.signals_per_day, 'signals / day') +
      card(an.avg_confidence, 'avg confidence') +
      card((an.by_side.LONG||0) + ' / ' + (an.by_side.SHORT||0), 'LONG / SHORT') +
      card(last ? (last.symbol + ' ' + last.side) : '—', 'last signal' + (last && last.age_minutes!=null ? ' ('+last.age_minutes+'m ago)' : '')) +
      card(st.monitored_symbols.length + ' coins', 'monitored universe');
    bars('bars-symbol', an.by_symbol); bars('bars-day', an.by_day);
    document.getElementById('rows').innerHTML = sig.map(s=>'<tr><td>'+(s.timestamp||'')+'</td><td><b>'+s.symbol+'</b></td><td class="'+(s.side==='LONG'?'long':'short')+'">'+s.side+'</td><td>'+s.entry+'</td><td>'+s.stop_loss+'</td><td>'+s.tp1+'</td><td>'+s.confidence+'</td><td>'+(s.strategy_id||'')+'</td><td>'+(s.btc_regime||'')+'</td></tr>').join('') || '<tr><td colspan="9">no signals yet</td></tr>';
    const scans = (st.recent_scans || []).slice().reverse();
    document.getElementById('scans').innerHTML = scans.map(s=>'<tr><td>'+s.timestamp+'</td><td>'+s.scan_type+'</td><td class="'+(s.status==='ok'?'ok':'warn')+'">'+s.status+'</td><td>'+s.candidates+'</td><td>'+s.published+'</td></tr>').join('') || '<tr><td colspan="5">no scans recorded yet</td></tr>';
    const s = oc.stats;
    document.getElementById('outcome-cards').innerHTML =
      card(s.tracked, 'signals tracked') +
      card(s.final + ' / ' + s.open, 'final / open') +
      card(s.entered ? Math.round(s.win_rate*100)+'%' : '—', 'win rate (entered)') +
      card((s.total_net_r>0?'+':'') + s.total_net_r + 'R', 'total net R') +
      card(s.profit_factor==null ? '—' : s.profit_factor, 'profit factor') +
      card(s.entered_total + ' (' + s.entered + ' closed)', 'entered trades');
    const eq = s.equity_curve || [];
    document.getElementById('equity').innerHTML = eq.length ? ('<div class="bar-label">Equity (cum. net R): <b class="'+(eq[eq.length-1].cum_r>=0?'ok':'warn')+'">'+eq[eq.length-1].cum_r+'R</b> over '+eq.length+' closed trades</div>' + eq.slice(-40).map(p=>'<div class="bar-row"><div class="bar-label">'+(p.t||'').slice(0,16)+'</div><div class="bar" style="width:'+Math.min(280,Math.abs(p.cum_r)*8+2)+'px;'+(p.cum_r<0?'background:var(--red)':'')+'"></div><div>'+p.cum_r+'R</div></div>').join('')) : '<span class="warn">no closed trades yet — ledger fills as signals resolve</span>';
    document.getElementById('orows').innerHTML = (oc.records||[]).map(r=>'<tr><td>'+(r.signal_id||'')+'</td><td><b>'+r.symbol+'</b></td><td class="'+(r.side==='LONG'?'long':'short')+'">'+r.side+'</td><td>'+(r.entry_triggered?'yes':'no')+'</td><td>'+(r.first_event||'—')+'</td><td>'+r.net_realized_r+'R</td><td class="'+(r.status==='LIQUIDATED'?'warn':'')+'">'+r.status+'</td><td>'+(r.liq_price==null?'—':r.liq_price)+'</td><td>'+(r.holding_time||'—')+'</td><td>'+(r.final?'yes':'open')+'</td></tr>').join('') || '<tr><td colspan="10">no tracked outcomes yet</td></tr>';
    document.getElementById('paper-cards').innerHTML =
      card('$' + pp.equity, 'paper equity') +
      card((pp.total_pnl>=0?'+':'') + '$' + pp.total_pnl + ' (' + pp.return_pct + '%)', 'paper P&L') +
      card(pp.trades_settled + ' (' + pp.wins + 'W' + (pp.liquidations? ', ' + pp.liquidations + ' LIQ' : '') + ')', 'settled (wins)') +
      card(pp.trades_entered ? Math.round(pp.win_rate*100)+'%' : '—', 'paper win rate') +
      card('$' + pp.max_drawdown_usd, 'max DD') +
      card('$' + pp.starting_equity + ' bank', 'virtual bank');
    const pe = pp.equity_curve || [];
    document.getElementById('paper-equity').innerHTML = pe.length ? ('<div class="bar-label">Paper equity: <b class="'+(pe[pe.length-1].cum_pnl>=0?'ok':'warn')+'">'+(pe[pe.length-1].cum_pnl>=0?'+':'')+'$'+pe[pe.length-1].cum_pnl+'</b> over '+pe.length+' settled trades</div>' + pe.slice(-40).map(p=>'<div class="bar-row"><div class="bar-label">'+(p.t||'').slice(0,16)+'</div><div class="bar" style="width:'+Math.min(280,Math.abs(p.cum_pnl)*4+2)+'px;'+(p.cum_pnl<0?'background:var(--red)':'')+'"></div><div>$'+p.cum_pnl+'</div></div>').join('')) : '<span class="warn">no settled paper trades yet — positions settle when outcomes finalize</span>';
    document.getElementById('prows').innerHTML = (pp.recent_trades||[]).map(t=>'<tr><td>'+(t.settled_at||'').slice(0,16)+'</td><td><b>'+t.symbol+'</b></td><td class="'+(t.side==='LONG'?'long':'short')+'">'+t.side+'</td><td>'+(t.first_event||'—')+'</td><td>'+t.net_r+'R</td><td>'+t.pnl_usd+'</td><td>'+(t.holding_time||'—')+'</td><td>'+t.equity_after+'</td></tr>').join('') || '<tr><td colspan="8">no settled paper trades yet</td></tr>';
    const cd = st.scheduler_cooldowns || {}; const keys = Object.keys(cd);
    document.getElementById('cooldowns').innerHTML = keys.length ? keys.map(k=>'<span class="pill">'+k+' → '+cd[k]+'</span> ').join('') : '<span class="warn">ledger empty — nothing suppressed</span>';
  }catch(e){ document.getElementById('upd').textContent = '· error loading ('+e.message+(TOKEN?'':', hint: ?token=ADMIN_TOKEN')+')'; }
}
function card(v,k){ return '<div class="card"><div class="v">'+v+'</div><div class="k">'+k+'</div></div>'; }
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
        elif path == "/api/status":
            self._send(200, json.dumps(get_status()))
        elif path == "/api/signals":
            try:
                limit = max(1, min(int(query.get("limit", ["50"])[0]), 500))
            except ValueError:
                limit = 50
            signals = load_all_signals()
            self._send(200, json.dumps(list(reversed(signals[-limit:]))))
        elif path == "/api/analytics":
            self._send(200, json.dumps(compute_analytics(load_all_signals())))
        elif path == "/api/paper":
            try:
                from src.papertrade.portfolio import PaperPortfolio
                self._send(200, json.dumps(PaperPortfolio().summary()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        elif path == "/api/outcomes":
            ledger = load_outcomes()
            self._send(200, json.dumps({
                "stats": compute_outcome_stats(ledger),
                "records": sorted(ledger.values(), key=lambda r: str(r.get("signal_timestamp") or ""), reverse=True)[:200],
            }))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def run(host="0.0.0.0", port=None):
    port = int(port or os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Admin dashboard listening on {host}:{port} (auth={'token' if ADMIN_TOKEN else 'open'})")
    server.serve_forever()


if __name__ == "__main__":
    run()
