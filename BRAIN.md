# BRAIN.md — ask the graph before you grep

This repo has a knowledge graph of itself: symbols, files, calls, imports, docs, and 120 named
communities. Use it to locate code cheaply, then open only the files that matter.

- Graph: `graphify-out/graph.json` — **2,815 nodes / 7,571 edges / 120 communities** (built at `fcfdc33`).
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
3. Suite baseline: **615 collected / 0 failed / 0 skipped**. Verify:
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
4. **Community labels persist** across `cluster-only` and `update` (node-overlap remap, verified
   120/120 names kept). Only a from-scratch extract into a fresh directory loses them — never do
   that without backing up `graphify-out/.graphify_labels.json`.
5. **Suite baseline is 615 / 0 / 0.** Any deviation means code changed — investigate before proceeding.
6. Dynamic dispatch generally (`getattr`, DI-registered handlers) is not bound by the AST pass.
   Grep is still correct for literals, error strings, and comments the extractor drops.

## 7. Test baseline (hard fact)

`python -m pytest` → **615 collected, 0 failed, 0 skipped** — 30 files, 609 `def test_*` functions
(432 top-level + 177 methods) + 6 parametrized expansions.

Reconciliation: **605 at `72bb525`** (599 functions + 6) **+ 2 (W9/W10) + 8 (Phase 04A
harness) = 615**.

| commit | test added |
|---|---|
| `f62d9a1` (W9) | `test_guard_p2_rejects_tp_widening_and_fails_closed` |
| `95db541` (W10) | `test_p3_shadow_logs_without_changing_invalidation` |
| Phase 04A | `tests/test_phase04a_harness.py` (8 tests: correlation math, proxy bounds, determinism) |

The earlier "607 is a miscount" note was correct only for the pre-W9 tree at `72bb525`. It is
superseded by this derivation: 605 + 2 + 8 = 615. From this commit on, **615 / 0 / 0** is the
baseline.

Count it from the tree, not from a report:
```powershell
python -m pytest --collect-only -q | Select-String "^tests/" |
  ForEach-Object { [int]($_.Line.Split(":")[1]) } | Measure-Object -Sum | Select-Object -ExpandProperty Sum
```

Note: the `pytest` console script on PATH belongs to Python 3.11 and fails collection (6 dependency
errors). Use `python -m pytest` (Python 3.14).

## 8. Obsidian vault

`C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot-Vault` (outside the repo; 2,935 notes + `graph.canvas`).
Open with Obsidian → *Open folder as vault*. Regenerate only after a graph rebuild, then reload.
Never committed.

## 9. Regenerating

- Code changed → `.\scripts\graphify-safe.ps1 update .` (AST only, no LLM, keeps labels).
- Docs changed → run the `/graphify --update` skill, or accept stale doc nodes.
- Full rebuild (loses curated labels) → see `README.md` → *Project brain*.

## 10. Session end

If you learned something that matters next session, run `graphify reflect` — it writes
`graphify-out/reflections/LESSONS.md` from `graphify-out/memory/` (deterministic, no LLM).
That file is read at the start of the next session.

## 11. Current work state (volatile — maintained by the operator)

- 03-B-2 closed: exit management is **not** the production defect.
- RSI + `ema_trend` retired. BTC = momentum-only, ADA = volume_breakout-only.
- Phase 04 (entry redesign) is the next major workstream — options spec in
  `reports/PHASE_04_ENTRY_REDESIGN_SPEC.md` (written; no option chosen).
- **Entry kill-switch (standing rule):** halt new entries if (i) the mimic's $16 cap is hit,
  (ii) a 30-day sample still has a CI lower bound ≤ 0 **and** a median ≤ −0.5R, or (iii) fees
  exceed gross in any rolling 7-day window.
- **BTC coverage watch (standing rule):** BTC produces ~1 signal / 11 days. If a 30-day scan
  produces **< 3** BTC signals, Phase 04 must address BTC coverage explicitly.
- `p3_shadow` writes `data_store/p3_shadow.jsonl` (main-service volume, survives deploys).
- **Deployment policy (standing): no deploy until Phase 04 completes AND the operator
  explicitly authorizes a deploy event.** `reports/DEPLOY_READINESS.md` (Wave 1 /
  Wave 2 / Hold) is a **future plan, not an imminent execution**. The open input is
  Phase 04 direction, not deploy authorization.
- Production runs pre-`49ff810` code; 37 local commits are held. Railway auth expired —
  running revision unconfirmed.
- W6 delivery is at-least-once: the queue replays byte-identical `clientTxId`s (64-bit
  since `5fcc4e6`), so safety depends on Invo deduping server-side (unproven from the repo).
- `EXITS_APPLY=true` on Railway; local stays false in fixtures.
- `MIMIC_LIVE=true` on Railway; $16 real money.
- Statistical caveat: 11 days / 204 entry signals, 5.1 days / 97 exit signals, one regime.
  No claim is a 30-day result.
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
