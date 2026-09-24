from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT/'background'/'download-manager.js').read_text(encoding='utf-8')
PERF = (ROOT/'shared'/'performance-policy.js').read_text(encoding='utf-8')
FAIL = (ROOT/'shared'/'failure-policy.js').read_text(encoding='utf-8')
CONTENT = (ROOT/'content'/'content.js').read_text(encoding='utf-8')
CAP = (ROOT/'background'/'save-capture.js').read_text(encoding='utf-8')

# Sequential safety gate remains intact.
assert 'if (await documentStore.hasActiveDownload(sessionId)) return;' in DM
# Near-zero idle healthy path.
assert PERF.count('fast: Object.freeze({ minMs: 25, maxMs: 70 })') >= 2
assert PERF.count('normal: Object.freeze({ minMs: 90, maxMs: 160 })') >= 2
assert 'const target = Math.max(now + 20' in DM
assert 'const LARGE_CASE_MIN_BATCH_TRANSITION_MS = 50;' in DM
# Safety backoff remains materially slower than healthy pacing.
assert 'backoff: Object.freeze({ minMs: 1_200, maxMs: 2_000 })' in PERF
assert 'NATIVE_DOWNLOAD_NOT_CORRELATED' in PERF and 'CLICK_DOWNLOAD_TIMEOUT' in PERF
# No unconditional 100 ms stale-menu wait in the click path.
assert 'dismissStaleSaveMenuIfNeeded' in CONTENT
assert "throw new Error('STALE_SAVE_MENU: Önceki Kaydet menüsü kapanmadı.');" in CONTENT
# High-confidence capture can resolve sooner; ambiguity/origin validation remains.
assert 'bestScore >= 130 && elapsed >= 75' in CAP
assert 'usable.length === 1 || usable[0].score > usable[1].score' in CAP
assert 'validateDownloadRequest' in CAP
# Faster preference windows, hard timeout/watchdog remain elsewhere.
assert 'nativePreferenceMs: 750' in FAIL
assert 'nativePreferenceMs: 450' in FAIL
assert 'CLICK_DOWNLOAD_TIMEOUT_MS' in DM
assert 'watchdogAlarmName' in DM
print('RC15.6 MAX-SAFE THROUGHPUT REGRESSION: PASS')
