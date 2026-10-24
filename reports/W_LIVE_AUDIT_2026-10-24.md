# W-LIVE — Live Production Audit (2026-10-24)

Read-only audit of the Railway-hosted bot during the active observation window
(2026-10-22 → 2026-11-21). No code change, no deploy, no restart, no env write,
no `railway login`. Logs and MCP metadata only.

Labeling: **Measured** = read directly from Railway/MCP output or repo. **User-provided**
= operator or prior-session figure carried in. **Estimated** = my inference, stated as such.
No figure in this report is a 30-day result. Window age at audit time: **2.8 days**.

**Revision 2 (2026-10-24):** operator Invo screenshots incorporated as §3.1; four defects queued
as backlog items 6–9 (`reports/W_S_DEFECTS_BACKLOG.md`); aggregation rule added to §7; report
committed with a `.gitignore` exception. Findings and window status unchanged — no production
action, no restart.

---

## 1. Executive summary

1. **The window is producing.** 248 scans, 192 candidates, **59 published signals**, 59/59
   delivered to Telegram, **51 outcomes booked**, zero errors in scan, supervision, or
   tracking paths. Nothing is stalled.
2. **No BLOCKER.** No crash loop, no restart, no OOM, no exposed secret, no uncontrolled loss,
   no traceback in either service across 2.8 days. No trigger fired; nothing forces a stop.
3. **Paper equity fell over the window: 1028.75 → 941.93, −86.82 (−8.44%)**, with an
   intra-window low of **803.30 (−21.9%)** on 10-23 14:05 recovering to −8.44% by 10-24 15:33.
   *Measured, self-reported `[Paper]` log line. This is the only P&L read obtainable.*
4. **One genuine defect found: `CLOSE_PAPER_POSITION` does not terminate supervision.**
   7 distinct positions were told to close **78 times** (one 26×, one 43×), state stayed `open`
   up to 33h, and supervision kept re-deciding after the close. Closes are deduped server-side,
   so this is a **measurement/observability defect, not a capital defect**.
5. **Worker- and strategy-level attribution is unobservable in production.** Neither worker
   adoption, RR_DEAD, per-strategy signal counts, nor H2/H3 aligned-vs-opposed can be derived
   from logs or files. Recorded here as blocked, not as "fine".

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
3. **The `CLOSE_PAPER_POSITION` loop means exit analysis must group by unique `signal_id`.**
   78 close decisions = **7 positions**. Now a binding rule in the backlog (item 7).

**Capital status, per operator directive:** the close loop and the session churn are
**capital-neutral in paper** — no principal at risk, closes deduped server-side — but **on the
mimic side they lose real money**, because a missed mirror is a missed +13.61% trade. None of that
justifies a mid-window fix; it is why backlog item 6 sits first in the queue.

### Triggers — none fired

- **Bullish regime week?** No. Regime counts in-window: BEARISH 174, NEUTRAL 63, BULLISH 11 (Measured).
- **5 consecutive R > 2.0?** Not evaluable — per-trade R is not readable (no file access),
  and the published series is 59 signals not 5 closed winners. No reason to believe it fired.
- **CI lower bound ≤ 0?** Not computable — requires the R sample; blocked (§11).

### Hypotheses H1–H4

- **H1 (gate 65 vs 60):** *not evaluable at n=59/2.8 d.* One observable: the
  `Live gate: N unvalidated coins excluded` line fired **0 times in 248 scans** → no coin was
  excluded by the validation gate in-window. That is about *exclusions*, not about the 65-vs-60
  threshold, and does not settle H1. *Measured, scope-limited.*
- **H2 (regime filter):** *cannot be evaluated.* Per-signal side is not logged, so aligned vs
  opposed counts are not derivable. Only proxy: the mimic's TPSL placements skew 84 SHORT /
  5 LONG — consistent with signals following a bearish regime, which is *alignment by
  construction*, not evidence of filter value. *Estimated proxy.*
- **H3 (side-opposed):** *cannot be evaluated* for the same reason; neither count nor mean exists.
- **H4 (regime-only scoring):** remains acknowledged degradation; production persists no
  `scoring_breakdown`, so nothing changed and nothing is measurable. *Confirmed by absence.*

---

## 4. Strategy quality analysis

**What I could not check, and why (stated plainly):** per-strategy signal counts are **not
logged**. `pipeline.py` logs only aggregate `Scan complete: N candidates, M rejected` and
`Publishing N signals`; strategy identity appears only in error lines, of which there were
**zero**. Without `signals.jsonl` (file access blocked, §11) the four-family breakdown
(bollinger / volume_breakout / donchian / momentum) **cannot be produced**. Any per-strategy
claim in this window would be fabricated.

