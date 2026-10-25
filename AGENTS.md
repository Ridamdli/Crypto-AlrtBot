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
- **PG mode is a second, different baseline.** `PG_TEST=1` (with
  `TEST_DATABASE_URL` exported) runs the same suite against Postgres:
  **593 passed / 27 skipped / 0 failed**. The 27 skips are `_JSON_ONLY`
  (`tests/conftest.py:115`) — they assert on JSON artifacts Postgres never
  writes. So 620/0/0 is the JSON baseline only; a PG run reporting 620 passed
  is a red flag, not a win.
- Nothing loads `.env` for a test run (only `src/main.py` calls `load_dotenv()`),
  so `PG_TEST=1` fails all 593 tests unless `TEST_DATABASE_URL` is exported
  first. A wrong value would `TRUNCATE` production — verify the endpoint differs
  from `DATABASE_URL` before running.
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

- **Local commits only.** No deploys, no production writes.
- **Nothing deploys without explicit operator authorization.**
- **Railway is abandoned** (account limits). The deploy target is a container
  PaaS (Koyeb / Render / Fly.io) built from the repo `Dockerfile`; the
  walkthrough, env var list and verification steps are in
  `docs/DEPLOY_KOYEB.md`.
- **Postgres (Neon) is the deployment target; JSON files are the local/test
  fallback.** `DATABASE_URL` selects it at runtime (`src/db/engine.py`); absent,
  every store falls back to `data_store/*.json`. Schema changes go through
  Alembic (`alembic upgrade head`), never ad-hoc DDL.
- **One image, two deploy units**, selected by `SERVICE_TYPE`: `main` (dashboard
  + scheduler, HTTP on `$PORT`) and `mirror` (no listener). The mirror is a
  separate service from the same image; first deploy must be
  `MIRROR_MODE=watch`, `MIMIC_LIVE=false`.
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

## 7. Commit verification (mandatory)

- After every work report that claims commits were made, the report **must end
  with verbatim `git log --oneline -5` output**. A report that asserts "committed"
  without that output has not verified anything.
- Run it after the last commit, not from memory. If the tree is not clean, say so
  in the same report — untracked files are the usual way claimed work turns out
  to be missing.
- Claimed commits that do not appear in that output are treated as work not done.
