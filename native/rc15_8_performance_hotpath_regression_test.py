from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
PERF = (ROOT / "shared" / "performance-policy.js").read_text(encoding="utf-8")
AGENT = (ROOT / "native" / "uyap_archive_agent.py").read_text(encoding="utf-8")

# The 11k+ local archive must not be rescanned before every UYAP click.
assert "function reusableLocalArchiveScan" in DM
assert '"session-local-scan-cache"' in DM and '"batch-local-scan-cache"' in DM
assert 'document.localArchiveMatchState === "not_found"' in DM
assert "localArchiveCheckedRoot" in DM
assert "localArchiveScanRoot" in DM

# Large reconciliation batches reduce repeated SQLite index + directory-cache work.
assert "const chunkSize = 3_000;" in DM

# Root changes still force a real reconciliation instead of trusting stale cache.
assert "{downloadsRoot, force:true, largeBatchNo:activeBatch, nativePriority:0}" in DM

# Historical DB rows pointing to the same archive path are hashed only once per proof pass.
assert "proven_paths = set()" in AGENT
assert "if path_key and path_key in proven_paths" in AGENT
assert tuple(map(int, AGENT.split('AGENT_VERSION = "', 1)[1].split('"', 1)[0].split("."))) >= (2, 7, 64)

# Sequential correlation safety and conservative failure pacing remain intact.
assert "if (await documentStore.hasActiveDownload(sessionId)) return;" in DM
assert "fast: Object.freeze({ minMs: 25, maxMs: 70 })" in PERF
assert "cautious: Object.freeze({ minMs: 400, maxMs: 650 })" in PERF
assert "backoff: Object.freeze({ minMs: 1_200, maxMs: 2_000 })" in PERF

print("RC15.8 PERFORMANCE HOTPATH REGRESSION: PASS")
