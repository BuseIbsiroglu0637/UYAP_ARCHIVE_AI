#!/usr/bin/env python3
"""One authoritative identity check for the imported RC source baseline.

Historical regression tests check the release family, not an obsolete RC label.
When preparing a new release, update the expected identity here intentionally.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native"


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
assert manifest["manifest_version"] == 3, "Chrome MV3 expected"
assert manifest["version"] == "2.9.0", "extension version mismatch"
assert manifest["version_name"] == "2.9.0 RC15.9.38 ARCHIVE-SYNC-RACE-FIX", "RC identity mismatch"
assert int(manifest["minimum_chrome_version"]) >= 120, "minimum Chrome changed"
assert constant(NATIVE / "uyap_archive_agent.py", "AGENT_VERSION") == "2.7.64", "native agent mismatch"
assert constant(NATIVE / "uyap_archive_agent.py", "PROTOCOL_VERSION") == 2, "protocol changed"
assert constant(NATIVE / "install_host.py", "VERSION") == "2.7.64", "installer mismatch"
assert constant(NATIVE / "install_host.py", "MIN_REUSABLE_VERSION") == "2.7.64", "minimum agent mismatch"
assert 'const MIN_AGENT_VERSION = "2.7.64";' in bridge, "extension-agent contract mismatch"
print("RC15.9.38 version identity and Native Agent 2.7.64: PASS")
