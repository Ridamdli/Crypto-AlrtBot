# AGENTS.md

Instructions for coding agents in this repository. `BRAIN.md` covers graph
navigation; this file covers the rules that must not be broken.

## 1. Test baseline

- **620 collected / 0 failed / 0 skipped** (31 files, 614 `def test_*` + 6
  parametrized expansions).
- Derivation: 605 at `72bb525` + 2 (W9 `f62d9a1`, W10 `95db541`) + 8 (Phase 04A
  harness) + 5 (W-DD Option A, `tests/test_scoring_breakdown.py`) = 620.
- W-DD Option A: `SignalModel.scoring_breakdown` (`dict | None`) persists the
  per-component score points. `None` means the row predates the contract — audits
  reconstruct and label that `Estimated`, never zero. Outcomes join by `signal_id`.
- Use `python -m pytest`. The `pytest` console script on PATH is Python 3.11 and
  fails collection.
- Any deviation means code changed. Investigate; do not update the baseline to
  match a failing tree.

## 2. Evidence discipline

- Statistical claims carry their sample: **11 days / 204 entry signals / one
  regime (bearish 157, neutral 47) / UNIUSDT-dominated exit population**. No
  claim is a 30-day result.
- **R-weighted or nothing** for policy. A count-only comparison cannot justify a
  policy change.
- Label every number **Measured / User-provided / Estimated**. Do not present a
  rate as a count.
- **03-B-2 is CLOSED.** Exit management is not the production defect. Do not
  reopen exit tuning; route exit work to the P2/P3 instrumentation already in
  place.

## 3. Secrets

- Never wildcard-grep secret-adjacent prefixes (`INVO_`, `MIRROR_`, `GMAIL_`,
  `APPS_`, `SECRET_`, `KEY_`, `TOKEN_`, `PASS_`, `AUTH_`).
- Anchored allowlist only:
  `printenv | grep -E "^(EXITS_APPLY|MIMIC_LIVE|EXITS_STRICT_MODE|INVO_PAPER_ENABLED|INVO_PAPER_DRY_RUN)$"`.
- If a command could emit a secret, do not run it. If one does, stop, do not
  echo, do not retry; report that it happened and what was affected.
- Password rotation was **declined (informed)** by the operator and is recorded
  as "operator-accepted risk, 2026-10-19". Do not raise it again unless the risk
  profile changes.

## 4. Deploy discipline

- **Local commits only.** No deploys, no production writes, no Railway reads
  while auth is expired.
- **Nothing deploys without explicit operator authorization.**
- The mirror service (`pariston-mimic`) is a separate deploy unit; it is uploaded
  from the local tree with `railway up` — GitHub auto-deploy does not fire.
- Mirror bot: no modifications and no runs without authorization.
- Deploy plan lives in `reports/DEPLOY_READINESS.md` (Wave 1 / Wave 2 / Hold).

## 5. Standing operator decisions

- No single-strategy coin backfill. Phase 04 redesigns coverage instead.
- `ema_trend_20_50` and `rsi_mean_reversion` stay retired.
- Entry kill-switch: halt new entries if the $16 cap is hit, or a 30-day sample
  has CI lower ≤ 0 **and** median ≤ −0.5R, or fees exceed gross in a rolling
  7-day window.
- BTC coverage watch: fewer than 3 signals in a 30-day scan forces an explicit
  Phase 04 BTC decision.

## 6. Working style

- Reports and production evidence are gitignored (`reports/*.md`,
  `prod_evidence/`), except the files explicitly whitelisted in `.gitignore`.
- Prefer the smallest complete change; finish callers, tests, and fixtures it
  touches.
- New non-trivial logic leaves one small test or an assert-based self-check.
- A known limitation gets a `shortcut:` comment with the upgrade trigger.