**What is measurable:**

- Zero strategy exceptions: `Error in strategy …` and `Error processing …` → **0 occurrences
  in 248 scans.** All four families ran without raising. *Measured.*
- Zero zero-signal failure mode at the pipeline level: the pipeline never aborted, never
  published a partial scan, and `rejected` never exceeded the eligible symbol set behaved
  anomalously (`rejected` = symbols yielding no candidate: 186 scans rejected 14/14,
  consistent with `N+M ≈ 14`). Burst scans reject 0. No scan showed a nonsensical count.
- Publication ratio 30.7% of candidates → dedupe/gating is doing real work, not passing
  everything through.
- Baseline comparison is **not possible** on R: the pre-window family figures
  (bollinger +19.06R / 51 signals / 4 coins; volume_breakout +11.62R / 42 / 5;
  donchian +6.38R / 68 / 8; momentum +1.42R / 21 / 7 — **User-provided**) cannot be extended
  without the outcome file. The only comparable measured series is aggregate paper equity,
  which is **negative in-window** (§3). That divergence is itself the finding:
  **R and equity are not telling the same story, and only equity is measured right now.**

**Assessment:** no strategy-level anomaly is *detectable*, which is not the same as *absent*.
The observability gap is the finding, and it should be closed post-window (§9).

---

## 5. Worker quality analysis

**Blocked, and it is a design gap, not a tooling accident.** The exit path logs only the
final action (`monitor.py:170`: `[Exits] {sid} {state} -> {action} (plan vN, …)`). It does
**not** log which worker(s) proposed the action, their assessments, or their abstentions.
Therefore:

- **Adoption rate per worker: not computable.**
- **RiskReward `RR_DEAD` 0/8 adoptable: cannot be re-tested.** The pre-window finding stands
  unverified in this window.
- **Silently-failing worker detection: not possible.** A worker that abstains on every
  position is indistinguishable in logs from a worker that never runs.

**What the action mix implies** (*Estimated* — inference from action semantics, not logged
attribution):

| Action | Decisions | Distinct signals | Likely worker |
|---|---|---|---|
| TIGHTEN_STOP | 49 | 25 | Stop |
| ADJUST_TAKE_PROFITS | 15 | 14 | Target |
| REDUCE_PAPER_POSITION | 7 | 7 | Risk/Structure |
| INVALIDATE_PENDING_SETUP | 11 | 11 | Structure / CounterSignal |
| FLAG_CONFLICT | 6 | 6 | CounterSignal |

Read-through: **at most 53 of 61 supervised signals ever received a non-NO_CHANGE
proposal** (and each of those signals can receive several). No worker dominates; no worker is
provably dead. `NO_CHANGE` at 87.6% of decisions means the orchestrator is mostly holding —
consistent with positions that are open and quiet, not with an abstaining worker.

**This is the highest-value instrumentation gap in the system** (§9): without worker
attribution, every future exit-quality debate is unfalsifiable.

---

## 6. Orchestrator quality analysis

**Supervisor volume** (248 runs, 66,783 skipped, **0 errors**): *Measured.*

| Action | Count | % | Distinct signals |
|---|---|---|---|
| NO_CHANGE | 1143 | 87.6% | 50 |
| CLOSE_PAPER_POSITION | 78 | 6.0% | **7** |
| TIGHTEN_STOP | 48 | 3.7% | 25 |
| ADJUST_TAKE_PROFITS | 15 | 1.1% | 14 |
| INVALIDATE_PENDING_SETUP | 11 | 0.8% | 11 |
| REDUCE_PAPER_POSITION | 7 | 0.5% | 7 |
| FLAG_CONFLICT | 3 | 0.2% | 6* |

\* recount from log lines gives 6 for FLAG_CONFLICT; the scheduler dict says 3. Reason for the
delta is not established (paging boundary vs. per-run dict). The dict is authoritative for
rates; the per-line recount is authoritative for distinct signals. *Measured, minor discrepancy.*

**Critical read-through correction.** The scheduler dict is **decisions, not positions**.
78 closes = **7 positions**. 48 tightens = 25 positions. Anyone reading
`{'CLOSE_PAPER_POSITION': 78}` as "78 positions closed" is wrong by **11×**. This is the
single easiest way to misreport this window.

**Idempotency is working.** `monitor.py:162` skips re-recording an identical decision key, which
is why 1,143 NO_CHANGE decisions produced far fewer log lines than evaluations. Two independent
tallies (scheduler dict 1,305; log-line recount 1,314) agree within 0.7% — the gap is the
paging boundary, not a leak. *Measured.*

