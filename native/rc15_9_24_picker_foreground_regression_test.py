#!/usr/bin/env python3
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("uyap_archive_agent", HERE / "uyap_archive_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def req(value, message):
    if not value:
        raise AssertionError(message)


req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
script = agent._windows_folder_picker_script()
req("$owner.TopMost = $true" in script, "picker owner is not topmost")
req("SetForegroundWindow($owner.Handle)" in script, "picker owner is not foregrounded")
req("$dialog.ShowDialog($owner)" in script, "folder dialog has no explicit owner")
req("$dialog.ShowDialog()" not in script, "ownerless folder dialog regression")
req("$dialog.Dispose()" in script and "$owner.Dispose()" in script, "picker resources are not disposed")

bridge = (HERE.parent / "background" / "native-agent.js").read_text(encoding="utf-8")
req('if (action === "select_existing_archive") return 190_000;' in bridge, "bounded bridge timeout missing")
req('const MIN_AGENT_VERSION = "2.7.64";' in bridge, "old agent is not rejected")

popup = (HERE.parent / "popup" / "popup.js").read_text(encoding="utf-8")
req("Klasör seçme penceresi açıldı" in popup, "visible picker state copy missing")

print("RC15.9.24 foreground folder picker regression: PASS")
