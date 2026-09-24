from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / 'background' / 'download-manager.js').read_text(encoding='utf-8')
BRIDGE = (ROOT / 'background' / 'native-agent.js').read_text(encoding='utf-8')
AGENT = (ROOT / 'native' / 'uyap_archive_agent.py').read_text(encoding='utf-8')
POPUP = (ROOT / 'popup' / 'popup.js').read_text(encoding='utf-8')
MANIFEST = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))

assert 'RC15.' in MANIFEST['version_name']
assert 'UYAP Archive AI' in MANIFEST['name']

# User sees one AUTO engine, not selectable small/large/speed modes.
assert 'Büyük Dosya Modu' not in POPUP
assert 'Kontrollü indirme hızı' not in POPUP
assert 'Hızlı indirme modu' not in POPUP
assert 'AUTO motor' in POPUP
assert 'AUTO işlem' in POPUP

# Native bridge is split so registry scans and bulk reconciliation/finalization
# cannot block validation or one another.
assert 'const nativeLanes = Object.freeze' in BRIDGE
assert 'fast: createLane("fast")' in BRIDGE
assert 'bulk: createLane("bulk")' in BRIDGE
assert 'registry: createLane("registry")' in BRIDGE
assert 'BULK_ACTIONS' in BRIDGE
assert '"sync_inventory", "finalize_archive", "format_audit"' in BRIDGE
assert 'REGISTRY_ACTIONS' in BRIDGE
assert '"list_archive_registry", "select_existing_archive"' in BRIDGE
assert 'laneForAction' in BRIDGE
assert 'lanes' in BRIDGE

# Large local archive reconciliation is incremental and prefetched one batch ahead.
assert 'ensureLocalArchiveBatchReconciled' in DM
assert 'prefetchNextLocalArchiveBatch' in DM
assert 'AUTO_PIPELINE_BATCH_PREFETCH' in DM
assert 'localArchiveReconciledBatches' in DM
assert 'largeBatchNo:largeCaseMode ? 1 : null' in DM
assert 'batch-local-scan-cache' in DM

# Browser correlation remains concurrency=1 while validation can overlap one next download.
assert 'MAX_PIPELINED_VALIDATIONS = 2' in DM
assert 'validatingDocuments = new Map()' in DM
assert 'status: STATUS.COMPLETED' in DM
assert 'validationPipeline: allowPipelineAdvance ? "overlapped" : "serial"' in DM
assert 'if (await documentStore.hasActiveDownload(sessionId)) return;' in DM
assert 'sessionValidationCount(sessionId) >= (circuitHalfOpenProbe ? 1 : MAX_PIPELINED_VALIDATIONS)' in DM

# HTML circuit breaker no longer runs an expensive heartbeat for every small HTML response.
assert 'htmlOrdinal === 1 || htmlOrdinal === 3 || probeStale' in DM
assert 'lastHtmlSessionProbeAt' in DM

# SQLite side is WAL-safe for two native host processes and indexed by scope.
assert 'AGENT_VERSION = "2.7.64"' in AGENT
assert 'PRAGMA journal_mode=WAL' in AGENT
assert 'PRAGMA busy_timeout=30000' in AGENT
assert 'idx_documents_status_case' in AGENT
assert 'idx_documents_scope' in AGENT
assert 'scope_case_id' in AGENT
assert 'proof_cache = {}' in AGENT
assert 'sqlite_wal_dual_lane_v1' in AGENT

print('RC15.8 AUTO PIPELINED NATIVE ENGINE REGRESSION: PASS')
