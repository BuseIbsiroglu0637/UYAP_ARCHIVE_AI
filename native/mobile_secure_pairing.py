"""TLS-only ephemeral, proof-of-possession device pairing for synthetic emulator.

Authenticates an Android Keystore-backed EC P-256 public key with ECDSA at
pairing and on every authenticated request. Tokens are in memory and revocable.
No real documents, no direct UYAP, no persistent device registry.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from mobile_pairing import CODE_CHARS, PAIRING_TTL, SESSION_TTL, MAX_ATTEMPTS

DEVICE = re.compile(r"[a-f0-9]{32}\Z")
NONCE = re.compile(r"[A-Za-z0-9_-]{16,32}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
SIG = re.compile(r"[A-Za-z0-9_-]{64,200}\Z")


def decode_url_b64(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("bad encoding")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def identity_from_der(public_der: bytes) -> str:
    return hashlib.sha256(public_der).hexdigest()[:32]


def signed_pairing_message(code: str, device: str) -> bytes:
    return ("PAIR-V017\n" + code + "\n" + device).encode("ascii")


def signed_request_message(method: str, path: str, stamp: str, nonce: str) -> bytes:
    return ("ARCHIVE-V017\n" + method + "\n" + path + "\n" + stamp + "\n" + nonce).encode("utf-8")


class SecurePairingGate:
    secure_device_proof = True

    def __init__(self, monotonic=time.monotonic, wall_clock=time.time):
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self.code = "".join(secrets.choice(CODE_CHARS) for _ in range(12))
        self._code_expiry = monotonic() + PAIRING_TTL
        self._attempts = 0
        self._consumed = False
        self._device = None
        self._public_key = None
        self._token = None
        self._expiry = 0
        self._used_nonces = set()

    def pair_secure(self, code: str, device: str, public: str, signature: str):
        with self._lock:
            if (self._consumed or self._monotonic() >= self._code_expiry or
                    self._attempts >= MAX_ATTEMPTS or
                    not isinstance(code, str) or not isinstance(device, str) or
                    not DEVICE.fullmatch(device) or not isinstance(public, str) or
                    not isinstance(signature, str)):
                return None
            self._attempts += 1
            if not hmac.compare_digest(code, self.code):
                return None
            try:
                public_der = decode_url_b64(public)
                signed = decode_url_b64(signature)
                if not 65 <= len(public_der) <= 300:
                    return None
                key = serialization.load_der_public_key(public_der)
                if (not isinstance(key, ec.EllipticCurvePublicKey) or
                        not isinstance(key.curve, ec.SECP256R1) or
                        not hmac.compare_digest(identity_from_der(public_der), device)):
                    return None
                key.verify(signed, signed_pairing_message(code, device),
                           ec.ECDSA(hashes.SHA256()))
            except (ValueError, InvalidSignature, TypeError):
                return None
            self._consumed = True
            self._device = device
            self._public_key = key
            self._token = secrets.token_urlsafe(32)
            self._expiry = self._monotonic() + SESSION_TTL
            return self._token

    def authorize_request(self, method: str, path: str, authorization: str,
                          device: str, stamp: str, nonce: str, signature: str) -> bool:
        with self._lock:
            if (self._token is None or self._monotonic() >= self._expiry or
                    not isinstance(device, str) or not DEVICE.fullmatch(device) or
                    not isinstance(authorization, str) or not isinstance(stamp, str) or
                    not stamp.isdecimal() or len(stamp) > 12 or
                    abs(self._wall_clock() - int(stamp)) > 120 or
                    not isinstance(nonce, str) or not NONCE.fullmatch(nonce) or
                    nonce in self._used_nonces or
                    not isinstance(signature, str) or not SIG.fullmatch(signature) or
                    not hmac.compare_digest(authorization, "Bearer " + self._token) or
                    not hmac.compare_digest(device, self._device)):
                return False
            try:
                self._public_key.verify(
                    decode_url_b64(signature),
                    signed_request_message(method, path, stamp, nonce),
                    ec.ECDSA(hashes.SHA256())
                )
            except (ValueError, TypeError, InvalidSignature):
                return False
            if len(self._used_nonces) >= 2048:
                # Fail closed. A new process/one-time code is required.
                return False
            self._used_nonces.add(nonce)
            return True

    def revoke_request(self, path: str, auth: str, device: str,
                       stamp: str, nonce: str, signature: str) -> bool:
        if not self.authorize_request("POST", path, auth, device,
                                      stamp, nonce, signature):
            return False
        with self._lock:
            self._token = None
            self._device = None
            self._public_key = None
            self._used_nonces.clear()
            return True
