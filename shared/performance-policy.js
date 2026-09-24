import { isIsolatedContentError, retryQueuePolicy } from './failure-policy.js';
export const PACING_PROFILES = Object.freeze({
  // Standard jobs should stay as fast as the already-proven sequential path allows.
  fast: Object.freeze({ minMs: 25, maxMs: 70 }),
  normal: Object.freeze({ minMs: 90, maxMs: 160 }),
  cautious: Object.freeze({ minMs: 400, maxMs: 650 }),
  backoff: Object.freeze({ minMs: 1_200, maxMs: 2_000 })
});

export const PARTITIONED_ARCHIVE_PACING_PROFILES = Object.freeze({
  // RC15.6 maximum-safe sequential profile. Concurrency remains exactly 1.
  // Healthy runs are nearly zero-idle, while hard/session-shaped failures still
  // escalate through cautious/backoff. Throughput comes from removing waits,
  // never from overlapping two UYAP document downloads.
  fast: Object.freeze({ minMs: 25, maxMs: 70 }),
  normal: Object.freeze({ minMs: 90, maxMs: 160 }),
  cautious: Object.freeze({ minMs: 400, maxMs: 650 }),
  backoff: Object.freeze({ minMs: 1_200, maxMs: 2_000 })
});

const IMMEDIATE_BACKOFF_ERRORS = new Set([
  "LOGIN_REQUIRED",
  "HEARTBEAT_FAILED",
  "SESSION_DEGRADED",
  "NATIVE_AGENT_UNAVAILABLE"
]);

const STRUCTURAL_ERRORS = new Set([
  "DOWNLOAD_INTERRUPTED",
  "DOWNLOAD_STALLED",
  "DOWNLOAD_START_FAILED",
  "NATIVE_DOWNLOAD_NOT_CORRELATED",
  "CLICK_DOWNLOAD_TIMEOUT",
  "INVALID_CONTENT"
]);

const WINDOW_SIZE = 20;
const FAST_SUCCESS_STREAK = 10;
const HTML_FAST_SUCCESS_STREAK = 10;
const NORMAL_RECOVERY_STREAK = 5;
const CAUTIOUS_RECOVERY_STREAK = 3;
const HTML_BURST_RECOVERY_STREAK = 2;
const HTML_SYSTEMIC_CONSECUTIVE_THRESHOLD = 8;
const HTML_SYSTEMIC_MIN_WINDOW = 10;
const HTML_SYSTEMIC_RATE = 0.80;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function normalizeOutcome(outcome = {}) {
  return {
    ok: Boolean(outcome.ok),
    errorCode: outcome.errorCode || null,
    outcomeKey: outcome.outcomeKey || null
  };
}

function appendOutcome(current = [], outcome = {}) {
  let recent = Array.isArray(current) ? current.map(normalizeOutcome) : [];
  const normalized = normalizeOutcome(outcome);
  // The window is document-level, not attempt-level. A retry of the same
  // document replaces its previous result instead of inflating global error
  // pressure with repeated failures from one noisy UYAP document.
  if (normalized.outcomeKey) {
    recent = recent.filter((item) => item.outcomeKey !== normalized.outcomeKey);
  }
  recent.push(normalized);
  return recent.slice(-WINDOW_SIZE);
}

function trailingSystemicFailures(outcomes = []) {
  let count = 0;
  for (let index = outcomes.length - 1; index >= 0; index -= 1) {
    const outcome = outcomes[index];
    if (outcome?.ok || outcome?.errorCode === "HTML_RESPONSE" || isIsolatedContentError(outcome?.errorCode)) break;
    count += 1;
  }
  return count;
}

function trailingError(outcomes = [], errorCode) {
  let count = 0;
  for (let index = outcomes.length - 1; index >= 0; index -= 1) {
    const outcome = outcomes[index];
    if (outcome?.ok || outcome?.errorCode !== errorCode) break;
    count += 1;
  }
  return count;
}

