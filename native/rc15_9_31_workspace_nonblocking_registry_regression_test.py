from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
MANAGER = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(condition, message):
    if not condition:
        raise AssertionError(message)


start = WORKSPACE.index("async function loadSessions")
end = WORKSPACE.index("function renderSessionPicker", start)
load_sessions = WORKSPACE[start:end]

req("GET_SESSIONS" in load_sessions and "GET_ACTIVE_SESSION" in load_sessions,
    "Workspace session requests missing")
req("GET_ARCHIVE_REGISTRY" not in load_sessions.split("void refreshArchiveRegistryInBackground", 1)[0],
    "Workspace still blocks initial session render on archive registry")
req("renderSessionPicker()" in load_sessions and "await loadSelectedSummary()" in load_sessions,
    "Workspace does not render the selected session first")
req("void refreshArchiveRegistryInBackground()" in load_sessions,
    "Archive registry background refresh missing")
req("let archiveRegistryRefreshTask=null" in WORKSPACE,
    "Workspace registry single-flight guard missing")
req("ARCHIVE_REGISTRY_CACHE_TTL_MS = 60_000" in MANAGER,
    "Registry cache TTL missing")
req("while (archiveRegistryInFlight)" in MANAGER and "await archiveRegistryInFlight.promise" in MANAGER,
    "Concurrent registry requests are not coalesced")
req("archiveRegistryCache = null" in MANAGER,
    "Archive selection does not invalidate registry cache")
req("RC15.9.37 QUEUE-RECOVERY" in MANIFEST.get("version_name", ""),
    "release identity mismatch")

print("RC15.9.31 workspace nonblocking registry regression: PASS")
