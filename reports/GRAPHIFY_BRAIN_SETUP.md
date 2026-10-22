# Graphify project brain — G3–G6 setup report

Date: 2026-10-22 · Graphify 0.9.93 · Python 3.14.0 · commit `fcfdc33` (HEAD, tree clean)
Zero changes to `src/`, `mirror/`, `tests/`. No Railway, no production writes, no paid API calls.

## 1. What exists now

| Piece | State |
|---|---|
| Code graph | `graphify-out/graph.json` — 2,815 nodes, 7,571 edges, 120 communities, 6 hyperedges, 0 import cycles |
| Confidence mix | 87% EXTRACTED (AST), 13% INFERRED (heuristics), 0% AMBIGUOUS |
| Human map | `graphify-out/GRAPH_REPORT.md` (28.7 KB), `graphify-out/graph.html` (3.1 MB) |
| Curated labels | `graphify-out/.graphify_labels.json` — 120 names (0 generic placeholders) |
| MCP server | registered in `~/.config/opencode/opencode.jsonc`, 10 tools, live |
| OpenCode hook | `.opencode/plugins/graphify.js` — fires before the first shell command per session |
| Safe wrapper | `scripts/graphify-safe.ps1` — forces `GRAPHIFY_BACKEND=none` |
| Agent contract | `BRAIN.md` (repo root) |
| Lessons loop | `graphify reflect` → `graphify-out/reflections/LESSONS.md` (verified working) |
| Obsidian vault | `C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot-Vault` — outside the repo, untouched |

## 2. MCP tools (G3)

| Tool | What it does |
|---|---|
| `query_graph` | BFS/DFS traversal from a natural-language question; returns nodes + edges within a token budget |
| `get_node` | Full record for one node (source file, line, community, degree, all edges) |
| `get_neighbors` | Direct neighbors of a node, optionally filtered by relation |
| `get_community` | All nodes in one community by id |
| `god_nodes` | Most-connected symbols (architectural hubs) |
| `graph_stats` | Node/edge/community counts + confidence breakdown + build commit |
| `shortest_path` | Path between two nodes (directed or undirected) |
| `list_prs` | GitHub PR list — **unused here** |
| `get_pr_impact` | Files/nodes touched by a PR — **unused here** |
| `triage_prs` | PR triage — **unused here** |

The three PR tools are compiled into the server and cannot be disabled per-tool. They are harmless:
each costs one line of the tool schema in the agent prompt (~15 tokens total) and is never called for
a local graph. Verified live after the reinstall: `graph_stats` returned 2,815 / 7,571 / 120 /
87-13-0 / `fcfdc33`.

## 3. OpenCode hook (G3)

`graphify install --platform opencode` wrote:

- `.opencode/plugins/graphify.js` (3,085 B) — a `tool.execute.before` hook that prepends a one-line
  reminder (`[graphify] knowledge graph at graphify-out/ …`) to the **first shell command of each
  session**, only when `graphify-out/graph.json` exists.
- `~/.config/opencode/skills/graphify/SKILL.md` (+ references) — the skill, user-level, not in the repo.
- **Evidence it fires:** the reminder line appeared at the top of a shell result in this session.

Limitation: the hook is a nudge, not a gate. It cannot block `grep` (that is the `--strict`
Claude-Code-only mode). The binding rules live in `BRAIN.md`.

**Incident:** `graphify install` re-installed the uv tool environment **without the `[mcp]` extra**,
which deleted `Lib/site-packages` and left the MCP server unable to start
(`ModuleNotFoundError: No module named 'graphify'`). Fix applied:
`uv tool install --force "graphifyy[mcp]"` (after stopping 3 stale `graphify-mcp` processes).
Do not run `graphify install` again; if MCP dies, run that command and restart OpenCode.

## 4. `GRAPHIFY_BACKEND=none` scoping (G5)

`.graphifyrc` supports only `viz_node_limit` and `language.<ext>` — **no backend key**, and graphify
does not load `.env` (no dotenv import anywhere in the package). So project-config scoping was
impossible; option (b) was implemented.

`scripts/graphify-safe.ps1` sets `GRAPHIFY_BACKEND=none` before every call and execs `graphify`;
`-UseLlm` removes the variable for a deliberately approved paid pass.

Verified at the library level:

