#!/usr/bin/env python3
"""Phase 1 product-contract and module-boundary smoke test.

This is intentionally static/offline. It must not touch UYAP, cloud services or
Windows registry state. The goal is to stop architectural drift before runtime.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

release = json.loads((ROOT / "config" / "release-contract.json").read_text(encoding="utf-8"))
environments = json.loads((ROOT / "config" / "environments.json").read_text(encoding="utf-8"))

assert release["schemaVersion"] == 1
assert release["product"] == "UYAP Archive AI"
assert release["release"]["environment"] in environments["profiles"]
assert release["release"]["channel"] in environments["profiles"][release["release"]["environment"]]["allowedReleaseChannels"]
assert release["compatibility"]["archiveEngineCloudIndependent"] is True
assert release["compatibility"]["verifiedContentOnlyForAI"] is True

assert environments["schemaVersion"] == 1
assert environments["defaultEnvironment"] == "prod"
assert set(environments["profiles"]) == {"dev", "stage", "prod"}
for name, profile in environments["profiles"].items():
    assert profile["localFirst"] is True, f"{name}: local-first must stay enabled"
    assert profile["cloudServicesEnabledByDefault"] is False, f"{name}: cloud must be opt-in"
    assert profile["rawDocumentsCloudAllowedByDefault"] is False, f"{name}: raw document cloud default forbidden"
    assert profile["aiEnabledByDefault"] is False, f"{name}: AI must be opt-in"
    assert profile["diagnostics"]["includeRawLegalData"] is False, f"{name}: diagnostics privacy regression"

forbidden_tokens = {"service_role", "secretkey", "privatekey", "rawdocumentuploadbydefault"}
serialized = json.dumps(environments, ensure_ascii=False).lower()
# The forbidden list is allowed to name forbidden concepts; values/config keys elsewhere are not.
without_forbidden_declaration = serialized.split('"forbidden"', 1)[0]
assert not any(token in without_forbidden_declaration for token in forbidden_tokens), "secret-like runtime config leaked into environment defaults"

IMPORT_RE = re.compile(r"\b(?:import|export)\s+(?:[^;\n]*?\s+from\s+)?[\"']([^\"']+)[\"']")
LAYERS = {"shared", "content", "background", "popup", "workspace", "account"}
ALLOWED = {
    "shared": {"shared"},
    "content": {"content", "shared"},
    "background": {"background", "shared"},
    "popup": {"popup", "shared", "account"},
    "workspace": {"workspace", "shared", "account"},
    "account": {"account", "shared"},
}


def target_layer(source: Path, specifier: str):
    if not specifier.startswith("."):
        return None
    target = (source.parent / specifier).resolve()
    try:
        relative = target.relative_to(ROOT.resolve())
    except ValueError:
        return "OUTSIDE_REPO"
    return relative.parts[0] if relative.parts else None


violations = []
for layer in sorted(LAYERS):
    for path in sorted((ROOT / layer).rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for specifier in IMPORT_RE.findall(text):
            target = target_layer(path, specifier)
            if target in LAYERS and target not in ALLOWED[layer]:
                violations.append(f"{path.relative_to(ROOT)} -> {specifier} ({target})")
            if target == "OUTSIDE_REPO":
                violations.append(f"{path.relative_to(ROOT)} -> {specifier} (outside repo)")

assert not violations, "module boundary violations:\n" + "\n".join(violations)
print("Phase 1 release/config contract and module boundaries: PASS")
