# BRAIN.md — ask the graph before you grep

This repo has a knowledge graph of itself: symbols, files, calls, imports, docs, and 148
communities (71 of them still carrying a curated name — see §6 rule 4). Use it to locate code
cheaply, then open only the files that matter.

- Graph: `graphify-out/graph.json` — **3,006 nodes / 7,930 edges / 148 communities** (built at `ec8527e`).
- Confidence: **87% EXTRACTED** (AST), **13% INFERRED** (heuristics), 0% ambiguous.
- Human-readable summary: `graphify-out/GRAPH_REPORT.md` (28 KB). Interactive map: `graphify-out/graph.html`.
- Session lessons: `graphify-out/reflections/LESSONS.md` (read it first if it exists).
- The graph is **disposable and regenerable**. It is a map, not the source of truth.

## 0. Install incident — read this first

**Never run `graphify install`.** It re-installs the uv tool environment as bare `graphifyy`,
silently drops the `[mcp]` extra, deletes `Lib/site-packages`, and leaves the MCP server dead
(`ModuleNotFoundError: No module named 'graphify'`). This happened on 2026-10-22.

Recovery (order matters — the MCP server holds a file lock on `Scripts\`, so kill it first):

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*graphify-mcp*" -or $_.CommandLine -like "*graphifyy\Scripts\python*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800
uv tool install --force "graphifyy[all]"      # or "graphifyy[mcp]" for the minimal install
```

Then **restart OpenCode** so the MCP client respawns the server.

Check (10 tools expected):

```powershell
$sp = "$env:APPDATA\uv\tools\graphifyy\Lib\site-packages\graphify"
(Select-String -Path "$sp\serve.py" -Pattern 'name="(query_graph|get_node|get_neighbors|get_community|god_nodes|graph_stats|shortest_path|list_prs|get_pr_impact|triage_prs)"').Count   # must print 10
```

`graphify-mcp --help` does **not** list tools — it only shows transport flags (`--transport`,
`--port`, `--graph`). Use the count above, or the tool list OpenCode exposes.

## 1. Pre-flight (once per session)

1. Read this file.
2. Verify the MCP server: the 10-tool count above must be 10. If fewer, the `[mcp]` extra is gone —
   run the recovery in §0.
3. Suite baseline: **620 collected / 0 failed / 0 skipped**. Verify:
   ```powershell
   python -m pytest --collect-only -q | Select-String "^tests/" |
     ForEach-Object { [int]($_.Line.Split(":")[1]) } | Measure-Object -Sum | Select-Object -ExpandProperty Sum
   ```
4. Read `graphify-out/reflections/LESSONS.md` if present — corrections from past sessions.
5. Broad "how is this project shaped?" → read `graphify-out/GRAPH_REPORT.md`.

## 2. Query-before-grep discipline

For any structural question, query the graph **before** reading files:

| Question | Tool |
|---|---|
| "What calls X?" | `shortest_path` / `affected "<node-id>" --relation calls` |
| "What does X do?" | `get_node` |
| "Where is X?" | `query_graph "<question>" --budget 2000` |
| "What are the central modules?" | `god_nodes` |
| "What community does X belong to?" | `get_community` |
| "How does X reach Y?" | `shortest_path` |

Fall back to grep/read only when: the graph returns no nodes; the edge is INFERRED and needs code
verification; or the question is about runtime behavior rather than structure.

## 3. Commands

Always use the wrapper. It sets `GRAPHIFY_BACKEND=none` so no paid LLM call can happen
(`GEMINI_API_KEY` is set globally on this machine).

```powershell
.\scripts\graphify-safe.ps1 explain "ExitSupervisor" --all      # node + every connection
.\scripts\graphify-safe.ps1 query "who decides exit actions" --budget 2000
.\scripts\graphify-safe.ps1 path "scheduler.py::BotScheduler._run" "HlBridge.close"
.\scripts\graphify-safe.ps1 affected "src_exits_risk_guard_riskguard_validate" --relation calls
.\scripts\graphify-safe.ps1 god-nodes --top 15
```

MCP tools (same graph, no wrapper needed — the server only reads JSON):
`query_graph`, `get_node`, `get_neighbors`, `get_community`, `god_nodes`, `graph_stats`,
`shortest_path`. `list_prs`, `get_pr_impact`, `triage_prs` are GitHub-PR extras, unused here.

**Ambiguity is real.** `explain "validate"` returns `src/config.py:137`, not RiskGuard's. Prefer
exact labels for classes/top-level functions, and `path::symbol` ids for methods:

```powershell
.\scripts\graphify-safe.ps1 explain "src_exits_risk_guard_riskguard_validate"
.\scripts\graphify-safe.ps1 path "src/invo/client.py::.close_paper_trade()" "HlBridge.close" --undirected
```

## 4. If you need X, query Y

| You need | Query this |
|---|---|
| Exit decision logic (merge/invalidate/close) | `explain "ExitSupervisor"` → `src/exits/supervisor.py` |
| Risk / veto rules before an exit action | `explain "RiskGuard"` → `src/exits/risk_guard.py` |
| Exit worker polling + decision ledger | `explain "ExitMonitor"`; community *Exit Monitor & Decision Ledger* |
| Target/RR math, stop, counter-signal, structure, risk-reward | `explain "TargetWorker"` / `"StopWorker"` / `"CounterSignalWorker"` / `"StructureWorker"` / `"RiskRewardWorker"` |
| Exit ledger + settlement accounting | community *Exit Settlement Baselines*; `src/exits/ledger.py` |
| Mimic close path (Pariston → Hyperliquid) | `explain "_close_coin"`, `explain "HlBridge.close"` |
| Mirror sizing / config | community *Mimic Config & Sizing*; `explain "MimicConfig"` |
| Invo auth, OTP, feed login | communities *Invo Session & Feed Auth*, *Invo Email OTP Auth* |
| Scheduler loop / daily run | `explain "BotScheduler"` → `src/scheduler.py` |
| Signal pipeline end-to-end | `explain "SignalPipeline"` → `src/pipeline.py` |
| Strategies + registry | community *Strategy Registry*; `explain "create_strategy"` |
| Config schema / all settings | `explain "AppConfig"` → `src/config.py` |
| Dashboard routes | community *Dashboard HTTP Server*; `src/dashboard.py` |
| Atomic file writes / persistence | `explain "atomic_write_json"` |
| Market data fetch + cache | `explain "CachedFuturesClient"`, community *Historical Data Cache* |
| Backtest scoring / outcome evaluation | `explain "SignalOutcomeEvaluator"` |
| Telegram formatting / delivery retries | communities *Telegram Signal Formatting*, *Telegram Delivery Retries* |
| Who calls a specific function | `affected "<full-node-id>" --relation calls` |
| Tests covering a module | query the symbol; test nodes share its community |

## 5. Token discipline

- Default query budget: **2,000 tokens** ≈ ~1,600 actual. Lower it (`--budget 500`) for narrow questions.
- The whole corpus is ~187,700 tokens; `graphify benchmark` reports **115×** reduction per query.
- Measured per task: **5.7×** (callers of `RiskGuard.validate`) and **8.5×** (exit workers) versus
  grep + reading the responsible file.
- A truncated result tells you the budget needed — prefer narrowing (`--seed`, `context_filter`,
  `get_node`) over raising the budget.
- Do not read a whole file when `get_node` returns the symbol you need.
- Do not re-query the same thing twice in a session — keep the answer in working memory.

## 6. Hard rules

1. **Never run `graphify install`.** It drops the `[mcp]` extra and breaks the server — see §0.
2. **Never run bare `graphify extract` / `label`.** They can trigger billed LLM extraction via the
   global `GEMINI_API_KEY`. Use `scripts/graphify-safe.ps1`.
3. **Any edge tagged INFERRED is a lead, not a citation.** Verify in source before acting on it.
   Known gap: the mimic close path. The graph shows `AutoBot --uses--> HlBridge` but not the method
   hop, because `auto.py:702 self.hl.close(coin)` is a dynamic attribute call the AST cannot bind.
   The actual binding is `mirror/hl.py:140 HlBridge.close`. **Treat graph paths ending at `HlBridge`
   as incomplete and verify the `.close()` binding in code.**
4. **Community labels survive only while the community set is stable.** The remap is by node overlap:
   when the graph has drifted since the last rebuild, community ids change and unmatched names are
   dropped. Measured 2026-10-22 — a stale graph (built at `fcfdc33`) rebuilt twice took the label
   store **120 → 83 → 71** names while communities went **120 → 140 → 148**. A small `update` keeps
   names; a rebuild after long drift does not. Back up `graphify-out/.graphify_labels.json` before a
   rebuild you expect to drift, and never refresh names with `graphify label` (billed — rule 2);
   accept hub-derived names instead.
5. **Suite baseline is 620 / 0 / 0.** Any deviation means code changed — investigate before proceeding.
6. Dynamic dispatch generally (`getattr`, DI-registered handlers) is not bound by the AST pass.
   Grep is still correct for literals, error strings, and comments the extractor drops.

## 7. Test baseline (hard fact)

`python -m pytest` → **620 collected, 0 failed, 0 skipped** — 31 files, 614 `def test_*` functions
+ 6 parametrized expansions.

Reconciliation: **605 at `72bb525`** (599 functions + 6) **+ 2 (W9/W10) + 8 (Phase 04A
harness) + 5 (W-DD Option A) = 620**.

| commit | test added |
|---|---|
| `f62d9a1` (W9) | `test_guard_p2_rejects_tp_widening_and_fails_closed` |
| `95db541` (W10) | `test_p3_shadow_logs_without_changing_invalidation` |
| Phase 04A | `tests/test_phase04a_harness.py` (8 tests: correlation math, proxy bounds, determinism) |
| `4ddd798` (W-DD A2) | `tests/test_scoring_breakdown.py` (5 tests: persist, `None` on legacy rows, join by `signal_id`) |

The earlier "607 is a miscount" note was correct only for the pre-W9 tree at `72bb525`. It is
superseded by this derivation: 605 + 2 + 8 + 5 = 620. From this commit on, **620 / 0 / 0** is the
baseline.

Count it from the tree, not from a report:
```powershell
python -m pytest --collect-only -q | Select-String "^tests/" |
  ForEach-Object { [int]($_.Line.Split(":")[1]) } | Measure-Object -Sum | Select-Object -ExpandProperty Sum
```

Note: the `pytest` console script on PATH belongs to Python 3.11 and fails collection (6 dependency
errors). Use `python -m pytest` (Python 3.14).

**PG mode is a second, different baseline.** `PG_TEST=1` (with `TEST_DATABASE_URL` exported — nothing
loads `.env` for a test run) executes the same suite against Postgres:
**593 passed / 27 skipped / 0 failed**. The 27 skips are `_JSON_ONLY` (`tests/conftest.py:115`) —
they assert on JSON artifacts Postgres never writes. So **620/0/0 is the JSON baseline only**; a PG
run reporting 620 passed is a red flag, not a win. Verify the test endpoint differs from
`DATABASE_URL` before running — a wrong value would `TRUNCATE` production.

## 8. Obsidian vault

`C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot-Vault` (outside the repo; 2,935 notes + `graph.canvas`).
Open with Obsidian → *Open folder as vault*. Regenerate only after a graph rebuild, then reload.
Never committed.

## 9. Regenerating

- Code changed → `.\scripts\graphify-safe.ps1 update .` (AST only, no LLM; labels survive a small
  drift, not a long one — see §6 rule 4).
- Docs changed → run the `/graphify --update` skill, or accept stale doc nodes.
- Full rebuild (loses curated labels) → see `README.md` → *Project brain*.

## 10. Session end

If you learned something that matters next session, run `graphify reflect` — it writes
`graphify-out/reflections/LESSONS.md` from `graphify-out/memory/` (deterministic, no LLM).
That file is read at the start of the next session.

## 11. Current work state (volatile — maintained by the operator)

- 03-B-2 closed: exit management is **not** the production defect.
- **Window 1 CLOSED EARLY 2026-10-24 (operator decision)** — terminated at day 3 of 30 because the
  running system was losing capital while the fixes were already committed. 49 settled signals are
  archived as a **pre-deploy baseline, not a 30-day result**; H1–H4 are **paused/inconclusive, not
  failed**; the post-window decision tree was **not applied**. `reports/OBSERVATION_PROTOCOL_2026-10.md`
  §7 defines **Window 2**, which starts on the deploy-success date.
- **Postgres is live (Neon, 2026-10-25).** Project `withered-bird-68619336`, region
  `aws-us-east-2`, branches `production` (deploy) and `test` (suite). Window 1 rows were migrated to
  `production` and verified: **0 mismatches across 14 tables / 3,624 rows**, `alembic check` clean.
  `DATABASE_URL` selects Postgres at runtime (`src/db/engine.py`); absent, every store falls back to
  `data_store/*.json`. `PG_TEST=1` + `TEST_DATABASE_URL` runs the suite on the `test` branch —
  baseline **593 / 27 / 0** (§7). Known gaps: `sim_reservations.baseline` has no column, and
  `SimLedger`, `scan_history`, `p3_shadow`, `invo_activity`, `invo_sync_audit`,
  `telegram_dead_letter` have no PG path yet.
- **Deploy AUTHORIZED 2026-10-24, operator executes — the agent does not deploy.** Railway is
  **abandoned** (account limits); the target is a container PaaS built from the repo `Dockerfile`
  (Koyeb / Render / Fly.io — see `docs/DEPLOY_KOYEB.md`). Local `HEAD` at push time; runtime code was
  `f80ff11`. Main service first, mirror second, each verified before the next. **Nothing has been
  built or run in the cloud yet** — the local `docker build` was stopped on request (bandwidth), so
  the first cloud build is the first real test of the `Dockerfile`. Pre-deploy hardening (cleanup,
  lint, deps, Dockerfile, docs) is committed locally and **not pushed**.
  **Free-tier blocker:** Koyeb/Render free instances scale to zero, which stops the 15m scheduler;
  scale-to-zero must be off, or the free tier cannot host the main service.
- **Window 2 protocol (armed on deploy success).** Same H1–H4 claims and thresholds, evaluated only
  on post-deploy settled rows; Window 1 rows are never pooled in. New: `scoring_breakdown` makes H4
  Measured, per-strategy R becomes a **4-family** number, and fee drag is read against the stated
  0.12%-of-notional model (conservative vs Hyperliquid taker/taker 0.09%).
- **Next scheduled action: the Sunday weekly report** (n, mean net R, median net R, top-5
  concentration, triggers) — or an immediate trigger alert, whichever comes first.
- **Graph rebuilt 2026-10-25 — the freeze is LIFTED by operator decision** (Phase 5 of the pre-deploy
  hardening pass). Was frozen at 3,006 nodes / 7,930 edges / 148 communities / 71 curated labels.
  Now **3,163 nodes / 8,333 edges / 142 communities / 65 curated labels** — the delta is the
  Postgres layer, which the old graph predated: 46 nodes under `src/db/` (engine, models, base) and
  8 under `alembic/`. Labels went **71 → 65**, and this is the mildest degradation of the three
  rebuilds so far (measured 120 → 83 → 71 → 65): **0 labels renamed, 6 dropped** because their
  communities no longer exist (14 Exit Monitor & Decision Ledger, 31 Target Worker & RR Math,
  39 Historical Data Cache, 64 Paper Portfolio, 93 Scoring & Technical Debt, 95 Dataset Split).
  Every surviving label kept its curated name. Pre-rebuild labels:
  `graphify-out/.graphify_labels.backup-2026-10-25.json`; graphify's own backup:
  `graphify-out/2026-10-25/`. Built with `.\scripts\graphify-safe.ps1 update .` (AST only,
  `GRAPHIFY_BACKEND=none`). `graphify label` was NOT run (billed). The next rebuild should still
  carry an explicit label strategy — the drift is slowing, not solved.
- **Window monitoring schedule (re-armed at Window 2 start).**
  - **Weekly report, every Sunday — 5 lines, no prompt required:** n (settled signals this week), mean net R,
    median net R, top-5 concentration, triggers fired (yes/no, which).
  - **Produce it autonomously** when the session does any work on a Sunday, or when the operator opens a session
    after Sunday and that week's report has not been produced. Do not wait to be asked.
  - **Immediate trigger alerts** — report at the next interaction, before other work:
    (i) **bullish regime week** — 7-day BTC 4h regime = BULL → pause protocol per A4, flag the single-regime trap;
    (ii) **5 consecutive trades with R > 2.0** → tail event, note it, change no decision;
    (iii) **CI lower bound < 0 on the rolling 30-day** → strong-fail territory, escalate.
  - On any trigger: report to the operator first. No action without authorization.
- RSI + `ema_trend` retired. BTC = momentum-only, ADA = volume_breakout-only.
- Phase 04 (entry redesign) is the next major workstream — options spec in
  `reports/PHASE_04_ENTRY_REDESIGN_SPEC.md` (written; no option chosen).
- **Entry kill-switch (standing rule):** halt new entries if (i) the mimic's $16 cap is hit,
  (ii) a 30-day sample still has a CI lower bound ≤ 0 **and** a median ≤ −0.5R, or (iii) fees
  exceed gross in any rolling 7-day window.
- **BTC coverage watch (standing rule):** BTC produces ~1 signal / 11 days. If a 30-day scan
  produces **< 3** BTC signals, Phase 04 must address BTC coverage explicitly.
- `p3_shadow` writes `data_store/p3_shadow.jsonl` (main-service volume, survives deploys).
- **Production runs pre-`49ff810` code — Measured, three ways:** no `applied` key on any of the
  1,315 in-window exit decisions (`49ff810`), no `scoring_breakdown` on any published signal
  (`4ddd798`), and `ema_trend_20_50` still publishing (`a29831a`). **61 commits** since the last
  confirmed production reference `f2dfde8`; **at least 32** since `49ff810`. Railway auth is valid
  as of 2026-10-24; the exact running revision is still unconfirmed.
- **Fee model risk (D1, open).** `fees_cost_r` is the harness model
  `2 × (0.04% taker + 0.02% slippage) = 0.12%` of notional per round trip — **conservative**
  versus Hyperliquid base taker/taker (0.09%) and ~4× maker/maker (0.03%). Window 1's −8.736R fee
  drag is therefore an upper bound until a real Invo/HL fee statement replaces it. One statement
  settles it; do not cite 0.178R/trade as fact before then.
- W6 delivery is at-least-once: the queue replays byte-identical `clientTxId`s (64-bit
  since `5fcc4e6`), so safety depends on Invo deduping server-side (unproven from the repo).
- `EXITS_APPLY=true` on Railway; local stays false in fixtures.
- `MIMIC_LIVE=true` on Railway; $16 real money.
- Statistical caveat: 11 days / 204 entry signals, 5.1 days / 97 exit signals, one regime.
  Window 1 adds 2.8 days / 59 published / 49 settled / one regime (bearish 44 of 59 published).
  No claim is a 30-day result.
- **W-LIVE audit complete (`f80ff11`).** `reports/W_LIVE_AUDIT_2026-10-24.md` is the Window 1
  evidence record: mean net **−0.158R**, median −1.096R, sum −7.736R, CI95 [−0.545, +0.229];
  gross **+1.000R** vs fees **−8.736R**; 89% of the net loss from retired `ema_trend_20_50`.
  **11 backlog items** queued in `reports/W_S_DEFECTS_BACKLOG.md` (items 1–11; 6–11 came out of
  this audit). No intervention and no deploy was performed by the agent.
- **Momentum `+1.42R` is `User-provided` and does NOT reproduce.** Production ledger: 25
  `momentum_20` rows, 24 `final`, **net −1.727R** over 24, **−1.470R over the 17 resolved**
  (mean −0.086R). Neither 21 nor 22 signals reproduces and the sign is opposite. `+1.42R` is
  most likely a harness/backtest figure — cite the two separately or not at all.
- **Baseline populations (2026-10-22):** the "+0.03" baseline is the **candidate-level
  (pre-gate)** correlation on the 6,438-candidate TRAIN replay; the **published-signal**
  correlation is **+0.12** (204). Complementary measurements of different objects, not
  contradictory — every report must state which population it measures.

### Regime Dominance Analysis (2026-10-22)

Regime weight in isolation: partial r **+0.101** (204) / **+0.109** (177 live-only).
Non-regime components, with regime held out: partial r **+0.109** (204) / **+0.112**.
`corr(non-regime, regime)` = **−0.582** — the two are not redundant.
Regime is not trend-following in disguise: vs EMA200-15m r = **+0.311** (partial +0.122);
vs 1h momentum r = **+0.016** (partial +0.101). Neither operator test threshold
(r ≥ 0.8 for duplication, ≥ 0.15 for dominance) fires.

**Correction to "regime is the entire score" — retracted by the operator.** Regime carries
a real but weak independent signal; non-regime components carry equal independent signal.
The honest statement is: **all components are weak; none is doing the work alone.**

### Decision: (f) status quo + observe (2026-10-22)

- **(a) rebuild — rejected:** W-S found no mechanically broken strategy.
- **(d) raise gate — rejected:** no R-weighted support. Gate 75/80 = halt (n = 4 / 0);
  gate 70 → −30% R; 65 → 70 discards 71 signals worth +10.27R.
- **(e) regime filter — rejected:** same money on fewer trades, and a hard filter is
  un-testable inside a single regime window (the single-regime trap).
- **(f) observe — adopted:** fixes the data debt, produces real out-of-sample data, keeps
  every option open.

Operator acknowledgment on record: **$16 cannot prove profitability.** The distribution is
tail-dependent (top-5 trades = 60% of profit). CI lower **+0.003** at gate 60 live-only is
the edge of noise — *the possibility of an edge*, not an edge. The 30-day test asks "does a
measurable signal exist and do the pre-registered hypotheses hold", not "does the strategy
work".

### 30-day observation protocol (standing, 2026-10-22 → +30d or n = 200)

Governs the Phase 04B/04C go/no-go. Hypotheses and criteria live in
`reports/PRE_REGISTERED_HYPOTHESES_2026-10.md` (H1 gate 65 vs 60, H2 hard regime filter,
H3 side-opposed vs aligned, H4 regime-only vs full score); the window and the five-band
verdict live in `reports/OBSERVATION_PROTOCOL_2026-10.md`.

- Minimum useful n = **100** published signals. Primary metrics: mean / median net R,
  CI 95% lower bound, top-5 concentration, win rate, fee/gross ratio.
- Verdict bands: strong pass (CI lower ≥ +0.10, median ≥ −0.05) · weak pass (≥ +0.05,
  ≥ −0.10) · inconclusive · weak fail (CI lower < 0 or median < −0.20) · strong fail
  (CI lower < −0.10, median < −0.30, or fee/gross > 0.70).
- Re-evaluation triggers: a bullish 7-day BTC 4h regime inside the window (re-examine H2's
  single-regime trap); a 5-trade streak of >2R wins (mean moves, median does not).
- **No behaviour change ships during the window.** Persistence/provenance work only.
- Pre-committed: no hypothesis is re-worded after seeing the data. A miss is reported as a
  miss.
