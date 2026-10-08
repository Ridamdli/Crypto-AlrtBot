"""Invo (Involio) auto-share package.

Posts bot signals to the user's Invo portfolio via the app's own backend
(reverse-engineered from the public web bundle — no official API exists).

SAFETY DEFAULTS: disabled + dry-run. Enabling executes REAL Hyperliquid
positions with REAL money through the Invo backend. See README section.
"""
from src.invo.client import InvoClient, InvoAuthError, InvoApiError

__all__ = ["InvoClient", "InvoAuthError", "InvoApiError"]
