"""Decrypt the browser-exported Invo refresh JWT. Local-only scratch tool.

The app stores FlutterSecureStorage.REFRESH_TOKEN AES-GCM encrypted in
Local Storage; the base64 AES-GCM key lives in the FlutterSecureStorage
entry. Paste both values below (or pass via env to avoid shell history),
run, print the JWT, verify it decodes. Nothing is written anywhere.
Usage:
  set REFRESH_ENC=<value of FlutterSecureStorage.REFRESH_TOKEN>
  set STORAGE_KEY=<value of FlutterSecureStorage>
  python scripts/decrypt_refresh.py
"""
import base64
import json
import os


def _b64(s: str) -> bytes:
    return base64.b64decode(s + "=" * (-len(s) % 4))


def decrypt(enc_value: str, key_b64: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _b64(key_b64.strip())
    blob = _b64(enc_value.strip())
    # FlutterSecureStorage/web layout: 12-byte nonce prefix, then ct+tag.
    # Fall back to whole-blob-with-external-nonce variants if needed.
    if len(blob) > 12:
        try:
            return AESGCM(key).decrypt(blob[:12], blob[12:], None).decode()
        except Exception:
            pass
    raise ValueError("decrypt failed: unexpected layout (paste exact values)")


def main() -> None:
    enc_value = os.getenv("REFRESH_ENC", "")
    key_b64 = os.getenv("STORAGE_KEY", "")
    if not enc_value or not key_b64:
        raise SystemExit("set REFRESH_ENC + STORAGE_KEY first (see docstring)")
    jwt = decrypt(enc_value, key_b64)
    seg = jwt.split(".")[1]
    payload = json.loads(_b64(seg).decode())
    print("JWT OK. payload keys:", sorted(payload.keys()))
    print("user_id:", payload.get("user_id"))
    print("trading_account:", "PRESENT" if payload.get("trading_account") else "ABSENT")
    print("expires_in_days:", round(((payload.get("expires") or 0) - __import__("time").time()) / 86400, 1))
    print("JWT:", jwt)


if __name__ == "__main__":
    main()
