from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / 'background' / 'download-manager.js').read_text(encoding='utf-8')
PERF = (ROOT / 'shared' / 'performance-policy.js').read_text(encoding='utf-8')

# Finalization guarantees multiple parts above 1000 documents, so only those
# jobs receive the controlled pacing profile by count.
assert 'const ARCHIVE_PART_MAX_DOCUMENTS = 1000;' in DM
assert 'archiveWillBePartitioned' in DM
assert 'count > ARCHIVE_PART_MAX_DOCUMENTS || (count > 1 && bytes > ARCHIVE_PART_TARGET_BYTES)' in DM
assert 'verifiedArchiveBytes' in DM
assert 'DOWNLOAD_PACING_PROFILE_CHANGED' in DM
assert 'adaptPacingForPartitionedArchive' in DM
assert 'windowSystemicErrorRate' in DM
assert 'partitioned_systemic_pressure' in DM
assert 'partitioned_error_pressure' not in DM
assert 'partitionedArchiveMode' in DM
assert 'downloadPacingProfile: partitionedArchiveMode ? "partitioned-controlled" : "standard-fast"' in DM
assert 'mode: partitionedArchiveMode ? "normal" : "fast"' in DM

# Standard and partitioned jobs must use separate pacing ranges.
assert 'PARTITIONED_ARCHIVE_PACING_PROFILES' in PERF
assert 'fast: Object.freeze({ minMs: 25, maxMs: 70 })' in PERF
assert PERF.count('fast: Object.freeze({ minMs: 25, maxMs: 70 })') >= 2
assert PERF.count('normal: Object.freeze({ minMs: 90, maxMs: 160 })') >= 2
assert 'partitionedArchiveMode ? PARTITIONED_ARCHIVE_PACING_PROFILES : PACING_PROFILES' in PERF

# Batch boundaries are checkpoints, not fixed four-second sleeps.
assert 'largeCaseBatchTransitionDelayMs(session)' in DM
assert 'transitionMode: "adaptive"' in DM
assert 'transitionDelayMs' in DM
assert 'return 4_000' not in DM[DM.index('function largeCaseBatchTransitionDelayMs'):DM.index('function archiveWillBePartitioned')] if 'function largeCaseBatchTransitionDelayMs' in DM else True

print('ADAPTIVE PACING REGRESSION: PASS')