```
GRAPHIFY_BACKEND=none → backend_disabled_by_env() == True
(unset)               → backend_disabled_by_env() == False
```

`graphify update .` with the wrapper ran clean (no LLM path taken, outputs untouched, labels intact).
The global `GEMINI_API_KEY` was **not** unset.

## 5. Community-label reproducibility — the blocking investigation

**Result: labels persist. The concern is moot.**

1. Labels are a first-class file: `graphify-out/.graphify_labels.json` (120 entries) plus a
   `.sig` signature sidecar and a `.pending.json` fallback file.
2. There is **no** `--community-labels` flag; persistence is by node-overlap remap, not by file input.
   `remap_communities_to_previous()` is called by **both** `cluster-only` and the `watch`/`update`
   path (`cli.py:2423,2468`, `cluster.py:434`, `watch.py:1780,2396`).
3. Empirical test (deterministic re-cluster of the real graph):
   `graphify cluster-only . --no-label --no-viz` → **120 distinct custom names, 0 generic
   placeholders**, 2,815 nodes / 7,571 edges unchanged. The command auto-backed up the 5 curated
   files to `graphify-out/backups/2026-10-22/` first.
4. `graphify update .` on unchanged code → `No code-graph topology changes detected`; labels file
   byte-identical (SHA-256 match).
5. `graphify label` (force relabel) would overwrite names with LLM/placeholder names — do not run it.

**Limit:** a full `graphify extract` into a *fresh directory* has no previous graph to overlap with,
so names restart at `Community NNN`. Rebuild in place, or copy `.graphify_labels.json` into the new
output dir before clustering.

## 6. Token impact (G6)

`graphify benchmark graphify-out/graph.json`:

```
Corpus: 140,750 words → ~187,666 tokens (naive full-corpus read)
Avg query cost: ~1,628 tokens
Reduction: 115.3x fewer tokens per query
```

Measured on the two validation questions (graph output vs `grep` + reading the responsible file):

| Question | Graph | grep + file read | Ratio |
|---|---|---|---|
| What calls `RiskGuard.validate`? | 1,026 tok | 488 + 5,401 (`supervisor.py`) = 5,889 | **5.7×** |
| Which workers are in the exit orchestrator? | 1,094 tok | 4,592 + 4,746 (`workers.py`) = 9,338 | **8.5×** |

