from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
dm = (ROOT / 'background' / 'download-manager.js').read_text(encoding='utf-8')
fp = (ROOT / 'shared' / 'failure-policy.js').read_text(encoding='utf-8')
content = (ROOT / 'content' / 'content.js').read_text(encoding='utf-8')
constants = (ROOT / 'shared' / 'constants.js').read_text(encoding='utf-8')

assert 'REBIND_DOCUMENT: "rebind_document"' in constants
assert 'async function rebindSingleDocument' in dm
assert "type: MESSAGE.REBIND_DOCUMENT" in dm
assert 'async function rebindDocument' in content

start = dm.index('async function startDocumentDownload')
end = dm.index('\nasync function clickDocumentAcrossFrames', start)
hot = dm[start:end]
strategy_start = hot.index('if (strategy.freshRebind)')
strategy_end = hot.index('const attempts', strategy_start)
strategy_block = hot[strategy_start:strategy_end]
assert 'rebindSingleDocument' in strategy_block
assert 'refreshSessionRuntimeBindings' not in strategy_block

native_only = re.search(r"native_only:\s*Object\.freeze\(\{(.*?)\}\)", fp, re.S)
assert native_only and 'freshRebind: false' in native_only.group(1)
assert "candidates = ['fallback_fast', 'native_only', 'fresh_rebind'];" in fp

session_wide = re.search(r"const SESSION_WIDE_START_FAILURES = new Set\(\[(.*?)\]\);", dm, re.S)
assert session_wide
assert 'DOWNLOAD_START_FAILED' in session_wide.group(1)
assert 'NATIVE_DOWNLOAD_NOT_CORRELATED' not in session_wide.group(1)
assert 'CLICK_DOWNLOAD_TIMEOUT' not in session_wide.group(1)

assert 'DIRECT_DOWNLOAD_GENERIC_DOCUMENT_WINDOW_MS = 4_000' in dm
assert 'DIRECT_DOWNLOAD_UNKNOWN_DOCUMENT_WINDOW_MS = 2_000' in dm
assert "unknownDocumentType = detectedExtension === \".bin\"" in dm

print('10K+ SCALE HOT PATH REGRESSION: PASS')
