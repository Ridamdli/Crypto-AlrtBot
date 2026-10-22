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
3. Suite baseline: **605 collected / 0 failed / 0 skipped**. Verify:
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
5. **Suite baseline is 605 / 0 / 0.** Any deviation means code changed — investigate before proceeding.
6. Dynamic dispatch generally (`getattr`, DI-registered handlers) is not bound by the AST pass.
   Grep is still correct for literals, error strings, and comments the extractor drops.

## 7. Test baseline (hard fact)

`python -m pytest` → **605 collected, 0 failed, 0 skipped** — 29 files, 599 `def test_*` functions
+ 6 parametrized expansions.

Prior documents cite **607**; that was a miscount, not a lost test. `git status` is clean and all 29
test files are collected, so no test was deleted or skipped. Correction is forward-only: from this
commit on, **605 / 0 / 0** is the baseline. Do not "reconcile" older reports.

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
- Phase 04 (entry redesign) is the next major workstream — spec in
  `reports/PHASE_04_ENTRY_REDESIGN_SPEC.md` (pending).
- Production runs pre-`49ff810` code; ~10 local commits ready, awaiting deploy decision.
- `EXITS_APPLY=true` on Railway; local stays false in fixtures.
- `MIMIC_LIVE=true` on Railway; $16 real money.
- Statistical caveat: 11 days / 204 entry signals, 5.1 days / 97 exit signals, one regime.
  No claim is a 30-day result.
