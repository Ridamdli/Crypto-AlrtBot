# Deploying Crypto-AlrtBot on a container PaaS (Koyeb / Render / Fly.io)

Railway is abandoned. This document deploys the **same image** twice from this
repo: one **main** service (dashboard + scheduler) and one **mirror** worker.

Nothing here has been executed. Local `docker build` was stopped on request
(bandwidth), so the image has **never been built or run**. The cloud build is the
first real test of the `Dockerfile` — treat the first deploy as a verification
step, not a formality.

---

## 0. What is already true (verified, not assumed)

| Fact | Evidence |
|---|---|
| `DATABASE_URL` is read from the process env, not only `.env` | `src/db/engine.py:41` `os.getenv("DATABASE_URL")` |
| `.env` is never in the image | `.dockerignore` excludes `.env` / `.env.*` |
| Every dependency has a `cp312` manylinux wheel — **no compiler, no `libpq-dev`** | build log: `pandas`, `psycopg_binary`, `ckzg`, `pycryptodome`, `cytoolz`, `regex` all resolved to `cp312-cp312-manylinux*` |
| Deps are pinned to the tested versions | `requirements.txt` |
| Health probe needs no auth and no DB | `src/dashboard.py:598` → `GET /healthz` → `200 {"ok":true}` |
| Dashboard binds `0.0.0.0` and honours `$PORT` | `src/dashboard.py:642-644` |
| Neon `production` schema is current | `alembic check` clean, `production` branch |

**Neon:** project `withered-bird-68619336`, region `aws-us-east-2`, branch
`production` (`br-winter-base-b4x95eis`), database `neondb`.

---

## 1. Prerequisites

1. The repo is on GitHub (the platform builds from the repo, not from your
   machine — this is why nothing needs to be built locally).
2. Two connection strings from the Neon console, branch **`production`**:
   - **pooled** host (contains `-pooler`) → use this for the running services.
     `src/db/engine.py:62` detects `-pooler` and disables prepared statements,
     which is required behind PgBouncer.
   - **direct** host → use this only for migrations.
3. A long random `ADMIN_TOKEN` (`python -c "import secrets;print(secrets.token_urlsafe(32))"`).

> Never paste either connection string into a shell history, a commit, or a chat
> message. Set them in the platform's secret UI only.

---

## 2. The one blocker to resolve before deploying the main service

**The scheduler needs an always-on process.** `src/service.py` runs a 15-minute
loop; if the instance is suspended, no scan happens, no alert is sent, and
`exit_decisions` / `outcomes` stop advancing.

Koyeb and Render free instances can **scale to zero** after inactivity. Before
deploying:

- In the service settings, find **Scaling / Scale to zero** and set it to **off**.
- If your plan does not permit disabling it, the free tier **cannot** host the
  main bot. Use the cheapest always-on instance instead. A keep-alive ping is
  not a fix — it keeps the process warm but proves nothing about the loop.

The mirror worker is different: it polls Invo/HL on its own cadence and a
restart is survivable, so scale-to-zero is tolerable there (it just means missed
detections while asleep — decide whether that is acceptable).

---

## 3. Deploy the MAIN service

### 3.1 Create the app

- Koyeb: **Create Service → GitHub** → pick this repo and branch.
- Builder: **Dockerfile** (path `Dockerfile`, build context `/`).
- Instance: smallest always-on size that fits (512 MB is enough; the image is
  mostly pandas).
- Port: **8080**, protocol **HTTP**.
- Health check: **HTTP**, path `/healthz`, port 8080.
- Regions: `aws-us-east-2` (same region as Neon — avoids cross-region latency).

Render equivalent: **New → Web Service**, runtime **Docker**, health check path
`/healthz`. Fly.io: `fly launch` (it reads the `Dockerfile`), then set
`internal_port = 8080` in `fly.toml` and `min_machines_running = 1`.

### 3.2 Environment variables (main)

**Required**

| Var | Value |
|---|---|
| `DATABASE_URL` | Neon **pooled** URL for branch `production` |
| `TELEGRAM_BOT_TOKEN` | bot token |
| `TELEGRAM_CHAT_ID` | destination chat id |

**Strongly recommended**

| Var | Value | Why |
|---|---|---|
| `ADMIN_TOKEN` | random 32+ chars | without it every dashboard endpoint except `/healthz` is public |
| `PORT` | `8080` | must match the port you exposed |
| `SCAN_INTERVAL_SECONDS` | `900` | 15-minute loop (default) |
| `TELEGRAM_ENABLED` | `true` | set `false` as an incident kill switch |

**Behaviour-changing — leave at defaults unless you mean it**

| Var | Default | Note |
|---|---|---|
| `EXITS_ENABLED` | code default | exit management; 03-B-2 is CLOSED, do not re-tune |
| `EXITS_APPLY` | off | turning this on moves state |
| `LIVE_REQUIRE_VALIDATED` | on | keep on |
| `INVO_PAPER_ENABLED` | `false` | paper share |
| `INVO_PAPER_DRY_RUN` | `true` | keep `true` until reviewed |
| `INVO_EMAIL`, `INVO_GMAIL_APP_PASSWORD`, `INVO_PORTFOLIO_ID`, `INVO_DEVICE_ID`, `INVO_SIM_SIZE` | empty | only if paper share is on |
| `INVO_TRADING_REFRESH` | empty | **deferred** — do not set on first deploy |
| `MIMIC_MAIN_DASHBOARD_TOKEN` | empty | set if the mirror will read this dashboard |

