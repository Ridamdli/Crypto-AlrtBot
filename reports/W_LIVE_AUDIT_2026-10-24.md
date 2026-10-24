# W-LIVE — Live Production Audit (2026-10-24)

Read-only audit of the Railway-hosted bot during the active observation window
(2026-10-22 → 2026-11-21). No code change, no deploy, no restart, no env write.
Phase 1–3 read logs and MCP metadata only; Phase 4–5 (this revision) read the pulled
`data_store/*.json` and `mirror_data/*` files over read-only `railway ssh`.

Labeling: **Measured** = read directly from Railway/MCP output or repo. **User-provided**
= operator or prior-session figure carried in. **Estimated** = my inference, stated as such.
No figure in this report is a 30-day result. Window age at audit time: **2.8 days**.

**Revision 2 (2026-10-24):** operator Invo screenshots incorporated as §3.1; four defects queued
as backlog items 6–9 (`reports/W_S_DEFECTS_BACKLOG.md`); aggregation rule added to §7; report
committed with a `.gitignore` exception. Findings and window status unchanged — no production
action, no restart.

**Revision 3 (2026-10-24, Phases 4–5 complete):** file access granted, ten files pulled
byte-exact into `prod_evidence/2026-10-24/` (gitignored). §3–§7 now carry computed numbers
instead of "blocked" placeholders. Three findings are new and material:

- **The window's net loss is fee drag, not signal quality.** Gross realized R over 49 settled
  trades is **+1.000R**; modeled fees are **−8.736R**; net is **−7.736R**. Fees are **8.7×** gross.
- **`ema_trend_20_50` is retired by standing decision but published 8 in-window signals**
  (7 settled, −6.896R, 85.7% stopped out).
- **The momentum `+1.42R / 21` figure does not reproduce.** The production outcome ledger holds
  24 settled `momentum_20` rows at **−1.727R net** (gross −0.750R).

---

## 1. Executive summary

1. **The window is producing.** 248 scans, 192 candidates, **59 published signals**, 59/59
   delivered to Telegram, **51 outcomes booked**, zero errors in scan, supervision, or
   tracking paths. Nothing is stalled.
2. **No BLOCKER.** No crash loop, no restart, no OOM, no exposed secret, no uncontrolled loss,
   no traceback in either service across 2.8 days. **No trigger forces a stop** — but note that
   the fee-exceeds-gross condition is met on every available span, and CI lower ≤ 0 is true
   (−0.545) while the 30-day sample gate is not; see *Triggers* below.
3. **Paper equity fell over the window: 1028.75 → 941.93, −86.82 (−8.44%)**, with an
   intra-window low of **803.30 (−21.9%)** on 10-23 14:05 recovering to −8.44% by 10-24 15:33.
   *Measured, self-reported `[Paper]` log line.*
4. **The loss is fee drag.** Over 49 settled window signals: mean net **−0.158R**, median
   **−1.096R**, sum **−7.736R**, CI95 **[−0.545, +0.229]** (includes 0). Decomposed: gross
   realized **+1.000R**, modeled fees **−8.736R** (0.178R per trade). Gross expectancy per trade
   is **+0.020R** against a **0.178R** cost — an **8.7×** cost-to-edge ratio. *Measured from
   `outcomes.json`; the fee figure is the harness's own fee model, not an exchange statement.*
5. **One genuine defect found: `CLOSE_PAPER_POSITION` does not terminate supervision.**
   7 distinct positions were told to close **78 times** (one 26×, one 43×), state stayed `open`
   up to 33h, and supervision kept re-deciding after the close. Closes are deduped server-side,
   so this is a **measurement/observability defect, not a capital defect**.
6. **Attribution is no longer blocked — and it immediately surfaces two contradictions of
   standing figures.** Worker identity *is* persisted (6,575 assessments over 1,315 decisions);
   `RR_DEAD` is measurable (31 in-window, not 0/8); RiskGuard ran on **71/1,315** decisions
   (5.4%), not the 124/124 carried in from the prior snapshot.
7. **A retired strategy family is still publishing — deploy lag, not a strategy question.**
   `ema_trend_20_50` was retired by standing operator decision (`AGENTS.md` §5) and the
   retirement is committed locally (`a29831a`, 2026-10-22), but production runs pre-`49ff810` and
   predates it. It published **8** in-window signals, 7 settled at **−6.896R** with **85.7%**
   stopped out — **89% of the window's net loss**. Excluding it, the four intended families sum
   to **−0.840R over 42** (mean −0.020R). Tracked as backlog item 11.

---

## 2. MCP status + service state

**MCP: working, authenticated.** `whoami` → `gusgrifit@gmail.com`, status REGISTERED.
71 Railway tools inventoried. No auth expiry, no fallback to SSH, no `login` attempted.

| | Main (Pariston) | Mimic (Ouroboross) |
|---|---|---|
| Project | `crypto-alrtbot` `76992030-8ce0-4e25-bfec-4d9879e51a48` | same |
| Environment | `production` `977d2864-481f-47f0-a986-6639d498298b` | same |
| Service | `crypto-alrtbot` `f59ad547-bf14-4c5c-aa51-f98efc4ae467` | `pariston-mimic` `2cbcea58-ee2b-4e44-b0b2-50cec7f14888` |
| Last deploy | `2026-10-13T21:40:35Z` SUCCESS (snapshot `aa556dcb`) | `2026-10-14T18:14:31Z` SUCCESS |
| State | `live`, no staged changes | `live`, no staged changes |
| Uptime | 11.9 d | 10.4 d |

Service IDs match the prior session. The MCP does not surface a commit hash — only
deploy id, snapshot id and `meta.reason: deploy`. **Commit-hash confirmation is blocked**;
what *is* confirmed is that neither service has deployed since 10-13/10-14, so production
still runs **pre-`49ff810`** and none of this session's local work is live. *Measured.*

**48 h resource health** — no restart signature, no leak:

| | CPU avg / max | Mem avg / min / max | Disk |
|---|---|---|---|
| main | 0.065 / 1.15 | 0.135 GB / 0.126 / 0.147 (flat) | 0.061–0.281 GB |
| mimic | 0.0044 | ~0.048 GB flat | — |

The main CPU max of 1.15 is a scan-burst spike, not sustained. Memory is flat across 2,881
samples on both services → no leak, no OOM path. *Measured.*

**Env (names/presence only — values are redacted by the connector, `valuesRedacted: true`;
no value was printed and none was derivable).**

