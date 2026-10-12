"""Pariston mimic-watch bot: an independent detect-only service.

One job: watch Pariston's paper portfolio and notify the operator the
moment he shares a setup (or closes one), with full setup detail
(symbol/side/leverage/entry/TP/SL) so the operator can tap the real
mimic button in an approved session.

Design notes:
- Detect-only by construction: no share/close/modify code exists in this
  package (package-level safety test enforces it). It cannot move money.
- Watches Pariston's portfolio directly (positions + full setup detail),
  both verified readable cross-account. No dependency on the main bot.
- Reuses src.invo.client.InvoClient with its own data dir for login only
  (token -> refresh -> OTP self-login), plus an isolated session + ledgers.
- brain.decide_size holds the confirm-time sizing knobs for the operator
  flow (flat base + bank/concurrency guards).
"""
