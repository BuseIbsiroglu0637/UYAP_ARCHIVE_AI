#!/usr/bin/env python3
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(value, message):
    if not value:
        raise AssertionError(message)


req("RC15.9.37 QUEUE-RECOVERY" in manifest.get("version_name", ""), "release identity")
req("selectedArchiveKey:null" in workspace, "archive-only workspace selection state missing")
req("function archiveCaseEntries()" in workspace, "native caseIndex adapter missing")
req("function selectedArchiveCase()" in workspace, "archive selection resolver missing")
req("archive::${encodeURIComponent(item.key)}" in workspace, "archive case is not added to picker")
req("yerel arşivden kurtarıldı" in workspace, "files summary does not expose recovered archives")
req("Tarayıcı oturumu yok • yerel _archive.db kaydı" in workspace, "archive-only job row missing")
req("UYAP'tan işi yeniden oluştur" in workspace, "safe rebuild action missing")
req("state.selectedId=null;state.selectedArchiveKey" in workspace, "archive-only selection must not impersonate a live session")
req("if(!selected)return docs" in workspace, "local archive scoping guard missing")

print("RC15.9.29 workspace archive recovery regression: PASS")