- Present, main: `EXITS_APPLY`, `INVO_PAPER_ENABLED`, `INVO_PAPER_DRY_RUN`,
  `LIVE_REQUIRE_VALIDATED`, `INVO_*`, `TELEGRAM_*`, `ADMIN_TOKEN`, 13 `RAILWAY_*`.
- **Absent, main:** `EXITS_STRICT_MODE`, `INVO_TRADING_REFRESH_PRESENT`.
- Present, mimic: `MIMIC_LIVE`, `MIMIC_MAX_USD`, `MIMIC_MAX_CONCURRENT`, `MIMIC_MAX_PCT`,
  `MIMIC_ACCOUNT_USD`, `MIMIC_MIN_REMAINING_USD`, `HL_AGENT_KEY`, `HL_WALLET_ADDRESS`,
  `MIRROR_*`, `MIMIC_MAIN_DASHBOARD_*`. (*names only*)

**Delta vs the 2026-10-19 snapshot:** no deploy, no flag change, no staged change, no restart,
no resource trend change. The running system is materially the same system that was audited
on 10-19; only its behaviour in the new window is new. *Measured.*

**Scan continuity (silent-downtime hunt).** 248 scans, largest inter-scan gap **16.6 min**
against a 16-min cadence, expected count for the span ≈ 246. A process restart or crash loop
would show a gap in tens of minutes to hours. **There is no such gap.** *Measured.*

---

## 3. Observation window progress

| Metric | Value | Source |
|---|---|---|
| Window age | 2.8 d / 30 d | Measured |
| Scans | 248 (expected ≈246) | Measured |
| Candidates | 192 | Measured |
| Published signals | **59** across 23 non-zero scans | Measured |
| Candidate→publish | 192 → 59 (30.7%) | Measured |
| Telegram delivered | 59 / 59 | Measured |
| Outcome finalizations **in-window** | **51** | Measured |
| Ledger size | 250 → 309 (+59, exactly the publish count) | Measured |
| Total booked (lifetime) | 249 → 299 | Measured |
| Paper equity | 1028.75 → **941.93** (−86.82, −8.44%) | Measured |
| Intra-window low | 803.30 (−21.9%) at 10-23 14:05 | Measured |

The ledger cross-check is clean and worth stating: **published 59 = ledger +59 = booked +50/+51**.
Two independent counters (outcome tracker `finalized_this_run` summed = 51; paper portfolio
`newly_booked` summed = 51) agree exactly. That rules out double-booking and silent
mis-counting in the tracking path. *Measured.*

**Reconciliation of 51 vs 49.** 51 bookings happened in-window, but only **49** belong to
signals *published* in-window. The other 2 are pre-window signals that settled after the window
opened (`SIG-20261021-XRPUSDT-CE53C8`, `SIG-20261021-SOLUSDT-5B02EE`, both STOPPED_OUT).
Every hypothesis below uses the **49-signal window-published population**. Sensitivity: adding
the 2 carry-ins gives n=51, mean **−0.194R**, median −1.096R, sum −9.909R — the conclusion does
not move. *Measured.*

**Signal distribution per coin** (61 distinct signals reached exit supervision; 14 coins):
ETH 7, LINK 6, AVAX 6, XRP 5, SOL 5, BNB 5, DOGE 5, ADA 4, NEAR 4, ZEC 4, DOT 3, UNI 3,
HYPE 3, **BTC 1**. *Measured.* BTC coverage: 1 signal in 2.8 d extrapolates to ~10 per 30 d,
so the "<3 signals in a 30-day scan" Phase 04 BTC decision is **not** forced. Thin, not triggered.

**Scan shape.** 210 of 248 scans produced **zero** candidates; the distribution is bimodal:
0 candidates (210 scans), 1–2 (20), 3–5 (6), 6–10 (6), 11+ (6), peak 24 in one scan. Publication
is therefore **event-clustered, not steady** — see §8 item 5.

### 3.1 Operator-confirmed observations (Invo screenshots, 2026-10-24)

Three items the operator confirmed directly from the Pariston and Ouroboross Invo interfaces.
Recorded as **User-provided** — they are not re-derivable from logs, and they are the reason two
of the items below are ranked the way they are.

1. **DOT (+13.61%) was published by Pariston and never mirrored by Ouroboross.** This is the
   concrete cost of §8 item 4: **1,252** record failures with sessions rotating every **7.1 min**
   (*Measured*). The link between the churn and the miss is a strong inference, not a proof —
   backlog item 6 opens with "investigate root cause", precisely because an Invo session cap or
   rate limit would produce an identical signature.
2. **Losing trades were held ~3 minutes** (DOGE, AVAX, NEAR, BNB, SOL). This explains the −8.44%
   paper equity as **immediate stop-outs, not exit mismanagement** — consistent with 03-B-2 being
   CLOSED and with a zero-error supervision path. The loss pattern is therefore an **entry-side**
   property, and an uncapped cross-section (§8 item 5) makes simultaneous stop-outs likely.
   *Phase 5 qualification:* the outcome ledger is **15-minute bar resolution** and cannot see a
   3-minute hold (§7), so this remains **User-provided** — and the loss is now better explained
   by fee drag (§7.1) plus one retired family (§4) than by hold duration alone.
3. **The `CLOSE_PAPER_POSITION` loop means exit analysis must group by unique `signal_id`.**
   78 close decisions = **7 positions**. Now a binding rule in the backlog (item 7).

**Capital status, per operator directive:** the close loop and the session churn are
**capital-neutral in paper** — no principal at risk, closes deduped server-side — but **on the
mimic side they lose real money**, because a missed mirror is a missed +13.61% trade. None of that
justifies a mid-window fix; it is why backlog item 6 sits first in the queue.

### 3.2 Phase 4 — window metrics (n=49 settled, 10 still active)

| Metric | Value | Label |
|---|---|---|
| Settled / still active | **49** / 10 | Measured |
| Mean net R | **−0.158** | Measured |
| Median net R | **−1.096** | Measured |
| Sum net R | **−7.736** | Measured |
| Win rate | **30.6%** (15/49) | Measured |
| CI95 on mean | **[−0.545, +0.229]** — includes 0 | Measured |
| Gross profit R | +27.306 | Measured |
| Gross loss R | −35.042 | Measured |
| Gross realized R (before fees) | **+1.000** | Measured |
| Fees R | **−8.736** (0.178/trade) | Measured (fee model) |
| Top-5 winners | 9.676R = **35.4%** of gross profit | Measured |
| Exit status mix | STOPPED_OUT 29, TP1_HIT 15, EXPIRED 4, ACTIVE 1 | Measured |

