export function retryDelayForAttempt(attempts, delays) {
  const completedAttempts = Number.isFinite(attempts) ? Math.max(1, Math.trunc(attempts)) : 1;
  const retryIndex = completedAttempts - 1;
  return retryIndex < delays.length ? delays[retryIndex] : null;
}

export function retryDelayForError(errorCode, attempts, { defaultDelays = [], htmlDelays = [] } = {}) {
  return retryDelayForAttempt(attempts, errorCode === "HTML_RESPONSE" ? htmlDelays : defaultDelays);
}
