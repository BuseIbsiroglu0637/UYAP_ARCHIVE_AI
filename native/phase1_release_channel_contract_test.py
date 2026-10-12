#!/usr/bin/env python3
"""Static Phase 1 release-channel and packaging contract test."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

release = json.loads((ROOT / "config" / "release-contract.json").read_text(encoding="utf-8"))
envs = json.loads((ROOT / "config" / "environments.json").read_text(encoding="utf-8"))
channels = json.loads((ROOT / "config" / "release-channels.json").read_text(encoding="utf-8"))
builder = (ROOT / "native" / "build_offline_setup.ps1").read_text(encoding="utf-8")
launcher = (ROOT / "HAZIR_SETUP_EXE_OLUSTUR.bat").read_text(encoding="utf-8")

assert channels["schemaVersion"] == 1
assert set(channels["channels"]) == {"alpha", "beta", "rc", "stable"}

current_channel = release["release"]["channel"]
current_environment = release["release"]["environment"]
policy = channels["channels"][current_channel]
assert policy["environment"] == current_environment
assert current_channel in envs["profiles"][current_environment]["allowedReleaseChannels"]

assert channels["channels"]["alpha"]["environment"] == "dev"
assert channels["channels"]["beta"]["environment"] == "stage"
assert channels["channels"]["rc"]["environment"] == "prod"
assert channels["channels"]["stable"]["environment"] == "prod"
for name in ("beta", "rc", "stable"):
    assert channels["channels"][name]["signedBuildRequired"] is True, name

caps = channels["distributionCapabilities"]
assert caps["automaticUpdaterImplemented"] is False
assert caps["codeSigningPipelineImplemented"] is False
assert caps["stagedRolloutBackendImplemented"] is False

assert "config\\release-contract.json" in builder
assert "config\\release-channels.json" in builder
assert "$ReleaseId" in builder
assert "$ReleaseChannel" in builder
assert "$PackageDirName" in builder
assert "RC15.9.37" not in builder
assert "RC15.9.17" not in builder
assert "RC15.9.37" not in launcher

print("Phase 1 release channel and packaging contract: PASS")