**Top-5 concentration is 35.4% of gross profit**, and the five best trades are tightly clustered
at 1.915–1.952R (all TP1 exits) — the profit base is *narrow but not single-trade dependent*.
The distribution is bimodal: 29 trades at exactly −1.0R gross, 15 at exactly +2.0R gross, 5 at
0.0R. *Measured.*

**Composition of the 49 — state it precisely.** 49 rows have `final=True`, but not all are
*resolved*: 29 stopped out, 15 hit TP1, **4 EXPIRED at 0.0R**, and **1 is still open at the data
edge** (LINKUSDT-400159, −0.054R mark-to-market). The resolved subset is therefore **44 trades:
sum −7.682R, mean −0.175R, win rate 34.1%**. The conclusion does not move; the count is stated so
the 49 is not mistaken for 49 completed round trips. *Measured.*

**The headline is not "strategies lose".** Gross expectancy is **+0.020R per trade** — slightly
positive. The system loses because a round trip costs **0.178R** in modeled fees at 10× leverage
on a ~$2.1k notional against a $10 risk unit. See §7.1.

### Triggers — one fired

- **Bullish regime week?** No. Regime counts in-window: BEARISH 174, NEUTRAL 63, BULLISH 11.
  Published signals: bearish 44, neutral 13, bullish 2. *Measured.*
- **5 consecutive wins > 2R?** **Not fired — and it could not have fired.** The best net R
  anywhere in the window is **1.952R**; **zero** settled trades exceeded 2.0R net. *Measured.*
- **CI lower bound ≤ 0?** **True** (−0.545), but the standing kill-switch requires a **30-day
  sample**; this is a 2.8-day sample. Not armed. *Measured, sample-gated.*
- **Fees exceed gross?** **Met on every span available.** Whole window: fees 8.736R vs gross
  1.000R. Per day: 10-22 fees 2.549 > gross −11.000; 10-24 fees 1.847 > gross −3.000; 10-23
  gross +15.000 > fees 4.340. The literal *rolling 7-day* window cannot be fully formed until
  10-29, so this is recorded as **met on the available span, not yet as a 7-day verdict**.
  *Measured.*

**Read this as one sentence:** on today's numbers, if the window closed now, the entry
kill-switch conditions (CI lower ≤ 0 **and** median ≤ −0.5R **and** fees > gross) would all be
met. The 30-day sample gate is the only thing standing between this window and a halt decision.
That is not a recommendation to act — it is the number to watch.

### Hypotheses H1–H4 (n=49 settled)

- **H1 (gate 65 vs 60):** gate 60 arm n=49 mean −0.158R; **gate 65 arm n=35 mean −0.000R**,
  median −1.100, win 37.1%. The sub-65 band carries the entire loss: **n=14, mean −0.552R,
  win 14.3%, ≈ −7.73R of the −7.736R total**. Directionally this supports the 65 gate, and the
  mechanism is clean — the 60–64 band is where the money goes. **But** the pre-registered
  failure branch requires mean < +0.10 **or** n < 50: both are true today (mean −0.000, n=35).
  With 27 days left this is a mid-window reading, not a verdict. **If the window closed today,
  H1 would be recorded FAILURE by sample, not success.** *Measured; verdict withheld.*
- **H2 (regime filter):** aligned n=36 mean **+0.051R** (win 38.9%, median −1.088) vs all n=49
  mean −0.158R. The pre-registered failure branch (filtered mean < +0.20R) is met today, but
  n=36 is far below the n ≥ 80 success requirement, so it is a mid-window reading.
  **Protocol flaw to fix before the close:** the "filtered total R ≥ 0.9 × unfiltered total R"
  condition is degenerate when the unfiltered total is negative — 1.84R ≥ −6.96R is true by
  construction. It cannot discriminate. Flagged, not changed (thresholds are frozen).
- **H3 (side-opposed):** **not testable in this window — zero opposed signals.** All 49 settled
  signals were either aligned (36) or in a neutral regime (13); none was side-opposed. The
  pre-window "14 of 204 opposed" figure **does not reproduce** here. H3 needs a window where
  signals actually oppose the regime; this one cannot supply it.
- **H4 (regime-only scoring):** unmeasurable and *confirmed by absence* — production runs
  pre-`49ff810`, so `scoring_breakdown` does not exist in `outcomes.json` at all. Nothing
  changed, nothing measurable.

---

## 4. Strategy quality analysis

Per-strategy attribution is now available from `outcomes.json` (`strategy_id` is persisted on
every outcome row), not from the logs — the logs still do not carry it. *Measured.*

| Strategy | Published | Settled | Coins | Mean net R | Median | Sum R | Stop-out | TP hit |
|---|---|---|---|---|---|---|---|---|
| `donchian_20` | 23 | 23 | 7 | −0.256 | −1.112 | −5.878 | 60.9% | 26.1% |
| `volume_breakout_20` | 15 | 7 | 4 | **+0.800** | +1.922 | **+5.598** | 28.6% | 57.1% |
| `bollinger_atr_20_2.0` | 12 | 12 | 3 | −0.047 | −1.098 | −0.560 | 58.3% | 33.3% |
| `ema_trend_20_50` | **8** | 7 | 5 | **−0.985** | −1.321 | **−6.896** | **85.7%** | 14.3% |
| `momentum_20` | 1 | 0 | — | — | — | — | — | — |

Sums check: −5.878 + 5.598 − 0.560 − 6.896 = **−7.736R**, the window total. *Measured.*

**Finding 1 — 89% of the window's net loss comes from a family that is already retired.**
`ema_trend_20_50` was retired by standing decision and the retirement is committed locally
(`a29831a`, 2026-10-22), but **production runs pre-`49ff810` and predates that commit**, so the
family is still live there. Excluding it, the four intended families sum to **−0.840R over 42
settled signals** (mean **−0.020R**) — essentially flat. This is not a strategy-quality problem;
it is **deploy lag**. It also means the window is measuring a **5-family system, not the intended
4-family system**, which is a validity caveat on every per-strategy conclusion below and a
material one for 04B.

**Finding 2 — `volume_breakout_20` is the only family with a positive point estimate**, +0.800R
mean over 7 settled (57% TP, 28.6% stopped). n=7 is far too small to act on, but it is the one
family worth watching through the close.

**Finding 3 — `donchian_20` carries the most volume** (23 settled, −5.878R, 60.9% stopped). It is
the best-sampled family and still negative; the largest single loss belongs to `ema_trend_20_50`.

