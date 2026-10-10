"""
Gmail inbox reader for Invo OTP codes (email+OTP self-login automation).

Uses an App Password (16 letters) — revocable, mail-read scope in practice.
Secrets arrive via env only and are never logged or persisted by this module.
"""
import imaplib
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

OTP_PATTERNS = [
    re.compile(r"\b(\d{6})\b"),   # 6-digit codes first (most common)
    re.compile(r"\b(\d{4,8})\b"),
]
SENDER_HINTS = ("invo", "involio", "no-reply", "noreply")


class MailboxReader:
    def __init__(self, email: str, app_password: str,
                 host: str = "imap.gmail.com", port: int = 993):
        self.email = email
        self.app_password = app_password
        self.host = host
        self.port = port

    def _connect(self) -> imaplib.IMAP4_SSL:
        m = imaplib.IMAP4_SSL(self.host, self.port)
        m.login(self.email, self.app_password)
        return m

    def latest_otp(self, since_minutes: int = 10,
                   sender_hints: tuple = SENDER_HINTS) -> Optional[str]:
        """Newest plausible OTP code from recent inbox mail. Returns None if absent."""
        since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        try:
            m = self._connect()
        except imaplib.IMAP4.error as e:
            logger.warning(f"[Mailbox] IMAP login failed: {e}")
            return None
        try:
            m.select("INBOX")
            _, data = m.search(None, "ALL")
            ids = data[0].split()
            for mid in reversed(ids[-15:]):
                _, d = m.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT])")
                raw = b" ".join(p[1] for p in d if isinstance(p, tuple)).decode("utf-8", errors="ignore")
                blob = raw.lower()
                if not any(h in blob for h in sender_hints):
                    continue
                for pat in OTP_PATTERNS:
                    found = pat.findall(raw)
                    if found:
                        # skip dates/years lookalikes: prefer 6-digit, skip >8 handled by regex bounds
                        code = found[0]
                        if self._fresh_enough(m, mid, since):
                            return code
            return None
        finally:
            try:
                m.logout()
            except Exception:
                pass

    @staticmethod
    def _fresh_enough(m: imaplib.IMAP4_SSL, mid: bytes, since) -> bool:
        try:
            _, d = m.fetch(mid, "(INTERNALDATE)")
            mstr = d[0].decode() if isinstance(d[0], bytes) else str(d[0])
            dt = imaplib.Internaldate2tuple(mstr.encode() if isinstance(mstr, str) else mstr)
            if not dt:
                return True  # can't tell: err on the side of returning the code
            import calendar
            ts = calendar.timegm(dt)
            return ts >= since.timestamp()
        except Exception:
            return True
