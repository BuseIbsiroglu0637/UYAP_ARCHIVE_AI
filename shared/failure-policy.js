export const ATTEMPT_STRATEGIES = Object.freeze({
  primary: Object.freeze({
    name: 'primary',
    freshRebind: false,
    allowFallback: true,
    nativePreferenceMs: 750,
    nativeOnlyWaitMs: 4_500,
    saveMenuTimeoutMs: 4_000,
    captureWindowMs: 550
  }),
  fresh_rebind: Object.freeze({
    name: 'fresh_rebind',
    freshRebind: true,
    allowFallback: true,
    nativePreferenceMs: 700,
    nativeOnlyWaitMs: 4_000,
    saveMenuTimeoutMs: 3_000,
    captureWindowMs: 900
  }),
  fallback_fast: Object.freeze({
    name: 'fallback_fast',
    freshRebind: false,
    allowFallback: true,
    nativePreferenceMs: 450,
    nativeOnlyWaitMs: 3_000,
    saveMenuTimeoutMs: 2_500,
    captureWindowMs: 700
  }),
  native_only: Object.freeze({
    name: 'native_only',
    // Transport-only retry. Runtime rebinding is deliberately not coupled to
    // this strategy: on very large (10k+) UYAP trees a full DOM rebind before
    // every native-only attempt turns an isolated correlation miss into an
    // O(n) session-wide operation.
    freshRebind: false,
    allowFallback: false,
    nativePreferenceMs: 0,
    nativeOnlyWaitMs: 4_500,
    saveMenuTimeoutMs: 3_500,
    captureWindowMs: 500
  })
});

const INCOMPLETE_CONTENT_ERRORS = new Set([
  'TRUNCATED_PDF',
  'TRUNCATED_TIFF',
  'DOWNLOAD_SIZE_MISMATCH',
  'DOWNLOAD_NOT_COMPLETE',
  'SOURCE_FILE_MISSING'
]);


const ISOLATED_CONTENT_ERRORS = new Set([
  'UNSUPPORTED_FORMAT',
  'SIGNATURE_MISMATCH',
  'INVALID_IMAGE',
  'INVALID_UDF_ENTRIES',
  'INVALID_UDF_XML',
  'UDF_LIMIT_EXCEEDED',
  'ZIP_CRC_FAILED',
  'ZIP_ENCRYPTED',
  'ZIP_LIMIT_EXCEEDED',
  'ZIP_UNSAFE_MEMBER',
  'PDF_TRAILING_DATA'
]);

export function isIsolatedContentError(errorCode) {
  return ISOLATED_CONTENT_ERRORS.has(String(errorCode || ''));
}

export function contentEndpointCooldownMs(cooldownCount = 0) {
  const count = Math.max(0, Math.trunc(Number(cooldownCount) || 0));
  return [10_000, 20_000, 30_000, 60_000][Math.min(count, 3)];
}

const RETRYABLE_STRATEGY_ERRORS = new Set([
  'HTML_RESPONSE',
  'DOWNLOAD_INTERRUPTED',
  'DOWNLOAD_STALLED',
  'DOWNLOAD_START_FAILED',
  'NATIVE_DOWNLOAD_NOT_CORRELATED',
  'CLICK_DOWNLOAD_TIMEOUT',
  'INVALID_CONTENT',
  ...INCOMPLETE_CONTENT_ERRORS
]);

export function isIncompleteContentError(errorCode) {
  return INCOMPLETE_CONTENT_ERRORS.has(String(errorCode || ''));
}

export function retryStrategySupported(errorCode) {
  return RETRYABLE_STRATEGY_ERRORS.has(errorCode);
}

function normalizedHistory(document = {}) {
  return (Array.isArray(document.retryStrategyHistory) ? document.retryStrategyHistory : [])
    .map(String)
    .filter((name) => ATTEMPT_STRATEGIES[name] && name !== 'primary')
    .slice(-3);
}

export function attemptStrategyForDocument(document = {}) {
  const name = ATTEMPT_STRATEGIES[document.retryStrategy]
    ? document.retryStrategy
    : 'primary';
  return ATTEMPT_STRATEGIES[name];
}