**Finding 4 — the momentum figure does not reproduce.** `outcomes.json` holds **25** `momentum_20`
rows, **24 with `final=True`**: 11 stopped out, 5 TP1, 1 TP2, **6 still open at mark-to-market**,
1 expired. Lifetime net over all 24 is **−1.727R**; over the **17 resolved** it is **−1.470R
(mean −0.086R)**. **Neither 21 nor 22 reproduces**, and the carried `+1.42R` figure is **not
supported by production data — it is negative on every cut**. The likely explanation is that
`+1.42R` was a harness/backtest number, not a production outcome; that distinction matters and
should be stated wherever the figure is cited. Only **1** momentum signal was published in-window
and it has not settled. *Measured.*

**Finding 5 — the Bollinger gate check is answered.** 12 Bollinger signals published in-window,
**all on AVAX / HYPE / SOL** (confidence 60/62/67/72). **None on ADA, NEAR or ZEC.** The 7
would-be-published signals (ADA, NEAR×4, ZEC) that backlog item 2 cites **did not appear**, which
is consistent with the compression gate blocking them. *Measured, consistent — not proof of
causality, since the gate's own decisions are not logged.*

**Assessment:** the strategy layer is not broken, and the window's aggregate loss is not
distributed evenly across it — it is concentrated in one retired family plus a uniform fee drag
(§7.1). The observability gap that forced the Phase 1–3 "cannot be produced" verdict is now
closed for strategy, and remains open for the *publish* path (strategy id still is not logged at
publish time).

---

## 5. Worker quality analysis

**The Phase 1–3 "blocked" verdict was wrong, and this corrects it.** Worker identity **is**
persisted: `WorkerAssessment.to_dict()` writes `worker_id` and `worker_version`
(`src/exits/models.py:165`), and every one of the **1,315** in-window decisions carries an
`assessments[]` array — **6,575** assessments in total. What is missing is only the *log line*
(`monitor.py:170` prints the action, not the proposer). **No schema change is needed to measure
worker behaviour; only the file read.** Backlog item 10 is rewritten accordingly.

**Worker verdict mix (in-window, Measured):**

| Worker | Verdicts | Proposals (non-NO_CHANGE) | Adopted | Adoption |
|---|---|---|---|---|
| `structure` | THESIS_INTACT 1174, THESIS_INVALIDATED 89, THESIS_WEAKENED 52 | 89 | **89** | **100%** |
| `counter` | NO_OPPOSITION 1247, WEAK_OPPOSITION 61, GENUINE_REVERSAL 7 | 7 | 4 | 57.1% |
| `riskreward` | RR_OK 1256, **RR_DEAD 31**, RR_DEGRADED 28 | 59 | 7 | 11.9% |
| `stop` | STOP_OK 700, STOP_TIGHTEN_CANDIDATE 615 | 615 | 49 | 8.0% |
| `target` | TARGETS_OK 790, TARGETS_UNREALISTIC 525 | 471 | 15 | 3.2% |

**Overall:** of **166** decisions that were not `NO_CHANGE`, **163 adopted a worker proposal
(98.2%)** and 3 had no proposer at all (all `FLAG_CONFLICT`, which the supervisor raises itself).
So when the supervisor acts, it is almost always acting *on* a worker. *Measured.*

**Read the adoption column carefully — it is not a "supervisor ignores workers" finding.**
`stop` proposed a tighten on 615 decisions and got 49 actions; `target` proposed on 471 and got
15. But a signal is re-evaluated many times, and `monitor.py:162` dedupes an identical
`decision_key`, so the same proposal recurs on every cycle without being re-applied. 615
proposals across 61 signals ≈ 10 per signal, against 49 actions across 25 signals ≈ 2 per
signal. **The honest metric is "first proposal per signal", which this audit has not computed**;
per-decision adoption systematically understates and should not be quoted as a worker-quality
number. That is a measurement design note, not a defect.

**`RR_DEAD` is now measurable and the carried figure does not reproduce.** 31 in-window verdicts,
7 adopted. The pre-window "RR_DEAD 0/8 adoptable" cannot be re-tested against a different
population, but the claim that RR_DEAD is never adoptable is **contradicted** — 7 of 31 were.
*Measured.*

**Silent-worker detection is now possible and no worker is silent.** All five workers produced
verdicts on every decision; the lowest-volume (`counter`) still emitted 1,315 verdicts. *Measured.*

**`structure` is the only worker whose proposal is adopted unconditionally (89/89).** It is also
the worker that owns `INVALIDATE_PENDING_SETUP`, the one action with a measurable R consequence
(§7). That asymmetry — one worker effectively decides cancellations, the other four advise — is
worth a design look post-window, but it is not evidence of a defect on 89 events.

---

## 6. Orchestrator quality analysis

**Supervisor volume** — now read from `exit_decisions.json` (authoritative), not the log recount:
1,315 in-window decisions over **61** distinct signals, 0 errors. *Measured.*

| Action | Decisions | % | Distinct signals |
|---|---|---|---|
| NO_CHANGE | 1,149 | 87.4% | 50 |
| CLOSE_PAPER_POSITION | 78 | 5.9% | **7** |
| TIGHTEN_STOP | 49 | 3.7% | 25 |
| ADJUST_TAKE_PROFITS | 15 | 1.1% | 14 |
| INVALIDATE_PENDING_SETUP | 11 | 0.8% | 11 |
| REDUCE_PAPER_POSITION | 7 | 0.5% | 7 |
| FLAG_CONFLICT | 6 | 0.5% | 6 |

The ledger supersedes the two earlier tallies (scheduler dict 1,305; log recount 1,314) and
resolves the `FLAG_CONFLICT 3 vs 6` discrepancy in favour of **6**. *Measured.*

**Critical read-through correction.** The scheduler dict is **decisions, not positions**.
78 closes = **7 positions**. 49 tightens = 25 positions. Anyone reading
`{'CLOSE_PAPER_POSITION': 78}` as "78 positions closed" is wrong by **11×**. This is the
single easiest way to misreport this window.

**Idempotency is working.** `monitor.py:162` skips re-recording an identical decision key, which
is why 1,149 NO_CHANGE decisions produced far fewer log lines than evaluations. Three independent
tallies — scheduler dict 1,305, log-line recount 1,314, ledger 1,315 — agree within 0.8%; the gap
is the paging boundary, not a leak. *Measured.*

