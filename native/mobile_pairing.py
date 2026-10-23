"""Ephemeral single-device pairing for LOCAL Android DEBUG emulator acceptance.

This is a revocable authorization layer, NOT TLS or production phone security.
The server must still bind to 127.0.0.1; only synthetic test data is in scope.
Credentials are process-local, never written to disk or included in HTTP logs.
"""
from __future__ import annotations

import hmac
import re
import secrets
import threading
import time

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEVICE_ID = re.compile(r"[a-f0-9]{32}\Z")
TOKEN_LEN = 43
PAIRING_TTL = 600
SESSION_TTL = 1800
MAX_ATTEMPTS = 6


class PairingGate:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.code = "".join(secrets.choice(CODE_CHARS) for _ in range(12))
        self._code_expiry = clock() + PAIRING_TTL
        self._attempts = 0
        self._consumed = False
        self._device = None
        self._token = None
        self._session_expiry = 0.0

    def pair(self, code: str, device: str) -> str | None:
        with self._lock:
            if (self._consumed or self._clock() >= self._code_expiry or
                    self._attempts >= MAX_ATTEMPTS or not isinstance(code, str) or
                    not isinstance(device, str) or not DEVICE_ID.fullmatch(device)):
                return None
            self._attempts += 1
            if not hmac.compare_digest(code, self.code):
                return None
            self._consumed = True  # one pairing per server run
            self._device = device
            self._token = secrets.token_urlsafe(32)
            self._session_expiry = self._clock() + SESSION_TTL
            return self._token

    def authorize(self, authorization: str, device: str) -> bool:
        with self._lock:
            if (self._token is None or self._clock() >= self._session_expiry or
                    not isinstance(authorization, str) or
                    not isinstance(device, str) or not DEVICE_ID.fullmatch(device)):
                return False
            return (hmac.compare_digest(authorization, "Bearer " + self._token) and
                    hmac.compare_digest(device, self._device))

    def revoke(self, authorization: str, device: str) -> bool:
        with self._lock:
            if (self._token is None or self._clock() >= self._session_expiry or
                    not isinstance(authorization, str) or
                    not isinstance(device, str) or not DEVICE_ID.fullmatch(device)):
                return False
            if (not hmac.compare_digest(authorization, "Bearer " + self._token) or
                    not hmac.compare_digest(device, self._device)):
                return False
            self._token = None
            self._device = None
            self._session_expiry = 0.0
            return True