export function nextRetryStrategy(document = {}, errorCode = null) {
  if (!RETRYABLE_STRATEGY_ERRORS.has(errorCode)) return null;
  const used = new Set(normalizedHistory(document));
  const binding = String(document.bindingMethod || '');

  let candidates;
  if (errorCode === 'HTML_RESPONSE') {
    // If the extension-replayed fallback produced HTML, prefer the real UYAP
    // browser/native path next. If the native path produced HTML, try the
    // captured fallback next. A fresh DOM rebind remains the third option.
    candidates = binding === 'extension-download-id'
      ? ['native_only', 'fresh_rebind', 'fallback_fast']
      : /^native-|browser|canonical/i.test(binding)
        ? ['fallback_fast', 'fresh_rebind', 'native_only']
        : ['fresh_rebind', 'fallback_fast', 'native_only'];
  } else if (INCOMPLETE_CONTENT_ERRORS.has(errorCode)) {
    // A browser-complete file can still be structurally truncated. Do not
    // repeat the same transport path: switch transport first, then rebind.
    candidates = binding === 'extension-download-id'
      ? ['native_only', 'fresh_rebind', 'fallback_fast']
      : /^native-|browser|canonical/i.test(binding)
        ? ['fallback_fast', 'fresh_rebind', 'native_only']
        : ['fresh_rebind', 'native_only', 'fallback_fast'];
  } else if (['NATIVE_DOWNLOAD_NOT_CORRELATED', 'CLICK_DOWNLOAD_TIMEOUT', 'DOWNLOAD_START_FAILED'].includes(errorCode)) {
    // Correlation misses usually mean Chrome did not expose enough download
    // metadata in time; they do not prove the 10k+ document DOM binding is
    // stale. Switch transport first and reserve a targeted rebind for the last
    // recovery strategy.
    candidates = ['fallback_fast', 'native_only', 'fresh_rebind'];
  } else {
    candidates = ['fallback_fast', 'fresh_rebind', 'native_only'];
  }

  return candidates.find((name) => !used.has(name)) || null;
}

export function appendRetryStrategyHistory(document = {}, strategyName = null) {
  if (!strategyName || !ATTEMPT_STRATEGIES[strategyName] || strategyName === 'primary') {
    return normalizedHistory(document);
  }
  return [...normalizedHistory(document).filter((name) => name !== strategyName), strategyName].slice(-3);
}

export function retryQueuePolicy(readyRetry = {}) {
  if (readyRetry.manualRetryPriority === true || Number(readyRetry.retryPriority) === 0) {
    return { successesRequired: 0, maxDeferredMs: 0, deferUntilPrimaryExhausted: false };
  }
  const isManualReview = readyRetry.retryClass === 'manual_review';
  const isHtml = readyRetry.errorCode === 'HTML_RESPONSE' || readyRetry.retryClass === 'deferred_html';
  const isStructural = ['DOWNLOAD_INTERRUPTED','DOWNLOAD_STALLED','DOWNLOAD_START_FAILED','NATIVE_DOWNLOAD_NOT_CORRELATED','CLICK_DOWNLOAD_TIMEOUT'].includes(readyRetry.errorCode);
  const isIncomplete = INCOMPLETE_CONTENT_ERRORS.has(readyRetry.errorCode);
  // HTML from one document is not evidence that the entire 12k queue is unhealthy.
  // Keep one retry for the end of the fresh inventory instead of interrupting healthy work.
  if (isManualReview || isHtml) return { successesRequired: Number.POSITIVE_INFINITY, maxDeferredMs: Number.POSITIVE_INFINITY, deferUntilPrimaryExhausted: true };
  if (isStructural || isIncomplete) return { successesRequired: 20, maxDeferredMs: 5 * 60_000, deferUntilPrimaryExhausted: false };
  return { successesRequired: 12, maxDeferredMs: 3 * 60_000, deferUntilPrimaryExhausted: false };
}

export function htmlRetryDecision(document = {}) {
  const recoveryAttempts = Math.max(0, Number(document.htmlRecoveryAttempts || 0));
  const attemptWasDeferred = document.attemptRecoveryClass === 'deferred_html';
  const attemptWasManualReview = document.attemptRecoveryClass === 'manual_review';
  // A healthy 12k run must not recycle the same HTML-producing record through
  // multiple transport strategies. The first HTML response is deferred behind
  // untouched inventory; that deferred pass is the one and only automatic
  // recovery attempt. A user-triggered review retry is also a single attempt:
  // if UYAP returns HTML again, the record must return to user review instead
  // of silently entering another automatic retry cycle.
  if (attemptWasDeferred || attemptWasManualReview || recoveryAttempts >= 1) {
    return { retry: false, recoveryAttempts };
  }
  return { retry: true, recoveryAttempts };
}