**Decision density — the earlier "~4× increase" is withdrawn.** In-window the rate is
**1,315 decisions / 61 signals / 2.8 d ≈ 7.7 per signal-day**. The pre-window comparison
(2,000 / 97 ≈ 1.9) is **invalid**: `exit_decisions.json` is a **capped ring buffer**
(`max_decisions: int = 2000`, `ledger.py:37`, trimmed at line 153) and holds **exactly 2,000**
decisions spanning only **2026-10-20T08:49 → 2026-10-24T19:06**. "2,000" is the cap, not a total,
so any density computed from it is an artifact of when the file was last trimmed. What *is*
measurable: the re-decision rate is high (≈7.7 per signal-day, ≈21 decisions per signal over the
window), which is the mechanism that amplifies the CLOSE-repeat defect. *Measured; prior
comparison retracted.*

**RiskGuard: measurable now, and far rarer than carried.** Present on **71 of 1,315** decisions
(**5.4%**), always `ok: true`, and only on the three actions that move a stop or a target:
`TIGHTEN_STOP` 49, `ADJUST_TAKE_PROFITS` 15, `REDUCE_PAPER_POSITION` 7. Sample reason:
`stop 1.352746 -> 1.359539: risk distance never wider than original`. The pre-window "124/124"
figure **does not reproduce** and should not be cited. Interpretation: RiskGuard is a
*post-proposal* veto, not an always-on gate, and in this window it never vetoed anything —
consistent with a guard that is only consulted on monotone-improving adjustments. *Measured.*

---

## 7. Exit management quality analysis

**Classification, deduped to `signal_id` first** (the binding rule from Revision 2). Rules, stated
so they can be attacked: `CLOSE` → CORRECT if the signal later stopped out, PREMATURE if it later
hit TP, else NEUTRAL. `INVALIDATE` → CORRECT if the entry never triggered, MISSED if it did.
`TIGHTEN`/`REDUCE`/`ADJUST_TP` → PREMATURE if the signal later hit TP, CORRECT if it stopped out,
else NEUTRAL. `FLAG_CONFLICT` is informational. **These rules use the final outcome as the
counterfactual — there is no forward-bar replay — so "CORRECT" means "the final state agrees with
the decision", not "the decision caused a better result".** That limit is real and is not papered
over. *Measured, method-limited.*

| Action | Signals | CORRECT | PREMATURE | NEUTRAL / unknown | MISSED | Settled Σ net R | Mean |
|---|---|---|---|---|---|---|---|
| `CLOSE_PAPER_POSITION` | 7 | 6 | 0 | 1 | – | **−6.593** (6 settled + 1 open at MTM) | −0.942 |
| `INVALIDATE_PENDING_SETUP` | 11 | 1 | 0 | 0 | **10** | **−2.799** | −0.254 |
| `TIGHTEN_STOP` | 25 | 6 | 8 | 11 | – | **+8.492** (15 settled) | +0.566 |
| `REDUCE_PAPER_POSITION` | 7 | 1 | 5 | 1 | – | **+7.600** (6) | +1.267 |
| `ADJUST_TAKE_PROFITS` | 14 | 3 | 3 | 8 | – | **+2.524** (7) | +0.361 |
| `FLAG_CONFLICT` | 6 | – | – | 6 | – | **−2.336** (3) | −0.779 |

**The R-weighted read is the one that matters** (*Measured*):

- **`INVALIDATE` has positive aggregate R-value and a bad hit rate.** The 11 invalidated signals
  would have summed **−2.799R**. It missed 3 winners (ZECUSDT-4BD342 **+1.915R**,
  DOGEUSDT-301089 +1.774R, ETHUSDT-A00BE8 +1.635R = **+5.32R** foregone) and avoided 7 losers
  (**−8.12R**). Net: cancelling **saved ≈2.8R**. A 10/11 "MISSED" count reads as a failure and is
  not one — this is exactly why a count-only read is banned for policy.
- **`CLOSE` shows no measurable R benefit.** All 7 closed signals were already losing at the data
  edge — six stopped out at ≈−1.1R, one still open at −0.054R — summing **−6.593R**. Closing did
  not avoid the loss; it fired at a loss. Recorded as an observation — **exit policy stays closed
  per 03-B-2**, this does not reopen it.
- **`REDUCE` fired on winners** (+7.600R over 6 settled, 5 wins, 5 flagged PREMATURE). Trimming
  exposure on positions that then ran is an opportunity cost, visible here for the first time.
- **`FLAG_CONFLICT` fired on losers only** (−2.336R over 3 settled). Small n; consistent with a
  conflict flag being informative rather than actionable.

**Holding time — the operator's ~3-minute claim is not visible here, and the ledger cannot see
it.** Settled median **90 min** (p25 15, p75 585, max 1,500); the STOPPED_OUT subset median
**30 min**, p25 **15**, min **15**. **Every value is a multiple of 15** and **17 of 45** settled
trades sit exactly on the **15-minute floor**. The outcome ledger is bar-resolution, so a
3-minute hold is below its measurement floor. This neither confirms nor refutes the operator's
Invo observation — it states that the outcome file is the wrong instrument for that claim.
*Measured, resolution-limited.*

**Aggregate evidence that is measurable:**

- 51 booked outcomes in 2.8 d is a **high turnover** rate (≈18/day) against 61 supervised
  signals — positions are being resolved, not left dangling.
- No `[Exits] Ledger unreadable / write failed / Ledger unhealthy` lines → the ledger was
  healthy for all 1,315 decisions (`apply=True` on every one). Exit application never fell
  back to record-only. *Measured — and a genuinely good result.*

**The one exit-quality defect I can prove** (§8 item 1) contaminates any per-signal
classification for the 7 closed positions: close does not terminate supervision, so their
decision sequences contain post-close re-decisions that no bar-level method would classify
sensibly.

**Aggregation rule (binding from this audit on).** Exit decisions **must be grouped by unique
`signal_id`**, never counted per decision. 1,315 decisions sit on 61 signals; closes are
overstated **11×** (78 decisions → 7 positions). Any Phase 5 classification run later against
`exit_decisions.json` must dedupe to `signal_id` first, or it will report a 78-position sample
that does not exist.

