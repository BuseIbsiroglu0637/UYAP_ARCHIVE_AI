from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
PERF = (ROOT / "shared" / "performance-policy.js").read_text(encoding="utf-8")

# HTML percentage alone must not globally throttle a healthy primary queue.
assert "partitioned_error_pressure" not in DM
assert "partitioned_systemic_pressure" in DM
assert "windowSystemicErrorRate" in DM
assert "consecutiveSystemic" in DM

# Automatic HTML retries are deferred across remaining large-case batches.
assert "deferAutomaticHtmlUntilPrimaryDrain" in DM
assert "readyRetry.retryClass === \"deferred_html\"" in DM
assert "deferFutureHtmlUntilPrimaryDrain" in DM
assert "hasFutureLargeBatches" in DM

# Safe throughput: still one-at-a-time, but lower idle ranges for partitioned jobs.
assert "if (await documentStore.hasActiveDownload(sessionId)) return;" in DM
assert "fast: Object.freeze({ minMs: 25, maxMs: 70 })" in PERF
assert "normal: Object.freeze({ minMs: 90, maxMs: 160 })" in PERF
assert "cautious: Object.freeze({ minMs: 400, maxMs: 650 })" in PERF
assert "backoff: Object.freeze({ minMs: 1_200, maxMs: 2_000 })" in PERF
assert "const target = Math.max(now + 20" in DM

print("RC15.2 throughput regression PASS")
