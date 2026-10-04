# Agent Documentation

## Overview
This repository implements **Crypto-AlrtBot**, a deterministic daily crypto futures signal generator that delivers high‑quality LONG/SHORT setups via Telegram.

## Purpose of the Agent
- **Scope:** Signal generation only – no automatic order execution.
- **Key Principles:** Deterministic, explainable, risk‑first, quality‑over‑quantity.
- **Primary Users:** Individual traders/researchers and quant developers.

## Project Structure
```
Crypto-AlrtBot/
├─ docs/                # Documentation (PRD, designs, etc.)
│   └─ PRD_compact.md   # Compact product requirements document
├─ src/                 # Source code for data collection, engines, and signal pipeline
│   ├─ data/            # Data layer modules (OHLCV, futures data)
│   ├─ engines/         # Market regime, volume, OI, funding, entry, SL, TP, risk engines
│   └─ main.py          # Entry point for the signal generation service
├─ AGENT.md             # **This file** – description of the development agent
├─ README.md            # Project overview for humans
└─ .gitignore           # Ignored files for Git
```

## Development Guidelines
- **Keep it simple:** Follow the *pony‑tail* philosophy – avoid over‑engineering.
- **Testing:** All engines should have deterministic unit tests.
- **Backtesting:** Provide scripts under `src/backtest/` to replay historical data.
- **Deployment:** Dockerfile and CI pipeline (GitHub Actions) are optional but encouraged.

## How to Contribute
1. Fork the repo.
2. Create a feature branch.
3. Implement a single, well‑scoped change.
4. Add or update tests.
5. Submit a Pull Request.

_This document is intended for the development team and any future contributors._