**Direction of the loss and the equity path — one explanation.** 29 of 49 settled signals (59%)
stopped out at exactly −1.0R gross and the median settled trade is −1.096R net, so the loss is
**entry-side** and the exit layer is not the cause: supervision ran with **0 errors**, RiskGuard
never vetoed, and no exit action shows negative aggregate R-value except `CLOSE` (which fired on
already-losing positions). The equity path is the same story at daily resolution — 10-22
**−11.000R** gross (13 stops / 1 TP) is the −21.9% low at 803.30, 10-23 **+15.000R** (9 stops /
**12 TPs**) is the +17.3% recovery off it, 10-24 **−3.000R** (7 stops / 2 TPs) is the drift since.
There is no hidden exit mechanism in either half of that path. **This section does not recommend
exit tuning, and 03-B-2 stays closed.** *Measured.*

### 7.1 Economic decomposition — the loss is fee drag (new in Revision 3)

This is the most consequential number in the audit and it was invisible until the files were
read. *Measured from `outcomes.json`; the fee figure is the harness's own fee model
(`policy.fee_cost_r`), not an exchange statement.*

| Component | R | Per trade (n=49) |
|---|---|---|
| Gross realized (before fees) | **+1.000** | **+0.020** |
| Fees | **−8.736** | **−0.178** |
| **Net** | **−7.736** | **−0.158** |

**Fees are 8.7× the gross edge.** Gross expectancy is slightly *positive*; the system loses
because a round trip costs **0.178R** while the average trade earns **0.020R**. Mechanism: 10×
leverage on a ~$2,119 notional against a $10 risk unit means ~$1.9 of round-trip cost at taker
rates ≈ 0.19R — the model is internally consistent.

**Cross-check against equity — first time the R series and the dollar series agree.**
−7.736R × $10 risk = **−$77.36** against a measured equity change of **−$86.82**. The 11% gap is
consistent with slippage and per-trade rounding. The two independent measurements now tell the
same story, which they did not in Revision 1.

**What this means for 04B (and what it does not).** At this fee model, break-even needs gross
expectancy ≥ **0.178R per trade**; the system produces 0.020R. Closing that gap by win rate alone
is not plausible on this sample, and the two levers that would do it — fewer, larger trades
(reduce frequency ~9×, or widen TP from +2.0R to ~+2.35R) — are **behaviour changes that cannot
be made inside the window**. The honest statement is: *this configuration cannot be profitable
after costs, regardless of which strategy wins*, and that is a cost-structure finding, not a
signal-quality finding.

**Two caveats that must be resolved before anyone cites this in a decision:**

1. `fees_cost_r` is a **model**, not a reconciled exchange fee. If real fills are maker or the
   account has a fee tier, the drag scales down proportionally. Verify against Invo/HL statements
   before treating 0.178R as fact.
2. The 10× leverage is what makes the fee 0.178R rather than ~0.018R. **Leverage is therefore a
   cost multiplier here, not only a risk multiplier** — a framing the current risk discussion
   does not contain.

---

## 8. Issues found, ranked

### 1. CRITICAL (measurement integrity) — `CLOSE_PAPER_POSITION` does not terminate supervision

- **Frequency:** 78 close decisions across 7 positions; **69 of them on 2 positions**
  (LINKUSDT-400159 **26×**, ADAUSDT-E50B12 **43×**).
- **First seen:** 2026-10-23T02:46:55Z (LINK), 2026-10-23T02:30:52Z (ADA).
- **Evidence:** after the first CLOSE the state remains `open`, plan frozen at v4, and the
  signal continues to be evaluated for 33+ h — e.g. LINK tail:
  `09:13 CLOSE → 09:29 CLOSE → 09:45 NO_CHANGE → … → 11:06 NO_CHANGE`, all `state=open`.
- **Impact:** (a) supervision churn and log inflation; (b) the "78 closes" figure is
  unusable as a position count; (c) exit-quality attribution for those 7 signals is
  untrustworthy; (d) any live-mode analogue would re-send close orders — **but see below**,
  the server dedupes them.
- **Why this is NOT a BLOCKER:** closables are idempotent downstream.
  `Invo exit-sync close … already gone server-side, stop retrying` × 5 and
  `already-applied — reconciled, stop retrying` × 10 show the `clientTxId` dedupe works
  (this also independently confirms the W6 assumption). No double-close, no double-book:
  booked (51) = finalized (51). **No principal is at risk from this defect.**
- **Capital status (operator directive, §3.1):** neutral in paper. On the mimic side the same
  loop is churn that a mirror does not need to carry, but it dedupes rather than double-closes.
  The money-losing half of the mimic problem is item 4 (missed mirrors), not this loop.
- **Action:** **defer — backlog item 7.** Logging it now is the correct disposition; fixing it
  mid-window would change exit behaviour and invalidate the window.

### 2. WARNING — `Sim reconcile DIVERGENT` on every run, never reconciled

- **Frequency:** 248 / 248 runs (100%).
- **First seen:** before window (10-12 ids in `local_only`); `diff_reserved` grew **40 → 60**.
- **Detail:** `bank_drift: 0.0` (cash agrees), `local_only` list grows with pre-window signal
  ids (`SIG-20261012-LINKUSDT-2651DF`, `SIG-20261016-DOTUSDT-CF258A`, …). The reservation bank
  disagrees with the server, cash does not.
- **Impact hypothesis:** locally-reserved sim notional is **overstated by $60** and drifting up.
  A persistent one-way divergence is the signature of "closes don't retire reservations" —
  coherent with item 1. Direct effect on sizing is **not verifiable** from logs.
- **Action:** post-window; but capture the `local_only` id list at window end so the drift can
  be reconstructed offline.

### 3. WARNING — `Invo exit-sync audit gap: unknown audit event type: 'suppressed'`

- **Frequency:** **2,943** occurrences, ~10.5/min, spanning the whole window
  (10-22 11:26 → 10-24 09:18 on the pages sampled).
- **Impact:** log noise that will swamp any future `WARNING`-level scan; possible loss of the
  audit information carried by the `suppressed` events. No functional failure observed — exit
  sync still completed 248 runs, 41 modified, 0 closed.
- **Action:** post-window; map the event type. This is a one-line enum addition, not a redesign.

### 4. WARNING — mimic `RECORD FAILED … no trading session (refresh failed)`

- **Frequency:** **1,252** bootstrap lines with `errors > 0`; 1,031 of them are the canonical
  `{'mimicked': 0, 'skipped': 1, 'errors': 1}`. `sims sync` carries 726 errors.
- **First seen:** throughout window; `Invo session rotated silently` **561 times**, median gap
  **7.1 min**.
- **Mechanism:** the trade is placed and the fill stands (`RECORD FAILED ADA (fill stands):
  no trading session (refresh failed)`); only the *record write* fails because the session
  rotated. So HL positions exist that the mirror ledger never recorded.