function windowStats(outcomes = []) {
  const total = outcomes.length;
  const failed = outcomes.filter((outcome) => !outcome.ok).length;
  const html = outcomes.filter((outcome) => outcome.errorCode === "HTML_RESPONSE").length;
  const isolatedContent = outcomes.filter((outcome) => !outcome.ok && isIsolatedContentError(outcome.errorCode)).length;
  const systemicFailed = outcomes.filter((outcome) => !outcome.ok && outcome.errorCode !== "HTML_RESPONSE" && !isIsolatedContentError(outcome.errorCode)).length;
  return {
    total,
    failed,
    html,
    isolatedContent,
    systemicFailed,
    errorRate: total ? failed / total : 0,
    htmlRate: total ? html / total : 0,
    systemicErrorRate: total ? systemicFailed / total : 0
  };
}

export function clearRecoveredPacingError(current = {}, recoveredErrorCode = null) {
  const code = String(recoveredErrorCode || "");
  if (!code) return { ...(current || {}) };
  const before = Array.isArray(current?.recentOutcomes) ? current.recentOutcomes.map(normalizeOutcome) : [];
  const recentOutcomes = before.filter((outcome) => outcome.errorCode !== code);
  const removed = before.length - recentOutcomes.length;
  if (!removed) return { ...(current || {}) };

  const stats = windowStats(recentOutcomes);
  const consecutiveFailures = trailingSystemicFailures(recentOutcomes);
  const consecutiveHtml = trailingError(recentOutcomes, "HTML_RESPONSE");
  let mode = PACING_PROFILES[current?.mode] ? current.mode : "normal";
  let backoffReason = current?.backoffReason || null;
  let successStreak = Math.max(0, Number(current?.successStreak || 0));

  // A successful native validation/ping is positive evidence that the native
  // service recovered. Session backoff caused only by the old native outage must
  // not survive as a ghost throttle. Other current systemic errors still win.
  if (code === "NATIVE_AGENT_UNAVAILABLE" && backoffReason === "session") {
    backoffReason = null;
    if (stats.systemicFailed === 0) {
      const healthySuccesses = recentOutcomes.filter((outcome) => outcome.ok).length;
      mode = healthySuccesses >= FAST_SUCCESS_STREAK ? "fast" : "normal";
      successStreak = Math.max(successStreak, healthySuccesses);
    } else if (consecutiveFailures >= 2 || (stats.total >= 10 && stats.systemicErrorRate >= 0.20)) {
      mode = "cautious";
    } else {
      mode = "normal";
    }
  }

  return {
    ...(current || {}),
    mode,
    backoffReason,
    successStreak,
    failureStreak: 0,
    recentOutcomes,
    windowAttempts: stats.total,
    windowFailures: stats.failed,
    windowHtmlFailures: stats.html,
    windowIsolatedContentFailures: stats.isolatedContent,
    windowSystemicFailures: stats.systemicFailed,
    windowErrorRate: stats.errorRate,
    windowHtmlRate: stats.htmlRate,
    windowSystemicErrorRate: stats.systemicErrorRate,
    consecutiveFailures,
    consecutiveHtml,
    recoveredErrorCode: code,
    recoveredErrorCount: removed
  };
}

