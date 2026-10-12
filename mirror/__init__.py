"""Pariston mirror bot: a separate service that mimics the main bot.

One job: watch Pariston's paper portfolio and copy every setup to a
DIFFERENT Invo paper account immediately, with its own capital-management
brain (flat base size plus bank/concurrency guards).

Design notes:
- Watches Pariston's portfolio directly (positions + full setup detail),
  both verified readable cross-account. No dependency on the main bot.
- Reuses src.invo.client.InvoClient with its own data dir, so the mirror
  gets the same battle-tested auth (token -> refresh -> OTP self-login),
  share, and close paths — but a fully isolated session + ledgers.
- Paper-only. dry_run defaults to true: the mirror must be started in
  dry-run first and only flipped live by explicit env change.
- V1 scope: copy new setups (entry + TP/SL) + close what Pariston closes.
  Live TP/SL drift-following is phase 2.
"""
