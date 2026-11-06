"""Password login with stateless signed session tokens (HMAC-SHA256, 12 h lifetime).

Token = base64url(expiry) + "." + base64url(HMAC(secret, expiry)). Stored in an HttpOnly,
SameSite=Strict cookie, so it is sent automatically with REST calls *and* with the WebSocket
handshake, and cannot be read by JavaScript.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

COOKIE = "rm_session"
LIFETIME_S = 12 * 3600


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Auth:
    def __init__(self, password: str | None, secret: str | None = None):
        self.password = password
        self._key = (secret or secrets.token_hex(32)).encode()

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def check_password(self, candidate: str) -> bool:
        return bool(self.password) and hmac.compare_digest(candidate.encode(), self.password.encode())

    def issue(self) -> str:
        exp = str(int(time.time()) + LIFETIME_S).encode()
        sig = hmac.new(self._key, exp, hashlib.sha256).digest()
        return f"{_b64(exp)}.{_b64(sig)}"

    def verify(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        if not token or "." not in token:
            return False
        try:
            exp_b64, sig_b64 = token.split(".", 1)
            exp = _unb64(exp_b64)
            good = hmac.new(self._key, exp, hashlib.sha256).digest()
            return hmac.compare_digest(good, _unb64(sig_b64)) and int(exp) > time.time()
        except Exception:
            return False