**Decision density changed.** In-window: 1,305 evaluations / 61 signals / 2.8 d ≈ **7.7
evaluations per signal-day**. Pre-window snapshot: 2,000 decisions / 97 signals ≈ **1.9 per
signal-day** (over the pre-window span; **User-provided**). That is a **~4× increase in
per-signal re-decision rate**. Explanations that fit: more concurrent open positions, and
event-driven `_is_due` gating firing on more 15m candle events. It is *not* an error (0 errors)
but it is a real behavioural delta vs baseline and it is the mechanism that amplifies the
CLOSE-repeat defect in §8 item 1.

**RiskGuard:** not separately logged; invocation rate is **not verifiable** from logs. The
pre-window 124/124 cannot be re-tested. *Blocked.*

---

## 7. Exit management quality analysis

**The CORRECT / PREMATURE / MISSED classification is not reproducible.** It needs per-decision
forward bar movement plus the outcome record for each signal; both live in
`data_store/*.json`, which is unreadable (§11). I am not going to assign labels I cannot test.

**Aggregate evidence that is measurable:**

- 51 booked outcomes in 2.8 d is a **high turnover** rate (≈18/day) against 61 supervised
  signals — positions are being resolved, not left dangling.
- Equity path: 1028.75 → 803.30 (10-23 14:05) → 941.93 (10-24 15:33). A −21.9% drawdown
  then a +17.3% recovery off the low within ~25 h. **Operator evidence resolves the drawdown
  half:** losing trades were held ~3 minutes, so the drop is immediate stop-outs on entries, not
  exits cutting early. The **recovery** half is still unexplained by anything I can read — that
  needs the outcome file, not a guess.
- No `[Exits] Ledger unreadable / write failed / Ledger unhealthy` lines → the ledger was
  healthy for all 1,314 decisions (`apply=True` on every one). Exit application never fell
  back to record-only. *Measured — and a genuinely good result.*

**The one exit-quality defect I can prove** (§8 item 1) contaminates any per-signal
classification for the 7 closed positions: close does not terminate supervision, so their
decision sequences contain post-close re-decisions that no bar-level method would classify
sensibly.

**Aggregation rule (binding from this audit on).** Exit decisions **must be grouped by unique
`signal_id`**, never counted per decision. 1,314 decisions sit on 61 signals; closes are
overstated **11×** (78 decisions → 7 positions). Any Phase 5 classification run later against
`exit_decisions.json` must dedupe to `signal_id` first, or it will report a 78-position sample
that does not exist.

**Direction of the loss, per operator confirmation (§3.1).** Losing trades were held ~3 minutes
(DOGE, AVAX, NEAR, BNB, SOL). That makes the −8.44% an **entry-side** outcome — stops hit almost
immediately — rather than an exit-quality failure, and it is why this section does not recommend
any exit tuning.

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

### 8. INFO — window P&L is negative

- 1028.75 → 941.93 (−8.44%). Lifetime equity 941.93 across 299 bookings ⇒ the system is **−5.8%
  since inception**, and the pre-window period was net **positive** (+2.9% over 249 bookings).
  One regime, 2.8 days: this says the current regime is losing, not that the strategies are
  broken.

---

## 9. Recommendations

### Urgent, NOW (would change behaviour → needs explicit operator approval)

**None.** I recommend **no production change and no window restart** (operator-confirmed). Nothing
found is a capital risk: no crash, no uncontrolled loss, no double-close, no double-book, no
exposed secret. Every defect worth fixing is either a measurement/record defect or a behaviour
change; fixing either mid-window would destroy the comparison the window exists to make.

The one urgent item is operator awareness: **`CLOSE_PAPER_POSITION: 78` is 7 positions, not 78**,
and **the mimic is provably dropping trades** (+13.61% DOT missed).

### Post-window backlog — queued as items 6–9 in `reports/W_S_DEFECTS_BACKLOG.md`

| Backlog | Item | Priority |
|---|---|---|
| 6 | Ouroboross Invo session churn / missed mirrors — investigate root cause first (possible Invo session or rate limit) | CRITICAL |
| 7 | `CLOSE_PAPER_POSITION` state persistence — terminal state + `_is_due` exclusion; shared root cause with reservation drift | HIGH |
| 8 | Cross-sectional / per-direction exposure cap (e.g. max 3) | HIGH |
| 9 | Map the Invo `suppressed` audit event type | INFO |

Also queued there but not yet numbered, and worth doing in the same pass:

1. **Log worker identity + assessment per exit decision** — without it worker adoption, RR_DEAD
   and silent-worker detection stay untestable forever. Highest-value observability fix.