Do **not** set `RAILWAY_*` — `/api/version` will report `source: unknown`; that
is cosmetic and expected.

### 3.3 First-boot sequence

1. Deploy. Watch the build log: it should end with the `pip install` layer
   succeeding. If it fails on a missing compiler, the wheel set changed — pin
   that package to the version listed in `requirements.txt` and rebuild; do not
   add `build-essential` as a first move.
2. Watch the runtime log for the dashboard line and the first scheduler cycle.
3. `GET https://<service>/healthz` → `{"ok": true}`.
4. `GET https://<service>/api/status?token=<ADMIN_TOKEN>` → status JSON.
5. Confirm rows are advancing in Neon (`signals`, `exit_decisions`).

---

## 4. Deploy the MIRROR service

Same repo, same `Dockerfile`, **different app**, no HTTP.

- Create a **Worker** (Koyeb: Service type **Worker**; Render: **Background
  Worker**; Fly: a second app / machine).
- No port, no HTTP health check — `mirror/__main__.py` opens no listener.
- Env: everything from §3.2 **except** `PORT` / `ADMIN_TOKEN`, plus:

| Var | Value | Note |
|---|---|---|
| `SERVICE_TYPE` | `mirror` | selects `python -m mirror` |
| `MIRROR_MODE` | `watch` (default) or `auto` | `auto` is headless mimic |
| `PARISTON_PORTFOLIO_ID` | portfolio to watch | read-only |
| `MIRROR_INVO_EMAIL`, `MIRROR_INVO_GMAIL_APP_PASSWORD`, `MIRROR_INVO_DEVICE_ID` | watcher login | read-only |
| `MIRROR_DATA_DIR` | `mirror_data` | container-local, **ephemeral** |
| `MIRROR_TELEGRAM_BOT_TOKEN`, `MIRROR_TELEGRAM_CHAT_ID` | optional | else logs only |
| `MIMIC_LIVE` | **`false`** | `false` = dry-run: detect + size + audit, zero orders |
| `HL_AGENT_KEY`, `HL_WALLET_ADDRESS` | required for `auto` | trade-only agent key |
| `INVO_TRADING_REFRESH` | empty | **deferred**; empty queues records to `pending_records.jsonl` |
| `MIMIC_MAIN_DASHBOARD_URL` / `_TOKEN` | main service URL + token | live TP/SL source |

> **Do not start the mirror in `auto` with `MIMIC_LIVE=true` without explicit
> operator authorization.** The mirror is a separate deploy unit, it currently
> holds 4 open positions, and `INVO_TRADING_REFRESH` is deliberately unset.
> First deploy is `MIRROR_MODE=watch`, `MIMIC_LIVE=false`.

---

## 5. Migrations

The image ships `alembic.ini` + `alembic/`, so a one-off command can migrate.
Use the **direct** (non-pooled) Neon URL for DDL.

- Koyeb: **Console / Exec** on the service, or a one-off job with the same image.
- Render: **Shell** tab on the service.
- Fly: `fly ssh console -a <app>`.

```sh
alembic upgrade head     # apply
alembic check            # expect: no new upgrade operations detected
```

`production` is already at head (verified). Run this only when a new revision
lands in the repo, and run it **before** rolling out the new image.

---

## 6. Ephemeral disk — what is lost on redeploy

The container filesystem is wiped on every redeploy/restart. Postgres holds the
system of record; these paths are still file-backed and will reset:

| Path | Content | Impact of loss |
|---|---|---|
| `data_store/scan_history.json` | scan log | history only |
| `data_store/telegram_dead_letter.json` | failed sends | failed alerts lost |
| `data_store/` share ledger, `SimLedger`, volume lock | paper bookkeeping | re-derived |
| `mirror_data/pending_records.jsonl` | queued mirror records | **queued recordings lost** |

If the mirror ever accumulates pending records worth keeping, attach a volume
and point `MIRROR_DATA_DIR` at it. Do not attach one for `data_store` before
`INVO_*` is settled — it would pin stale state across deploys.

---

## 7. Verification checklist

- [ ] Build succeeds from the repo (no local build needed)
- [ ] `/healthz` returns `{"ok": true}` from outside
- [ ] `/api/status?token=…` returns JSON (and 401 without the token)
- [ ] Runtime log shows the dashboard thread **and** a scheduler cycle
- [ ] Neon `signals` / `exit_decisions` row counts advance across two cycles
- [ ] `alembic check` clean against `production`
- [ ] Mirror worker log shows its poll loop; `MIMIC_LIVE=false` in effect
- [ ] Scale-to-zero **off** on the main service

## 8. Rollback

Redeploy the previous image tag from the platform UI. Schema is forward-only:
if the rollback image predates a migration, restore the Neon branch to its
pre-deploy state first (Neon branch restore), then redeploy.
