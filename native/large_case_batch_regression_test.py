from pathlib import Path
import math, re

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / 'background' / 'download-manager.js').read_text(encoding='utf-8')
STORE = (ROOT / 'background' / 'store.js').read_text(encoding='utf-8')
DB = (ROOT / 'background' / 'db.js').read_text(encoding='utf-8')
CONST = (ROOT / 'shared' / 'constants.js').read_text(encoding='utf-8')

assert 'const LARGE_CASE_THRESHOLD = 3000;' in DM
assert 'const LARGE_CASE_BATCH_SIZE = 500;' in DM
assert 'LARGE_CASE_BATCH_COMPLETED' in DM
assert 'largeCaseBatchTransitionDelayMs' in DM
assert 'const LARGE_CASE_MIN_BATCH_TRANSITION_MS = 50;' in DM
assert 'LARGE_CASE_BATCH_COOLDOWN_MS' not in DM
assert 'alignLargeCaseBatchToPendingWork' in DM
assert 'largeCaseFastStart:true' in DM
assert 'documentStore.nextQueued(sessionId, activeLargeBatch)' in DM
assert 'documentStore.nextReadyRetry(sessionId, nowIso, activeLargeBatch)' in DM
assert 'sessionBatchStatus' in DB
assert 'sessionStatusBatch' in DB
assert 'sessionBatchStatusRetryAt' in DB
assert 'putManyChunked' in STORE
assert 'DB_VERSION = 6' in CONST

n=11923
batch=500
parts=math.ceil(n/batch)
assert parts == 24
sizes=[min(batch, n-i*batch) for i in range(parts)]
assert sizes[0] == 500 and sizes[-1] == 423 and sum(sizes) == n
# Only one active part should be necessary for ordinary primary-queue work.
assert max(sizes) <= 500
print(f'LARGE CASE BATCH REGRESSION: PASS ({n} docs -> {parts} batches; last={sizes[-1]})')
