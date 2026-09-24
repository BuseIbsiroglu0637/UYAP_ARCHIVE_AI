#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import json
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "native" / "uyap_archive_agent.py"
DM = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
NATIVE_JS = (ROOT / "background" / "native-agent.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

spec = importlib.util.spec_from_file_location("uyap_agent_rc1591", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def require(cond, message):
    if not cond:
        raise AssertionError(message)


def segment(code, payload):
    length = len(payload) + 2
    return b"\xff" + bytes([code]) + length.to_bytes(2, "big") + payload


def sof0(height, width):
    # 8-bit, one component.
    payload = bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([1, 1, 0x11, 0])
    return segment(0xC0, payload)


def sos1():
    return segment(0xDA, bytes([1, 1, 0, 0, 63, 0]))


def standard_jpeg():
    return b"\xff\xd8" + sof0(2, 3) + sos1() + b"\x11\x22\x33" + b"\xff\xd9"


def dnl_jpeg():
    # Legal JPEG DNL form: SOF height starts at zero, DNL defines lines while
    # the entropy-coded scan is in progress.
    return (
        b"\xff\xd8"
        + sof0(0, 3)
        + sos1()
        + b"\x11\x22"
        + segment(0xDC, (2).to_bytes(2, "big"))
        + b"\x33\x44"
        + b"\xff\xd9"
    )


require(str(MANIFEST.get("version_name", "")).startswith("2.9.0 RC15.9."), "manifest RC15.9.x version_name")
require(agent.AGENT_VERSION == "2.7.64", "Agent 2.7.64 required")
require("jpeg_uyap_compatible_v1" in agent.dispatch({"action": "ping", "probe": "jpeg-test"})["capabilities"], "JPEG compat capability missing")
require('const JPEG_UYAP_COMPAT_CAPABILITY = "jpeg_uyap_compatible_v1";' in NATIVE_JS, "extension capability gate missing")
require('"INVALID_IMAGE",' in DM[DM.index('const LOCAL_REVALIDATION_CODES'):DM.index('async function tryLocalQuarantineRevalidation')], "INVALID_IMAGE not locally revalidatable")
problem_fn = DM[DM.index('export async function getProblemItems'):DM.index('const LOCAL_REVALIDATION_CODES')]
require('await quarantineLegacyDeterministicRetries(sessionId);' in problem_fn, "legacy deterministic retry normalization missing")

with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)

    standard = root / "standard.jpg"
    standard.write_bytes(standard_jpeg())
    profile = agent.classify_image_payload(standard)
    require(profile.get("valid") is True and profile.get("mode") == "jpeg_marker_structure", f"strict JPEG failed: {profile}")
    require(agent.validate_content(standard, ".jpg")[0] is True, "strict JPEG validate_content failed")

    dnl = root / "dnl.jpg"
    dnl.write_bytes(dnl_jpeg())
    require(agent._jpeg_dimensions(dnl) is None, "legacy strict validator unexpectedly accepted DNL control")
    profile = agent.classify_image_payload(dnl)
    require(profile.get("valid") is True, f"DNL JPEG rejected: {profile}")
    require(profile.get("mode") == "jpeg_uyap_compatible_dnl", f"wrong DNL mode: {profile}")
    require((profile.get("width"), profile.get("height")) == (3, 2), f"wrong DNL dimensions: {profile}")
    require(agent.validate_content(dnl, ".jpg")[0] is True, "DNL JPEG validate_content failed")

    # Fail-closed control: arbitrary bytes after EOI stay invalid.
    trailing = root / "trailing.jpg"
    trailing.write_bytes(standard_jpeg() + b"ARBITRARY_TRAILING_PAYLOAD")
    profile = agent.classify_image_payload(trailing)
    require(profile.get("valid") is False and profile.get("mode") == "jpeg_compatible_trailing_data", f"arbitrary JPEG tail accepted: {profile}")
    valid, code, _ = agent.validate_content(trailing, ".jpg")
    require(valid is False and code == "INVALID_IMAGE", "arbitrary JPEG tail did not fail INVALID_IMAGE")

    # Fail-closed control: SOI/SOS/EOI alone is not enough without a valid SOF.
    fake = root / "fake.jpg"
    fake.write_bytes(b"\xff\xd8" + sos1() + b"\x01\x02\x03\xff\xd9")
    profile = agent.classify_image_payload(fake)
    require(profile.get("valid") is False, f"fake JPEG accepted: {profile}")

    # Existing RC15.8.x INVALID_IMAGE quarantine can be repaired locally after
    # upgrading the agent; no second UYAP download is required.
    session_id = "jpeg-compat-session"
    import hashlib
    session_ref = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    qdir = root / "UYAP_Arsiv" / "Hatalı Dosyalar" / "Goruntu_Yapi_Sorunu" / session_ref
    qdir.mkdir(parents=True)
    quarantined = qdir / "legacy_INVALID_IMAGE.invalid"
    quarantined.write_bytes(dnl_jpeg())
    response = agent.revalidate_quarantined({
        "downloadsRoot": str(root),
        "quarantinePath": str(quarantined),
        "sessionId": session_id,
        "targetPath": "UYAP_Arsiv/Test/2026-1/dnl.jpg",
        "document": {
            "documentId": "jpeg-dnl-1",
            "caseId": "2026/1",
            "documentTitle": "DNL JPEG",
            "extension": ".jpg",
            "targetPath": "UYAP_Arsiv/Test/2026-1/dnl.jpg",
            "attempts": 1,
            "sourceSystem": "UYAP",
        },
    })
    require(response.get("valid") is True, f"quarantine JPEG revalidation failed: {response}")
    require(response.get("imageValidationMode") == "jpeg_uyap_compatible_dnl", f"revalidation mode missing: {response}")
    require(response.get("validationNotes"), "compatibility validation note missing")
    require(Path(response["localPath"]).is_file(), "revalidated JPEG not moved to final target")

print("RC15.9.1 JPEG COMPAT REGRESSION: PASS")