export function nextPacingState(current = {}, { ok = true, errorCode = null, outcomeKey = null } = {}) {
  let mode = PACING_PROFILES[current.mode] ? current.mode : "normal";
  let backoffReason = current.backoffReason || null;
  let successStreak = Math.max(0, Number(current.successStreak || 0));
  let failureStreak = Math.max(0, Number(current.failureStreak || 0));
  const recentOutcomes = appendOutcome(current.recentOutcomes, { ok, errorCode, outcomeKey });
  const stats = windowStats(recentOutcomes);
  const consecutiveFailures = trailingSystemicFailures(recentOutcomes);
  const consecutiveHtml = trailingError(recentOutcomes, "HTML_RESPONSE");

  if (ok) {
    successStreak += 1;
    failureStreak = 0;

    if (mode === "backoff" && backoffReason === "html_burst" && successStreak >= HTML_BURST_RECOVERY_STREAK) {
      mode = "normal";
      backoffReason = null;
    } else {
      if (mode === "backoff" && successStreak >= CAUTIOUS_RECOVERY_STREAK) mode = "cautious";
      if (mode === "cautious" && successStreak >= NORMAL_RECOVERY_STREAK) {
        mode = "normal";
        backoffReason = null;
      }
    }
    const fastThreshold = stats.html > 0 ? HTML_FAST_SUCCESS_STREAK : FAST_SUCCESS_STREAK;
    if (mode === "normal" && successStreak >= fastThreshold && stats.systemicErrorRate <= 0.10) mode = "fast";
  } else if (isIsolatedContentError(errorCode)) {
    // Deterministic per-document format/signature failures are quarantined.
    // They must not make the entire healthy UYAP queue enter global backoff.
    failureStreak = 0;
  } else if (errorCode === "HTML_RESPONSE") {
    // HTML is quarantined per document and retried later. An isolated HTML page
    // must not penalize healthy documents or reset the global success streak.
    // Only a burst across distinct documents is evidence of a session-shaped
    // problem and therefore deserves global backoff.
    failureStreak = 0;
    const systemicHtmlBurst =
      consecutiveHtml >= HTML_SYSTEMIC_CONSECUTIVE_THRESHOLD ||
      (stats.total >= HTML_SYSTEMIC_MIN_WINDOW &&
       stats.html >= Math.ceil(HTML_SYSTEMIC_MIN_WINDOW * HTML_SYSTEMIC_RATE) &&
       stats.htmlRate >= HTML_SYSTEMIC_RATE);
    if (systemicHtmlBurst) {
      // HTML is a content-endpoint signal, not proof that the whole UYAP session
      // is dead. Keep the queue moving with a modest cautious delay; the hard
      // RECOVERING state is reserved for login/source/native failures or for the
      // final deferred-retry phase when no fresh document remains.
      mode = "cautious";
      backoffReason = "html_burst_soft";
      successStreak = 0;
    }
  } else {
    successStreak = 0;
    failureStreak += 1;

    if (IMMEDIATE_BACKOFF_ERRORS.has(errorCode)) {
      mode = "backoff";
      backoffReason = "session";
    } else if (STRUCTURAL_ERRORS.has(errorCode)) {
      // A single quarantined document must not globally slow a healthy queue.
      // Escalate only when failures are consecutive across distinct documents
      // or the unique-document systemic rate is genuinely high.
      if (consecutiveFailures >= 3 || (stats.total >= 10 && stats.systemicErrorRate >= 0.30)) {
        mode = "backoff";
        backoffReason = "structural";
      } else if (consecutiveFailures >= 2 || (stats.total >= 10 && stats.systemicErrorRate >= 0.20)) {
        mode = "cautious";
        backoffReason = null;
      }
    } else if (consecutiveFailures >= 3 || (stats.total >= 10 && stats.systemicErrorRate >= 0.30)) {
      mode = "backoff";
      backoffReason = "errors";
    } else if (consecutiveFailures >= 2 || (stats.total >= 10 && stats.systemicErrorRate >= 0.20)) {
      mode = "cautious";
      backoffReason = null;
    }
  }

  return {
    mode,
    backoffReason,
    successStreak,
    failureStreak,
    recentOutcomes,
    windowAttempts: stats.total,
    windowFailures: stats.failed,
    windowHtmlFailures: stats.html,
    windowIsolatedContentFailures: stats.isolatedContent,
    windowSystemicFailures: stats.systemicFailed,
    windowErrorRate: stats.errorRate,
    windowHtmlRate: stats.htmlRate,
    windowSystemicErrorRate: stats.systemicErrorRate,
    consecutiveFailures,
    consecutiveHtml
  };
}

export function shouldRunDeferredRetry({ queued = null, readyRetry = null, primarySuccessesSinceRetry = 0, nowMs = Date.now() } = {}) {
  if (!readyRetry) return false;
  if (!queued) return true;

  const { successesRequired, maxDeferredMs, deferUntilPrimaryExhausted } = retryQueuePolicy(readyRetry);
  if (deferUntilPrimaryExhausted && queued) return false;
  const queuedAtMs = Date.parse(readyRetry.retryQueuedAt || readyRetry.retryAt || "");
  const deferredAgeMs = Number.isFinite(queuedAtMs) ? Math.max(0, nowMs - queuedAtMs) : 0;

  return Math.max(0, Number(primarySuccessesSinceRetry || 0)) >= successesRequired || deferredAgeMs >= maxDeferredMs;
}

export function pacingDelayMs(state = {}, random = Math.random(), { partitionedArchiveMode = false } = {}) {
  const profiles = partitionedArchiveMode ? PARTITIONED_ARCHIVE_PACING_PROFILES : PACING_PROFILES;
  const profile = profiles[state.mode] || profiles.normal;
  const boundedRandom = Math.min(0.999999, Math.max(0, Number(random) || 0));
  return Math.round(profile.minMs + (profile.maxMs - profile.minMs) * boundedRandom);
}

