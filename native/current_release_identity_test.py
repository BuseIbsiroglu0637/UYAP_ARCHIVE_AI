#!/usr/bin/env python3
"""Authoritative identity check for the current product contract.

Runtime components keep their native version declarations for packaging safety.
The machine-readable release contract is the expected source and this test stops
those declarations from drifting apart.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"
CONTRACT = json.loads((ROOT / "config" / "release-contract.json").read_text(encoding="utf-8"))
RELEASE = CONTRACT["release"]
AGENT = CONTRACT["nativeAgent"]
STORAGE = CONTRACT["storage"]


def constant(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
bridge = (ROOT / "background" / "native-agent.js").read_text(encoding="utf-8")
constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
popup = (ROOT / "popup" / "popup.js").read_text(encoding="utf-8")
workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")

assert manifest["manifest_version"] == RELEASE["manifestVersion"], "Chrome MV3 contract mismatch"
assert manifest["version"] == RELEASE["extensionVersion"], "extension version mismatch"
assert manifest["version_name"] == RELEASE["versionName"], "release identity mismatch"
assert int(manifest["minimum_chrome_version"]) == RELEASE["minimumChromeVersion"], "minimum Chrome mismatch"
assert constant(NATIVE / "uyap_archive_agent.py", "AGENT_VERSION") == AGENT["version"], "native agent mismatch"
assert constant(NATIVE / "uyap_archive_agent.py", "PROTOCOL_VERSION") == AGENT["protocolVersion"], "protocol mismatch"
assert constant(NATIVE / "install_host.py", "VERSION") == AGENT["version"], "installer version mismatch"
assert constant(NATIVE / "install_host.py", "MIN_REUSABLE_VERSION") == AGENT["minimumVersion"], "minimum reusable agent mismatch"
assert f'const MIN_AGENT_VERSION = "{AGENT["minimumVersion"]}";' in bridge, "extension-agent minimum mismatch"
assert f'export const CONTENT_PROTOCOL_VERSION = "{CONTRACT["contentProtocolVersion"]}";' in constants, "content protocol mismatch"
assert f'export const RELIABILITY_SCHEMA_VERSION = {STORAGE["reliabilitySchemaVersion"]};' in constants, "reliability schema mismatch"
assert f'export const DB_NAME = "{STORAGE["indexedDbName"]}";' in constants, "DB name mismatch"
assert f'export const DB_VERSION = {STORAGE["indexedDbVersion"]};' in constants, "DB version mismatch"
assert RELEASE["versionName"] in popup, "popup fallback/version string drift"
assert RELEASE["versionName"] in workspace, "workspace fallback/version string drift"

print(f'{RELEASE["versionName"]} / Native Agent {AGENT["version"]}: PASS')
