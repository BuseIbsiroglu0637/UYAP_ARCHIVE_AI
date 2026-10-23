"""Synthetic-only acceptance for revocable localhost device pairing."""
from contextlib import closing
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import tempfile

from archive_package_adapter import make_archive_package_handler
from create_mobile_live_demo import create
from live_archive_adapter import VerifiedLiveArchive
from mobile_pairing import PairingGate, SESSION_TTL, PAIRING_TTL, MAX_ATTEMPTS


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class PairingGateTest(unittest.TestCase):
    DEVICE = "a" * 32

    def setUp(self):
        self.clock = FakeClock()
        self.gate = PairingGate(clock=self.clock)

    def test_pair_once_device_id_required(self):
        self.assertIsNone(self.gate.pair(self.gate.code, "short"))
        token = self.gate.pair(self.gate.code, self.DEVICE)
        self.assertIsNotNone(token)
        self.assertTrue(self.gate.authorize("Bearer " + token, self.DEVICE))
        self.assertFalse(self.gate.authorize("Bearer " + token, "b" * 32))
        self.assertIsNone(self.gate.pair(self.gate.code, "b" * 32))

    def test_code_expiry(self):
        self.clock.now += PAIRING_TTL
        self.assertIsNone(self.gate.pair(self.gate.code, self.DEVICE))

    def test_failed_attempt_lockout(self):
        for _ in range(MAX_ATTEMPTS):
            self.assertIsNone(self.gate.pair("WRONG", self.DEVICE))
        self.assertIsNone(self.gate.pair(self.gate.code, self.DEVICE))

    def test_session_expiry(self):
        token = self.gate.pair(self.gate.code, self.DEVICE)
        self.clock.now += SESSION_TTL
        self.assertFalse(self.gate.authorize("Bearer " + token, self.DEVICE))

    def test_revoke_invalidates_immediately_and_cannot_repair(self):
        token = self.gate.pair(self.gate.code, self.DEVICE)
        self.assertFalse(self.gate.revoke("Bearer garbage", self.DEVICE))
        self.assertTrue(self.gate.revoke("Bearer " + token, self.DEVICE))
        self.assertFalse(self.gate.authorize("Bearer " + token, self.DEVICE))
        self.assertIsNone(self.gate.pair(self.gate.code, self.DEVICE))


class PairingHttpTest(unittest.TestCase):
    DEVICE = "c" * 32

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = create(str(Path(self.folder.name) / "synthetic"), include_zip=True)
        package = VerifiedLiveArchive(str(root))
        self.gate = PairingGate()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_archive_package_handler(package, token="legacy-disabled",
                                         pairing_gate=self.gate)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.folder.cleanup()

    def request(self, path, body=None, token=None, device=None, method=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if device is not None:
            headers["X-Device-Id"] = device
        if body is not None:
            headers["Content-Type"] = "application/json"
        raw = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.url + path, data=raw, headers=headers,
            method=method or ("POST" if body is not None else "GET")
        )
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def test_pair_inventory_revoke_blocks_every_document(self):
        self.assertEqual(401, self.request("/v2/status")[0])
        self.assertEqual(401, self.request(
            "/v2/status", token="legacy-disabled", device=self.DEVICE
        )[0])
        status, data = self.request("/v2/pair", {
            "code": self.gate.code, "deviceId": self.DEVICE
        })
        self.assertEqual(200, status)
        info = json.loads(data)
        self.assertFalse(info["tlsEnabled"])
        self.assertFalse(info["realDocumentsAllowed"])
        token = info["sessionToken"]
        self.assertEqual(200, self.request("/v2/status", token=token,
                                            device=self.DEVICE)[0])
        status, body = self.request("/v2/documents", token=token, device=self.DEVICE)
        self.assertEqual(200, status)
        docs = json.loads(body)["documents"]
        self.assertEqual(4, len(docs))
        self.assertEqual(401, self.request(
            "/v2/document/" + docs[0]["sourceKey"], token=token,
            device="b" * 32
        )[0])
        self.assertEqual(200, self.request(
            "/v2/document/" + docs[0]["sourceKey"], token=token,
            device=self.DEVICE
        )[0])
        self.assertEqual(200, self.request(
            "/v2/pair/revoke", token=token, device=self.DEVICE, method="POST"
        )[0])
        self.assertEqual(401, self.request(
            "/v2/documents", token=token, device=self.DEVICE
        )[0])
        self.assertEqual(401, self.request("/v2/pair", {
            "code": self.gate.code, "deviceId": "d" * 32
        })[0])

    def test_invalid_pair_requests_fail_closed(self):
        self.assertEqual(400, self.request("/v2/pair", {
            "code": self.gate.code
        })[0])
        self.assertEqual(401, self.request("/v2/pair", {
            "code": "INVALID", "deviceId": self.DEVICE
        })[0])
        self.assertEqual(404, self.request("/v2/other", {
            "code": self.gate.code, "deviceId": self.DEVICE
        })[0])


if __name__ == "__main__":
    unittest.main()