function appendRecentVerified(current = [], nowMs, verified) {
  const cutoff = nowMs - 60_000;
  const recent = (Array.isArray(current) ? current : [])
    .map(Number)
    .filter((value) => Number.isFinite(value) && value >= cutoff && value <= nowMs + 1_000);
  if (verified) recent.push(nowMs);
  return recent.slice(-120);
}

const LONG_RUN_SAMPLE_LIMIT = 60;
const LONG_RUN_RECENT_WINDOW = 50;
const LONG_RUN_BASELINE_WINDOW = 50;
const LONG_RUN_MAX_CHECKPOINTS = 32;

function appendBoundedNumber(current = [], value, limit = LONG_RUN_SAMPLE_LIMIT) {
  const finite = finiteNumber(value);
  const values = (Array.isArray(current) ? current : [])
    .map(Number)
    .filter((item) => Number.isFinite(item) && item >= 0);
  if (finite !== null) values.push(finite);
  return values.slice(-limit);
}

function averageNumbers(values = []) {
  const finite = (Array.isArray(values) ? values : []).map(Number).filter((value) => Number.isFinite(value) && value >= 0);
  return finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : 0;
}

function degradationPercent(baseline, recent) {
  const a = finiteNumber(baseline);
  const b = finiteNumber(recent);
  if (a === null || b === null || a <= 0) return 0;
  return ((b - a) / a) * 100;
}

function checkpointMilestone(verifiedAttempts) {
  const verified = Math.max(0, Number(verifiedAttempts || 0));
  if (verified < 100) return null;
  if (verified <= 1_000) return Math.floor(verified / 100) * 100;
  return Math.floor(verified / 500) * 500;
}

function longRunState(performance = {}) {
  const baselineTotalAvgMs = Number(performance.baselineTotalCount || 0) > 0
    ? Number(performance.baselineTotalSumMs || 0) / Number(performance.baselineTotalCount || 1)
    : 0;
  const baselineQueueAvgMs = Number(performance.baselineQueueCount || 0) > 0
    ? Number(performance.baselineQueueSumMs || 0) / Number(performance.baselineQueueCount || 1)
    : 0;
  const recentTotalAvgMs = averageNumbers(performance.recentVerifiedTotalMs);
  const recentQueueAvgMs = averageNumbers(performance.recentVerifiedQueueLookupMs);
  const totalDegradationPct = degradationPercent(baselineTotalAvgMs, recentTotalAvgMs);
  const queueDegradationPct = degradationPercent(baselineQueueAvgMs, recentQueueAvgMs);
  const heapBaseline = finiteNumber(performance.heapBaselineBytes);
  const heapLatest = finiteNumber(performance.heapLatestBytes);
  const heapGrowthPct = heapBaseline && heapLatest ? ((heapLatest - heapBaseline) / heapBaseline) * 100 : null;
  const verified = Math.max(0, Number(performance.verifiedAttempts || 0));

  let status = verified < LONG_RUN_BASELINE_WINDOW ? "warming" : "stable";
  if (verified >= 200) {
    const totalBad = totalDegradationPct >= 35 && recentTotalAvgMs >= baselineTotalAvgMs + 500;
    const queueBad = recentQueueAvgMs >= 25 && queueDegradationPct >= 100;
    const heapBad = heapGrowthPct !== null && heapGrowthPct >= 75;
    const totalWatch = totalDegradationPct >= 20 && recentTotalAvgMs >= baselineTotalAvgMs + 250;
    const queueWatch = recentQueueAvgMs >= 15 && queueDegradationPct >= 75;
    const heapWatch = heapGrowthPct !== null && heapGrowthPct >= 40;
    if (totalBad || queueBad || heapBad) status = "degrading";
    else if (totalWatch || queueWatch || heapWatch) status = "watch";
  }

  return {
    status,
    baselineTotalAvgMs,
    recentTotalAvgMs,
    totalDegradationPct,
    baselineQueueAvgMs,
    recentQueueAvgMs,
    queueDegradationPct,
    heapSupported: heapLatest !== null,
    heapBaselineBytes: heapBaseline,
    heapLatestBytes: heapLatest,
    heapPeakBytes: finiteNumber(performance.heapPeakBytes),
    heapGrowthPct,
    checkpointCount: Array.isArray(performance.soakCheckpoints) ? performance.soakCheckpoints.length : 0
  };
}