- **Impact hypothesis:** the mimic's ledger understates its own open positions. Sizing is
  account-constrained anyway (positions bump to the HL minimum ~$10.50, account ~$11.94), so
  the exposure consequence is small — but the *record* is wrong, and that matters if the mimic
  is ever used as a live-mirroring rehearsal.
- **Note:** these are **not** stderr duplicates of INFO lines. The earlier "every mimic line is
  logged twice" caveat does **not** apply to these; they are real counter values.
- **Confirmed cost (operator, §3.1):** **DOT (+13.61%) was published by Pariston and never
  mirrored.** One missed trade of that size on a ~$11.94 account is larger than the entire
  recorded P&L swing; the mirror's opportunity cost is not a rounding error.
- **Action:** post-window (backlog item 6). One root cause — session refresh vs record timing,
  possibly an Invo session/rate limit rather than a code bug — likely explains both the rotations
  and the failures. **Do not patch locally before that investigation.**

### 5. WARNING — correlated publication bursts with no cross-sectional cap

- **Frequency:** 6 scans published ≥11 signals; two studied in detail:
  10-23 22:42 (**15 candidates → 10 published**), 10-24 14:27 (**24 → 11**), both BEARISH,
  `rejected: 0`.
- **Impact:** 10–11 new same-direction positions opened into a single move. No cross-sectional
  or per-direction exposure limit is visible in behaviour. With operator confirmation that
  losers were held ~3 minutes (§3.1), this is the mechanism that turns one bad move into
  **10 simultaneous stop-outs** — the concentration is real even though the drawdown low
  (10-23 14:05) predates the first studied burst (22:42).
- **Action:** post-window design question, not a bug — **backlog item 8**. Clearest candidate
  for a Phase 04 coverage/exposure decision.

### 6. INFO — `bootstrap mimic failed: size rounds to zero at szDecimals`

- Observed for ZEC (2 placements still succeeded). Minimum-size rounding drops some signals
  entirely. Expected on an ~$11.94 account; worth a counter so skipped-because-too-small is
  distinguishable from skipped-because-stale.

### 7. INFO — dashboard fully token-gated, no unauthenticated health surface

- `/`, `/api/health`, `/api/signals`, `/api/status` all **401** `{"error":"unauthorized
  (bad/missing token)"}`. Correct security posture; it also means the only readable production
  surface during an audit is logs. That is why §11 is long.

### 8. INFO — window P&L is negative, and it is cost-driven

- 1028.75 → 941.93 (−8.44%). Lifetime equity 941.93 across 299 bookings ⇒ the system is **−5.8%
  since inception**, and the pre-window period was net **positive** (+2.9% over 249 bookings).
  One regime, 2.8 days. **The mechanism is now known and it is not the signals:** gross
  +1.000R vs fees −8.736R over 49 settled trades (§7.1). Gross expectancy is +0.020R/trade
  against a 0.178R cost. This says the current *cost structure* loses money in this regime, not
  that the strategies are broken.

### 9. WARNING — production still runs a retired strategy family (deploy lag)

- **Finding:** `ema_trend_20_50` published **8** in-window signals (7 settled, **−6.896R**,
  85.7% stopped out). The retirement is committed locally at **`a29831a` (2026-10-22)**, which is
  **after** the deployed base `49ff810`. The local registry no longer contains the family
  (`src/strategies/__init__.py` registers 4 families); production still runs 5.
- **Impact:** (a) **89% of the window's net loss** comes from a family the operator already
  retired; (b) the window is measuring a 5-family system, so per-strategy R from this window is
  not a clean read on the intended 4-family system; (c) no code is wrong — nothing has been
  deployed since 10-13, by design.
- **Action:** **no mid-window deploy.** The fix is the next authorized deploy, not a patch. Tracked
  as backlog item 11 so the retirement is not silently lost.

---

## 9. Recommendations

### Urgent, NOW (would change behaviour → needs explicit operator approval)

**None.** I recommend **no production change and no window restart** (operator-confirmed). Nothing
found is a capital risk: no crash, no uncontrolled loss, no double-close, no double-book, no
exposed secret. Every defect worth fixing is either a measurement/record defect or a behaviour
change; fixing either mid-window would destroy the comparison the window exists to make.

The urgent items are **operator awareness, not action**: **`CLOSE_PAPER_POSITION: 78` is 7
positions, not 78**; **the mimic is provably dropping trades** (+13.61% DOT missed); and **the
window's loss is fee drag, not signal quality** (+1.000R gross vs −8.736R fees).

### Post-window backlog — items 6–11 in `reports/W_S_DEFECTS_BACKLOG.md`

| Backlog | Item | Priority |
|---|---|---|
| 6 | Ouroboross Invo session churn / missed mirrors — investigate root cause first (possible Invo session or rate limit) | CRITICAL |
| 7 | `CLOSE_PAPER_POSITION` state persistence — terminal state + `_is_due` exclusion; shared root cause with reservation drift | HIGH |
| 8 | Cross-sectional / per-direction exposure cap (e.g. max 3) | HIGH |
| 9 | Map the Invo `suppressed` audit event type | INFO |
| 10 | Worker attribution: `assessments[]` already persists `worker_id`/`worker_version`; the gaps are the `[Exits]` log line and an explicit adopted-worker field | HIGH |
| 11 | Deploy lag: `ema_trend_20_50` retirement (`a29831a`) never reached production; verify at the next authorized deploy | HIGH |

Also queued there but not yet numbered, and worth doing in the same pass:

1. **Log `strategy_id`/`strategy_version` on publish** — makes per-strategy attribution possible
   without file access. (`F601` duplicated keys are known and intentional.)
2. **Retire reservations on close** and assert `diff_reserved` returns to 0 after a settle cycle.
3. **Log skipped-because-too-small** distinctly from skipped-because-stale in the mimic.
4. **Fix the H2 total-R condition** before the close: "filtered total R ≥ 0.9 × unfiltered total
   R" is degenerate when the unfiltered total is negative. Recorded as a protocol defect, not
   changed mid-window (thresholds are frozen).

### The one thing to watch before 2026-11-21

**The fee drag, not the signal edge.** Gross expectancy is +0.020R/trade against a 0.178R cost
(§7.1). If the remaining 27 days look like the first 2.8, the window closes with the entry
kill-switch conditions met (CI lower ≤ 0, median ≤ −0.5R, fees > gross) and a 30-day sample
behind them. That is a *decision the operator will face*, not an action available now. The
question to resolve in the meantime is the §7.1 caveat: is `fees_cost_r` the real fee, or a
conservative model?

