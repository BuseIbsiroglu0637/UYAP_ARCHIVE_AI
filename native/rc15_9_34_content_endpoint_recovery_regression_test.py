#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
POPUP = (ROOT / "popup" / "popup.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(condition, message):
    if not condition:
        raise AssertionError(message)


req("2.9.0 RC15.9." in MANIFEST.get("version_name", ""), "release identity")
req('const HTML_CIRCUIT_MAX_SOFT_PROBES = 3;' in DM, "bounded probe limit missing")
req('Object.freeze([15_000, 30_000, 60_000])' in DM, "safe cooldown schedule missing")
req('UYAP_INVALID_REQUEST_TAIL_HEX = "496e76616c69642072657175657374"' in DM,
    "Invalid request PDF tail signature missing")
req('const effectiveErrorCode = uyapInvalidRequestTail ? "HTML_RESPONSE"' in DM,
    "hybrid PDF endpoint response is not routed through endpoint recovery")
req('pauseReason: "CONTENT_ENDPOINT_REFRESH_REQUIRED"' in DM,
    "probe exhaustion does not stop the queue")
req('status: SESSION_STATUS.PAUSED' in DM and 'await clearSessionRuntimeAlarms(sessionId);' in DM,
    "safe pause must clear active queue alarms")
req('endpointRecoveryMigratedAt:now' in DM and 'originalValidationErrorCode:"PDF_TRAILING_DATA"' in DM,
    "existing Invalid request quarantines are not migrated safely")
req('wasEndpointRefreshResume' in DM and 'htmlCircuitState:"half_open"' in DM,
    "manual refresh resume is not controlled by half-open gate")
req('pause==="CONTENT_ENDPOINT_REFRESH_REQUIRED"' in POPUP and 'Yeniledim, güvenli devam et' in POPUP,
    "popup does not expose the recovery action")

print("RC15.9.34 content endpoint recovery regression: PASS")
