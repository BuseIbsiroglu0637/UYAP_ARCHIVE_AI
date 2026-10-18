#!/usr/bin/env python3
"""Beta1 INTERNAL-ONLY identity gate. No network or user archive access."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
release = json.loads((root / "config/release-contract.json").read_text(encoding="utf-8"))["release"]
channels = json.loads((root / "config/release-channels.json").read_text(encoding="utf-8"))
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
name = "2.9.0 BETA1 P1P2 TEST ONLY"

assert release["releaseId"] == "BETA1"
assert release["releaseName"] == "P1P2-TEST-ONLY"
assert release["channel"] == "beta"
assert release["environment"] == "stage"
assert release["versionName"] == name
assert channels["channels"]["beta"]["signedBuildRequired"] is True
assert manifest["version_name"] == name
assert manifest["version"] == release["extensionVersion"] == "2.9.0"
for target in ("popup/popup.js", "workspace/workspace.js", "native/portable_installer.ps1"):
    assert name in (root / target).read_text(encoding="utf-8-sig"), target
assert "BETA1 P1P2 INTERNAL TEST ONLY" in (root / "SETUP_UYAP_ARCHIVE_AI.bat").read_text(encoding="utf-8-sig")
workflow = (root / ".github/workflows/beta1-internal-validation.yml").read_text(encoding="utf-8")
assert "UYAP_Archive_AI_BETA1_Setup_amd64.exe" in workflow
assert "UYAP_Archive_AI_BETA1_P1P2_TEST_ONLY_amd64.exe" in workflow
assert "NotSigned" in workflow
assert "actions/upload-artifact@" in workflow
print("BETA1 INTERNAL IDENTITY: PASS (stage/beta; unsigned distribution forbidden)")
