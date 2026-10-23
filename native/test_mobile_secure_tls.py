"""Synthetic HTTPS + P-256 private key pairing/replay/revoke tests."""
import base64
from contextlib import closing
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from archive_package_adapter import make_archive_package_handler
from create_mobile_live_demo import create
from live_archive_adapter import VerifiedLiveArchive
from mobile_secure_pairing import (
    SecurePairingGate, identity_from_der,
    signed_pairing_message, signed_request_message
)
from mobile_tls import ephemeral_server_context


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class SecureTlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = create(str(Path(self.tmp.name) / "synthetic"), include_zip=True)
        package = VerifiedLiveArchive(str(root))
        self.gate = SecurePairingGate()
        self.ssl_env = ephemeral_server_context()
        context, self.cert_fingerprint = self.ssl_env.__enter__()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_archive_package_handler(package, "legacy-not-allowed", self.gate)
        )
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "https://127.0.0.1:" + str(self.server.server_port)
        self.testing_tls_context = ssl._create_unverified_context()
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.der = self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.device = identity_from_der(self.der)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.ssl_env.__exit__(None, None, None)
        self.tmp.cleanup()

    def request(self, path, body=None, token=None, sign=True, method=None,
                device=None, nonce=None, stamp=None, override_signature=None):
        method = method or ("POST" if body is not None else "GET")
        headers = {}
        device = self.device if device is None else device
        if token is not None:
            headers["Authorization"] = "Bearer " + token
            headers["X-Device-Id"] = device
            stamp = str(int(time.time())) if stamp is None else stamp
            nonce = secrets.token_urlsafe(16) if nonce is None else nonce
            headers["X-Device-Time"] = stamp
            headers["X-Device-Nonce"] = nonce
            if sign:
                proof = self.key.sign(
                    signed_request_message(method, path, stamp, nonce),
                    ec.ECDSA(hashes.SHA256())
                )
                headers["X-Device-Proof"] = (
                    override_signature if override_signature is not None else b64(proof)
                )
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode() if body is not None else (
                b"" if method == "POST" else None
            ),
            headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(
                request, context=self.testing_tls_context, timeout=6
            ) as result:
                return result.status, result.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def pair(self):
        proof = self.key.sign(
            signed_pairing_message(self.gate.code, self.device),
            ec.ECDSA(hashes.SHA256())
        )
        status, body = self.request("/v3/pair", {
            "code": self.gate.code, "deviceId": self.device,
            "publicKey": b64(self.der), "signature": b64(proof)
        })
        self.assertEqual(200, status, body)
        payload = json.loads(body)
        self.assertTrue(payload["tlsEnabled"])
        self.assertFalse(payload["realDocumentsAllowed"])
        return payload["sessionToken"]

    def test_tls_certificate_fingerprint_and_san(self):
        import socket
        with socket.create_connection(("127.0.0.1", self.server.server_port)) as raw:
            with self.testing_tls_context.wrap_socket(raw, server_hostname="10.0.2.2") as tls:
                cert_der = tls.getpeercert(binary_form=True)
        self.assertEqual(hashlib.sha256(cert_der).hexdigest().upper(),
                         self.cert_fingerprint)
        self.assertNotEqual("0" * 64, self.cert_fingerprint)
        # A normal CA trust store must reject a synthetic self-signed certificate.
        with self.assertRaises((ssl.SSLError, urllib.error.URLError, OSError)):
            urllib.request.urlopen(self.url + "/v2/status", timeout=4)

    def test_no_bearer_only_access_and_public_key_proof_each_request(self):
        self.assertEqual(401, self.request("/v2/status")[0])
        self.assertEqual(404, self.request("/v2/pair", {
            "code": self.gate.code, "deviceId": self.device
        })[0])
        token = self.pair()
        self.assertEqual(401, self.request("/v2/documents", token, sign=False)[0]
                         if False else self.request("/v2/documents", token=token, sign=False)[0])
        self.assertEqual(401, self.request("/v2/status", token=token,
                                          override_signature=b64(b"wrong"))[0])
        status, raw = self.request("/v2/documents?limit=20&offset=0", token=token)
        self.assertEqual(200, status, raw)
        files = json.loads(raw)["documents"]
        self.assertEqual(4, len(files))
        self.assertEqual(200, self.request(
            "/v2/document/" + files[-1]["sourceKey"], token=token
        )[0])

    def test_nonce_replay_and_revoke(self):
        token = self.pair()
        nonce = secrets.token_urlsafe(16)
        stamp = str(int(time.time()))
        self.assertEqual(200, self.request(
            "/v2/status", token=token, nonce=nonce, stamp=stamp
        )[0])
        self.assertEqual(401, self.request(
            "/v2/status", token=token, nonce=nonce, stamp=stamp
        )[0])
        self.assertEqual(200, self.request(
            "/v3/pair/revoke", method="POST", token=token
        )[0])
        self.assertEqual(401, self.request("/v2/status", token=token)[0])

    def test_device_identity_must_match_actual_p256_public_key(self):
        proof = self.key.sign(
            signed_pairing_message(self.gate.code, "a" * 32),
            ec.ECDSA(hashes.SHA256())
        )
        status, _ = self.request("/v3/pair", {
            "code": self.gate.code, "deviceId": "a" * 32,
            "publicKey": b64(self.der), "signature": b64(proof)
        })
        self.assertEqual(401, status)
        token = self.pair()
        self.assertEqual(401, self.request(
            "/v2/status", token=token, device="a" * 32
        )[0])


if __name__ == "__main__":
    unittest.main()