### Working well — do not break

- **Scan loop:** 248/248, max gap 16.6 min, 0 errors, 0 tracebacks, both services stable.
- **Ledger idempotency:** identical decisions deduped (`monitor.py:162`); ledger healthy on
  **1,315/1,315** decisions (`apply=True` every time); no `Ledger unreadable/write failed`.
- **Server-side dedupe:** `already-applied` ×10, `already gone server-side` ×5 — the W6
  `clientTxId` assumption is confirmed in production.
- **Counting integrity:** published 59 = ledger +59; booked 51 = finalized 51 (two independent
  counters, exact agreement).
- **Delivery:** 59/59 signals reached Telegram.
- **Resources:** memory flat on both services across 2,881 samples.
- **Security posture:** all secrets redacted, dashboard gated, no secret printed in this audit.

---

## 10. Open questions for operator

**Answered by the operator on 2026-10-24:** close-churn deferral (→ backlog 7, no mid-window
read), report disposition (→ committed with a `.gitignore` exception), window status (→ continue,
no restart).

Still open:

1. **Fee model (new, highest value):** is `fees_cost_r` the **real** exchange fee, or the
   harness's conservative model? It is the single input that decides whether the window's −7.736R
   is real or overstated (§7.1). One Invo/HL statement settles it.
2. **Equity definition:** is `[Paper] equity 941.93` net of fees and slippage on a $1,000 start?
   The R×$10 cross-check (−$77.36 vs −$86.82) says *yes, net* — worth a one-word confirmation.
3. **Burst entries (issue 5 / backlog 8):** confirm these stay an in-window observation for the
   11-21 review, with the cap decision taken after the window closes.
4. **Reservation drift (issue 2):** is the reserved bank supposed to be authoritative for sizing?
   If yes, −$60 of phantom reservations is a sizing input, and that changes its severity.
5. **`suppressed` events (issue 3 / backlog 9):** new Invo event type to be mapped, or internal
   state leaking into the audit reader?
6. **Mimic session concurrency (backlog 6):** does the main bot hold the *same* Invo account
   session the mimic uses? If yes, a one-session-per-account rule is the leading root cause and
   the first thing to test post-window — before any code change.
7. **Deploy lag (backlog 11):** confirm the `ema_trend_20_50` retirement ships at the next
   authorized deploy, so the window's remaining 27 days and any 04B read are not silently
   measuring a retired family.

---

## 11. What was checked, and what remains blocked

**Resolved in Revision 3** (files pulled read-only over `railway ssh`, gzip+base64, byte-exact,
JSON-validated; stored in the gitignored `prod_evidence/2026-10-24/`):

| File | Size | Used for |
|---|---|---|
| `data_store/exit_decisions.json` | 9,592,085 B | §5, §6, §7 (deduped to 61 signals) |
| `data_store/outcomes.json` | 324,959 B | §3.2, §4, §7, §7.1 (309 rows, 299 settled) |
| `data_store/paper_portfolio.json` | 219,873 B | equity cross-check |
| `data_store/invo_exit_sync.json`, `sim_reservations.json` | 34,940 / 71,624 B | drift context |
| `data_store/signals_2026-10-22/23/24.json` | 38,375 / 57,186 / 45,591 B | 59 published signals, per-strategy, Bollinger check |
| `mirror_data/mirror_events.jsonl` | 606,182 B | mimic events (capped at 5,000 lines) |
| `mirror_data/mirrored.json` | 8,716 B | mirrored-position record |

**Still blocked, and why:**

| Blocked item | Reason |
|---|---|
| Commit hash of deployed revisions | MCP exposes deploy/snapshot ids only. Deploy timestamps + `git merge-base` prove `a29831a` is not deployed |
| True decision totals before 2026-10-20 | `exit_decisions.json` is a 2,000-row ring buffer; older rows are gone |
| `mirror_events` before 2026-10-15 | that file is capped at 5,000 lines |
| Tick-level holding time (~3 min claim) | outcome ledger is 15-minute bar resolution; floor is exactly 15 min |
| `p3_shadow.jsonl` / A2 status | not present in the volume. Indirect: **0** `[P3 shadow]` lines in 248 scans → A2 not active |
| Secrets / flag *values* | redacted by the connector (`valuesRedacted: true`) — by design, not worked around |
| Real exchange fees | `fees_cost_r` is the harness model; needs an Invo/HL statement (§10 q1) |
| First-proposal-per-signal worker adoption | not computed in this pass; per-decision rates understate (§5) |

---

## 12. Bottom line

The window is alive, producing at the expected rate, with clean plumbing and three defects on the
mimic/measurement side. Its measured result is **−8.44% paper equity over 49 window-published
settled signals (−7.736R, mean −0.158R, CI95 [−0.545, +0.229])**, and the cause is now identified:
**fees of 8.736R against gross of 1.000R** — an 8.7× cost-to-edge ratio, not a signal-quality
failure. **89% of the net loss comes from `ema_trend_20_50`, a family already retired locally but
still live in production because nothing has been deployed since 10-13.**

Hypotheses: **H1** directionally supports the 65 gate (the 60–64 band is the entire loss) but is
**failure-by-sample at n=35**; **H2** is under-sampled and its total-R test is degenerate;
**H3** is **not testable** — zero opposed signals; **H4** unmeasurable, confirmed by absence.

**No action is required before 2026-11-21, and none is recommended.** The one thing worth watching
is the fee structure: on today's numbers the entry kill-switch conditions would all be met at
window close. Six backlog items are queued (6–11), and the report's remaining uncertainty is
concentrated in one question — is `fees_cost_r` the real fee?

---

### Verification

No code change was made. Tracked files touched by this revision: this report and the defects
backlog. Production evidence lives in the gitignored `prod_evidence/2026-10-24/`; the temporary
analysis scripts used to compute §3.2–§7.1 were deleted before commit. No deploy, no restart, no
env write, no production write.

Last five commits **before** this report's commit — the report cannot contain its own hash:

```
2bd1968 Add W-LIVE production audit and queue four window defects
fdb5c89 Label momentum +1.42R as User-provided in volatile state
ec7f97b Establish weekly reporting cadence and trigger alerts for observation window
3b7ec50 Promote momentum ATR inert-params to backlog item 5 (behavioral, not cleanup)
05fa27d Audit F601 and queue post-window lint cleanup
```
