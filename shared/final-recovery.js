export const FINAL_RECOVERY_MAX_PASSES = 2;

const RECOVERABLE_COMPLETION_ERRORS = new Set([
  'HTML_RESPONSE',
  'HTML_RESPONSE_RECEIVED',
  'DOWNLOAD_INTERRUPTED',
  'DOWNLOAD_STALLED',
  'DOWNLOAD_START_FAILED',
  'NATIVE_DOWNLOAD_NOT_CORRELATED',
  'CLICK_DOWNLOAD_TIMEOUT',
  'DOWNLOAD_SIZE_MISMATCH',
  'DOWNLOAD_NOT_COMPLETE',
  'TRUNCATED_PDF',
  'TRUNCATED_TIFF',
  'FILE_NOT_CONFIRMED',
  'RECOVERED_STALE_DOWNLOAD',
  'ARCHIVE_INTEGRITY_FAILED'
]);

export function finalRecoveryLimit(total) {
  const count = Math.max(0, Number(total || 0));
  return Math.max(10, Math.min(100, Math.ceil(count * 0.02)));
}

export function isRecoverableCompletionError(errorCode) {
  return RECOVERABLE_COMPLETION_ERRORS.has(String(errorCode || ''));
}

export function planFinalRecovery({
  documents = [],
  total = documents.length,
  pass = 0,
  candidateIds = null,
  integrity = null,
  mode = 'unresolved'
} = {}) {
  const currentPass = Math.max(0, Number(pass || 0));
  const maxPasses = FINAL_RECOVERY_MAX_PASSES;
  if (currentPass >= maxPasses) {
    return {allowed:false, reason:'PASS_LIMIT', currentPass, nextPass:currentPass, maxPasses, limit:finalRecoveryLimit(total), documents:[]};
  }

  const limit = finalRecoveryLimit(total);
  const byId = new Map((documents || []).map(document => [String(document.documentId || ''), document]));
  const fingerprintMismatch = new Set((integrity?.fingerprintMismatchIds || []).map(String));
  let candidates = [];

  if (candidateIds) {
    const uniqueIds = [...new Set(candidateIds.map(String).filter(Boolean))];
    candidates = uniqueIds
      .filter(id => !fingerprintMismatch.has(id))
      .map(id => byId.get(id))
      .filter(Boolean);
  } else {
    candidates = (documents || []).filter(document =>
      ['failed', 'review_required'].includes(String(document.status || '')) &&
      isRecoverableCompletionError(document.errorCode)
    );
  }

  if (!candidates.length) {
    return {allowed:false, reason:'NO_RECOVERABLE_DOCUMENTS', currentPass, nextPass:currentPass, maxPasses, limit, documents:[]};
  }
  if (candidates.length > limit) {
    return {allowed:false, reason:'SAFETY_LIMIT', currentPass, nextPass:currentPass, maxPasses, limit, documents:candidates};
  }

  return {
    allowed:true,
    reason:null,
    mode,
    currentPass,
    nextPass:currentPass + 1,
    maxPasses,
    limit,
    documents:candidates
  };
}