2. **Log `strategy_id`/`strategy_version` on publish** — makes per-strategy attribution possible
   without file access. (`F601` duplicated keys are known and intentional.)
3. **Retire reservations on close** and assert `diff_reserved` returns to 0 after a settle cycle.
4. **Log skipped-because-too-small** distinctly from skipped-because-stale in the mimic.

### Working well — do not break

- **Scan loop:** 248/248, max gap 16.6 min, 0 errors, 0 tracebacks, both services stable.
- **Ledger idempotency:** identical decisions deduped (`monitor.py:162`); ledger healthy on
  **1,314/1,314** decisions (`apply=True` every time); no `Ledger unreadable/write failed`.
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

1. **Equity definition:** is `[Paper] equity 941.93` net of fees and slippage on a $1,000 start?
   If yes, the window's headline result is **−8.44% measured**, and R-based hypotheses must be
   read against it, not instead of it.
2. **Burst entries (issue 5 / backlog 8):** confirm these stay an in-window observation for the
   11-21 review, with the cap decision taken after the window closes.
3. **Reservation drift (issue 2):** is the reserved bank supposed to be authoritative for sizing?
   If yes, −$60 of phantom reservations is a sizing input, and that changes its severity.
4. **`suppressed` events (issue 3 / backlog 9):** new Invo event type to be mapped, or internal
   state leaking into the audit reader?
5. **Mimic session concurrency (backlog 6):** does the main bot hold the *same* Invo account
   session the mimic uses? If yes, a one-session-per-account rule is the leading root cause and
   the first thing to test post-window — before any code change.

---

## 11. Could not check — and exactly why

| Blocked item | Reason |
|---|---|
| `data_store/*.json` (exit_decisions, outcomes, signals, paper_portfolio, invo_exit_sync) | No shell into the service; volume not downloadable via MCP; dashboard 401. **Operator to run `railway login`, then these are pullable** |
| `mirror_data/*` (mirrored, mirror_events, pending_records) | Same |
| `p3_shadow.jsonl` / A2 status | Same. Indirect: **0** `[P3 shadow]` lines in 248 scans → A2 not active (consistent with "not deployed") |
| Commit hash of deployed revisions | MCP exposes deploy/snapshot ids only, not commits. Deploy timestamps prove nothing shipped since 10-13/10-14 |
| Mean/median/top-5 net R; CI lower bound | R series lives in the unreadable outcome file |
| H2 aligned/opposed, H3 side-opposed | Per-signal side not logged |
| Per-strategy signal counts, stop-out rate | Strategy id not logged on publish; only aggregate counts exist |
| Worker adoption, RR_DEAD, silent-worker detection | Worker identity not logged |
| RiskGuard invocation rate | Not separately logged |
| CORRECT / PREMATURE / MISSED exit classification | Requires per-decision forward bars + outcome record; unreadable |
| Secrets / flag *values* | Redacted by connector (`valuesRedacted: true`) — by design, not worked around |

**Unblock path (agreed, operator action required):**

```
railway login     # operator runs this in their own shell — not attempted by the agent
```

Then pull, read-only, into the local tree:

- `data_store/exit_decisions.json`
- `data_store/outcomes.json`
- `mirror_data/mirror_events.jsonl`

Those three files unblock §3 (R distribution), §4 (per-strategy counts and stop-out rate),
§5 (worker attribution is still blocked — that is a logging gap, not a file gap), §7 (CORRECT /
PREMATURE / MISSED) and the full R/CI computation for H1–H3. When they land, Phase 4 and Phase 5
are completed against them, **grouping exit decisions by `signal_id`** (§7 rule).

---

## 12. Bottom line

The window is alive and producing at the expected rate with clean plumbing, one real measurement
defect, and one real money defect on the mimic side. Its measured result so far is **−8.44% paper
equity over 51 bookings**, driven by ~3-minute stop-outs rather than exit management, in one
regime — not enough to judge any hypothesis and not claimed to be. **No action is required before
2026-11-21.** Four defects are queued, and §11 is the reason this audit should be re-run with
file access before the window closes.

---

### Verification

No code change was made. The only tracked files touched are this report, the defects backlog, and
one `.gitignore` exception so the report survives a clean checkout. Working tree clean at commit.

Last five commits **before** this report's commit — the report cannot contain its own hash:

```
fdb5c89 Label momentum +1.42R as User-provided in volatile state
ec7f97b Establish weekly reporting cadence and trigger alerts for observation window
3b7ec50 Promote momentum ATR inert-params to backlog item 5 (behavioral, not cleanup)
05fa27d Audit F601 and queue post-window lint cleanup
894d833 Add mandatory commit-verification rule to AGENTS.md
```