Method: UTF-8 characters ÷ 4 (same approximation graphify's benchmark uses). The naive path assumes
the agent reads the whole target file; a careful agent might read 100 lines instead, so treat 5–9×
as the realistic per-task floor and 115× as the whole-corpus ceiling.

## 7. Validation queries (answers checked against the code, not just non-empty)

**Q1 — "What calls RiskGuard.validate?"** ✅
`Graph: .decide() [calls] src/exits/supervisor.py:L201` — exactly the expected caller
(edge labeled INFERRED). Also returns the 7 test callers. Confirmed in `src/exits/supervisor.py:201`.

**Q2 — "Trace the mimic close path from Pariston's close to Hyperliquid."** ⚠️ mostly, one hop missing.
What the graph gives:
`src/scheduler.py:476 → InvoClient.close_paper_trade` (Pariston side, separate HTTP action) and
`AutoBot._sims_sync (auto.py:769) → _close_coin (auto.py:687)`; the graph then has
`AutoBot --uses--> HlBridge` (INFERRED) but **not** `_close_coin → HlBridge.close`.
Code confirms the last hop: `auto.py:702 self.hl.close(coin)` → `mirror/hl.py:140 HlBridge.close`.
Cause: a dynamic attribute call (`self.hl.close`) that the AST pass cannot bind. Documented as a
standing rule in `BRAIN.md`: confirm dynamic-dispatch hops with one file read. The `_close_coin →
_ev/_notify/_tpsl_of/audit` fan-out and the test edges are all correct.

**Q3 — "Which workers are in the exit orchestrator?"** ✅ all 5
`StructureWorker` (workers.py:106), `TargetWorker` (181), `StopWorker` (267),
`CounterSignalWorker` (328), `RiskRewardWorker` (395).

## 8. Test suite (G6)

```
python -m pytest          → 605 passed, 0 failed, 0 skipped  (junit: tests=605, failures=0, errors=0)
git status --porcelain    → M .gitignore, ?? .opencode/, ?? BRAIN.md, ?? scripts/graphify-safe.ps1
```

No source file changed, so the suite result is independent of this work.

### Test Count Correction (forward-only)

Prior reports cited **607**. Accurate collection is **605** (599 `def test_*` functions + 6
parametrized expansions, 29 files, 0 skipped). All prior "green" claims remain valid — 0 failed at
every checkpoint. `git status` is clean and all 29 `test_*.py` files are collected, so no test was
deleted or silently skipped: the 607 figure was a miscount.

**From this commit forward the baseline is 605 collected / 0 failed / 0 skipped.**

Older reports are deliberately **not** reconciled and should not be edited — this is the single
forward correction of record. Recorded as a hard fact in `BRAIN.md` §7.

Note: the `pytest` console script on PATH belongs to Python 3.11 and fails collection (6 dependency
errors); run the suite with `python -m pytest` (Python 3.14).

## 9. Obsidian (G4 — no-op, as directed)

`C:\Users\grimm\OneDrive\Desktop\Crypto-AlrtBot-Vault` — 2,935 notes + `graph.canvas`, outside the
repo, not regenerated. Regenerate (if ever lost) after a graph rebuild, then re-open as a vault.
Documented in `BRAIN.md`; a pointer also needs to go in `README.md` (see §11).

## 10. Files and git

Committed now: **nothing** (all work is uncommitted).

Recommended commit set:

| Path | Why |
|---|---|
| `BRAIN.md` | the agent contract |
| `scripts/graphify-safe.ps1` | backend scoping |
| `.opencode/plugins/graphify.js` | always-on reminder |
| `.gitignore` (modified) | ignores generated graph output |

Ignored (disposable): `graphify-out/` (covers `graph.json`, `graph.html`, `GRAPH_REPORT.md`,
`.graphify_labels.json`, `memory/`, `reflections/`), `.graphify/`, `.graphify_cache/`,
`graphify-out/`, `obsidian_vault/`.

`reports/*.md` is already ignored by an existing rule (`!reports/README.md` is the only exception),
so **this report is not committed**. To version it, add `!reports/GRAPHIFY_BRAIN_SETUP.md`.

Optional: to version the curated labels (120 hand-checked names), change the ignore from
`graphify-out/` to `graphify-out/*` + `!graphify-out/.graphify_labels.json`.

## 11. Warnings and limitations

- **INFERRED edges (13%) are leads, not facts.** Dynamic calls (`self.hl.close`), DI/registry
  dispatch, and doc-derived links are the weak spots.
- **Community names are curated by the agent, not the model.** Good enough to navigate; not
  canonical.
- **`graphify install` can break its own tool env** (dropped the `[mcp]` extra). Recovery command in
  §3 and in `BRAIN.md`.
- **`graphify reflect` mangles non-ASCII separators** in `LESSONS.md` (`1 useful � 0 dead ends`).
  Cosmetic graphify bug; the content is correct.
- **`explain` is label-global.** `explain "validate"` resolves to `src/config.py:137`. Use full node
  ids or `path::symbol` for methods.
- **Rebuild cost.** `update` is cheap and label-safe; a from-scratch `extract` is not (loses labels,
  and with the global key set could bill — always through the wrapper).
- **README pointer not added:** the instruction "note in README how to regenerate the vault" would
  edit a tracked file, which is outside the read-only scope. The same text lives in `BRAIN.md` §
  *Obsidian vault* / *Regenerating*; add it to `README.md` on request.

## 12. Copy-paste paragraph for future agent prompts

> This repo has a knowledge graph at `graphify-out/` (2,815 nodes, 7,571 edges, 120 named
> communities, built at commit `fcfdc33`). Before grepping for structure — who calls what, where a
> subsystem lives, which workers implement a decision — query the graph instead: MCP tools
> `query_graph` / `get_node` / `shortest_path` / `get_community`, or
> `.\scripts\graphify-safe.ps1 query "<question>" --budget 2000`. Read
> `graphify-out/reflections/LESSONS.md` at session start if present. Treat `INFERRED` edges as
> leads (13% of edges; dynamic calls like `self.hl.close` are not bound) and confirm them with one
> file read before asserting a call path. Never run bare `graphify extract` / `label` / `install` —
> `GEMINI_API_KEY` is global and `install` breaks the MCP env; use the wrapper. Full rules:
> `BRAIN.md`.