function rollingAverage(previousAverage, previousCount, value) {
  const sample = finiteNumber(value);
  if (sample === null) return { average: finiteNumber(previousAverage) || 0, count: Math.max(0, Number(previousCount || 0)) };
  const count = Math.max(0, Number(previousCount || 0));
  const average = finiteNumber(previousAverage) || 0;
  return {
    average: count ? ((average * count) + sample) / (count + 1) : sample,
    count: count + 1
  };
}

export function updatePerformanceMetrics(current = {}, sample = {}, nowMs = Date.now()) {
  const next = { ...current };
  const metrics = [
    ["queueLookup", sample.queueLookupMs],
    ["clickToDownload", sample.clickToDownloadMs],
    ["download", sample.downloadMs],
    ["validation", sample.validationMs],
    ["nativeAgent", sample.nativeAgentMs],
    ["nativeBridge", sample.nativeBridgeMs],
    ["nativeDbOpen", sample.nativeDbOpenMs],
    ["nativeTypeDetect", sample.nativeTypeDetectMs],
    ["nativeContentCheck", sample.nativeContentCheckMs],
    ["nativeHash", sample.nativeHashMs],
    ["nativeFsync", sample.nativeFsyncMs],
    ["nativeJournal", sample.nativeJournalMs],
    ["nativeMove", sample.nativeMoveMs],
    ["failureLatency", sample.failureLatencyMs],
    ["failureClickPhase", sample.failureClickPhaseMs],
    ["failureNativeWait", sample.failureNativeWaitMs],
    ["failureHistoryRecovery", sample.failureHistoryRecoveryMs],
    ["total", sample.totalMs]
  ];

  for (const [name, value] of metrics) {
    const result = rollingAverage(next[`${name}AvgMs`], next[`${name}Samples`], value);
    next[`${name}AvgMs`] = result.average;
    next[`${name}Samples`] = result.count;
    const finite = finiteNumber(value);
    if (finite !== null) next[`${name}LastMs`] = finite;
  }

  next.attempts = Math.max(0, Number(next.attempts || 0)) + 1;
  if (!Number.isFinite(Number(next.performanceStartedAtMs))) next.performanceStartedAtMs = nowMs;
  next.verifiedAttempts = Math.max(0, Number(next.verifiedAttempts || 0));
  next.failedAttempts = Math.max(0, Number(next.failedAttempts || 0));
  if (sample.verified) next.verifiedAttempts += 1;
  else next.failedAttempts += 1;
  if (!sample.verified) {
    if (sample.failureStage) next.lastFailureStage = String(sample.failureStage);
    if (sample.failureStrategy) next.lastFailureStrategy = String(sample.failureStrategy);
  }

  if (sample.verified) {
    const totalMs = finiteNumber(sample.totalMs);
    const queueLookupMs = finiteNumber(sample.queueLookupMs);
    if (totalMs !== null) {
      next.recentVerifiedTotalMs = appendBoundedNumber(next.recentVerifiedTotalMs, totalMs, LONG_RUN_RECENT_WINDOW);
      if (Math.max(0, Number(next.baselineTotalCount || 0)) < LONG_RUN_BASELINE_WINDOW) {
        next.baselineTotalSumMs = Math.max(0, Number(next.baselineTotalSumMs || 0)) + totalMs;
        next.baselineTotalCount = Math.max(0, Number(next.baselineTotalCount || 0)) + 1;
      }
    }
    if (queueLookupMs !== null) {
      next.recentVerifiedQueueLookupMs = appendBoundedNumber(next.recentVerifiedQueueLookupMs, queueLookupMs, LONG_RUN_RECENT_WINDOW);
      if (Math.max(0, Number(next.baselineQueueCount || 0)) < LONG_RUN_BASELINE_WINDOW) {
        next.baselineQueueSumMs = Math.max(0, Number(next.baselineQueueSumMs || 0)) + queueLookupMs;
        next.baselineQueueCount = Math.max(0, Number(next.baselineQueueCount || 0)) + 1;
      }
    }
  }

  const heapUsedBytes = finiteNumber(sample.heapUsedBytes);
  if (heapUsedBytes !== null) {
    if (!Number.isFinite(Number(next.heapBaselineBytes))) next.heapBaselineBytes = heapUsedBytes;
    next.heapLatestBytes = heapUsedBytes;
    next.heapPeakBytes = Math.max(heapUsedBytes, Number(next.heapPeakBytes || 0));
    next.recentHeapBytes = appendBoundedNumber(next.recentHeapBytes, heapUsedBytes, 20);
  }

  const scalarRuntime = {
    nativePendingRequests: sample.nativePendingRequests,
    nativeMaxQueueDepth: sample.nativeMaxQueueDepth,
    nativeConnectCount: sample.nativeConnectCount,
    nativeDisconnectCount: sample.nativeDisconnectCount,
    nativeTimeoutCount: sample.nativeTimeoutCount,
    frameInjections: sample.frameInjections,
    frameInjectionMax: sample.frameInjectionMax,
    completingDownloads: sample.completingDownloads
  };
  for (const [name, value] of Object.entries(scalarRuntime)) {
    const finite = finiteNumber(value);
    if (finite !== null) next[name] = finite;
  }

  next.recentVerifiedAt = appendRecentVerified(next.recentVerifiedAt, nowMs, Boolean(sample.verified));
  next.docsPerMinute = next.recentVerifiedAt.length;
  const elapsedMinutes = Math.max((nowMs - Number(next.performanceStartedAtMs || nowMs)) / 60_000, 1 / 60);
  next.sessionAvgDocsPerMinute = next.verifiedAttempts / elapsedMinutes;
  next.lastOutcome = sample.verified ? "verified" : "failed";
  next.lastErrorCode = sample.errorCode || null;
  next.updatedAt = new Date(nowMs).toISOString();

  if (sample.verified) {
    const milestone = checkpointMilestone(next.verifiedAttempts);
    if (milestone && milestone !== Number(next.lastSoakMilestone || 0)) {
      const longRun = longRunState(next);
      const checkpoints = Array.isArray(next.soakCheckpoints) ? next.soakCheckpoints.slice(-LONG_RUN_MAX_CHECKPOINTS + 1) : [];
      checkpoints.push({
        verified: milestone,
        at: new Date(nowMs).toISOString(),
        docsPerMinute: next.docsPerMinute,
        sessionAvgDocsPerMinute: Number(next.sessionAvgDocsPerMinute || 0),
        totalAvgMs: Number(next.totalAvgMs || 0),
        queueLookupAvgMs: Number(next.queueLookupAvgMs || 0),
        recentTotalAvgMs: longRun.recentTotalAvgMs,
        recentQueueAvgMs: longRun.recentQueueAvgMs,
        heapUsedBytes: heapUsedBytes,
        nativeConnectCount: Number(next.nativeConnectCount || 0),
        nativeDisconnectCount: Number(next.nativeDisconnectCount || 0)
      });
      next.soakCheckpoints = checkpoints;
      next.lastSoakMilestone = milestone;
    }
  }

  // Hard cap persisted telemetry size. This field is intentionally a compact
  // self-observation rather than an ever-growing event log.
  next.telemetryBytes = new TextEncoder().encode(JSON.stringify({
    recentVerifiedAt: next.recentVerifiedAt,
    recentVerifiedTotalMs: next.recentVerifiedTotalMs,
    recentVerifiedQueueLookupMs: next.recentVerifiedQueueLookupMs,
    recentHeapBytes: next.recentHeapBytes,
    soakCheckpoints: next.soakCheckpoints
  })).length;
  return next;
}

export function summarizePerformance(performance = {}, nowMs = Date.now()) {
  const recentVerifiedAt = appendRecentVerified(performance.recentVerifiedAt, nowMs, false);
  const startedAt = Number(performance.performanceStartedAtMs);
  const elapsedMinutes = Number.isFinite(startedAt)
    ? Math.max((nowMs - startedAt) / 60_000, 1 / 60)
    : null;
  const summary = {
    ...performance,
    recentVerifiedAt,
    docsPerMinute: recentVerifiedAt.length,
    sessionAvgDocsPerMinute: elapsedMinutes
      ? Math.max(0, Number(performance.verifiedAttempts || 0)) / elapsedMinutes
      : Number(performance.sessionAvgDocsPerMinute || 0)
  };
  summary.longRun = longRunState(summary);
  return summary;
}
