#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("uyap_archive_agent", HERE / "uyap_archive_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
# Synthetic test: only the temporary root may be scanned, never the developer's
# selected archive or browser download folders on this machine.
_all_archive_roots = agent._archive_root_candidates
agent._archive_root_candidates = lambda supplied_root=None: _all_archive_roots(supplied_root)[:1] if supplied_root else []


def req(value, message):
    if not value:
        raise AssertionError(message)

manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
manager = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
service = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
agent_source = (HERE / "uyap_archive_agent.py").read_text(encoding="utf-8")

req("2.9.0 RC15.9." in manifest.get("version_name", ""), "release identity")
contract = json.loads((ROOT / "config" / "release-contract.json").read_text(encoding="utf-8"))
req(agent.AGENT_VERSION == contract["nativeAgent"]["version"], agent.AGENT_VERSION)
req("setInterval(()=>void refreshWorkspaceLiveState(),WORKSPACE_LIVE_REFRESH_MS)" in workspace,
    "Workspace live refresh timer missing")
req("GET_ACTIVE_SESSION" in workspace[workspace.index("async function refreshWorkspaceLiveState"):],
    "Workspace does not refresh selected session")
req("GET_REPORT_DATA" in workspace[workspace.index("async function refreshWorkspaceLiveState"):],
    "Workspace archive table does not refresh while open")
req("forceRefresh:true" in workspace, "Workspace does not request a fresh local registry")
req("forceRefresh = false" in manager and "forceRefresh ? null : cachedResult()" in manager,
    "archive registry cache cannot be bypassed")
req("invalidateArchiveRegistryCache();" in manager, "archive mutations do not invalidate registry cache")
req("forceRefresh:request.forceRefresh===true" in service, "service worker does not forward force refresh")
# S2-06: the snapshot memoizes _registry_row_path per (root, local_path, target_path).
req("local_path = _registry_row_path(root, row)" in agent_source
    or ("local_path = row_path(root, row)" in agent_source and "_registry_row_path(root, row, probe)" in agent_source),
    "case index still uses stale absolute local_path")
req('if os.name != "nt" and re.match' not in agent_source[agent_source.index("case_groups = {}"):agent_source.index("physical_only = []")],
    "Windows-only stale path branch still present in case index")

# Physical-only files must still surface as a recoverable case in Workspace.
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    physical = root / "UYAP_Arsiv" / "Kastamonu 2. Asliye Hukuk Mahkemesi" / "2026_221_Dosya" / "Evraklar" / "ornek.pdf"
    physical.parent.mkdir(parents=True)
    physical.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
    registry = agent.list_archive_registry({"downloadsRoot": str(root), "limit": 100})
    req(registry["physicalDiscoveredFiles"] == 1, registry)
    req(len(registry["caseIndex"]) == 1, registry["caseIndex"])
    case = registry["caseIndex"][0]
    req(case["caseId"] == "2026/221", case)
    req(case["presentCount"] == 1 and case["problemCount"] == 0, case)
    req(case["documentCount"] == 0 and case["unregisteredPhysicalCount"] == 1, case)

# A copied DB row must resolve through target_path even when local_path points at the old PC.
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    relative = "UYAP_Arsiv/Kastamonu 2. Asliye Hukuk Mahkemesi/2026_221_Dosya/Evraklar/eski.pdf"
    physical = root.joinpath(*relative.split("/"))
    physical.parent.mkdir(parents=True)
    payload = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    physical.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    connection = agent.database(root)
    connection.execute(
        """INSERT INTO documents
           (session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
            source_fingerprint,source_system,institution_name,source_document_id,
            archive_identity_key,document_date,target_path,status,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("old", "doc-1", "2026/221", "Eski", "Evraklar",
         r"Z:\\OldPC\\UYAP_Arsiv\\missing\\eski.pdf", len(payload), digest, "fp",
         "UYAP", "Kastamonu 2. Asliye Hukuk Mahkemesi", "123456", None,
         "21/09/2026", relative, "VERIFIED", "2026-09-21T08:00:00Z")
    )
    connection.commit()
    connection.close()
    registry = agent.list_archive_registry({"downloadsRoot": str(root), "limit": 100})
    req(registry["physicalReadyFiles"] == 1, registry)
    case = registry["caseIndex"][0]
    req(case["physicalReadyCount"] == 1 and case["missingCount"] == 0, case)

print("RC15.9.37 Workspace live sync regression: PASS")
