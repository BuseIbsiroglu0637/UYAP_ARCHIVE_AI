import {normalizeDiagnosticEvent, sanitizeDiagnosticText} from "../shared/diagnostic-policy.js";

export function logOperational(level, event, {
  component = "service-worker",
  error = null,
  errorCode = null,
  durationMs = null,
  retryCount = null,
  metadata = {}
} = {}) {
  const manifest = chrome.runtime.getManifest();
  const safeMetadata = {...(metadata || {})};
  if (error) {
    safeMetadata.error = sanitizeDiagnosticText(error instanceof Error ? error.message : error, 240);
  }

  const record = normalizeDiagnosticEvent({
    level,
    component,
    event,
    release: manifest.version_name || manifest.version || null,
    errorCode,
    durationMs,
    retryCount,
    metadata: safeMetadata
  });

  const method = record.level === "error" ? "error" : record.level === "warn" ? "warn" : "info";
  console[method]("[UYAP]", JSON.stringify(record));
  return record;
}

export function logOperationalError(event, error, options = {}) {
  const candidateCode = options.errorCode
    || (error && typeof error === "object" && "code" in error ? error.code : null)
    || null;
  return logOperational("error", event, {...options, error, errorCode:candidateCode});
}
