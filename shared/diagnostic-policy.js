export const DIAGNOSTIC_SCHEMA_VERSION = 2;

const LEVELS = new Set(["debug", "info", "warn", "error"]);
const SENSITIVE_KEY = /(?:sourceDocumentId|documentId|caseId|caseNumber|court|institution|fileName|filename|filePath|path|url|token|authorization|cookie|query|payload|rawContent|documentTitle)/i;

function boundedNumber(value, {min = 0, max = Number.MAX_SAFE_INTEGER} = {}) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.min(max, Math.max(min, number));
}

export function sanitizeDiagnosticText(value, maxLength = 400) {
  const limit = Math.max(32, Math.min(4000, Number(maxLength) || 400));
  return String(value ?? "")
    .replace(/https?:\/\/[^\s"'<>]+/gi, "[URL]")
    .replace(/\b[A-Za-z]:[\\/][^\r\n\t|]+/g, "[PATH]")
    .replace(/\b(?:access_token|refresh_token|id_token|api[_-]?key|token)=([^\s&]+)/gi, (match) => match.split("=")[0] + "=[REDACTED]")
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{16,}/gi, "Bearer [REDACTED]")
    .replace(/\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_.-]{10,}\.[A-Za-z0-9_.-]{10,}\b/g, "[TOKEN]")
    .replace(/\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b/g, "[EMAIL]")
    .replace(/\b\d{4}\/\d{1,8}\b/g, "[CASE]")
    .replace(/\b\d{8,30}\b/g, "[ID]")
    .slice(0, limit);
}

export async function diagnosticReference(scope, value) {
  const encoder = new TextEncoder();
  const input = encoder.encode(String(scope ?? "") + "\u0000" + String(value ?? ""));
  const digest = await crypto.subtle.digest("SHA-256", input);
  const hex = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return "ref_" + hex.slice(0, 20);
}

export function sanitizeDiagnosticMetadata(metadata = {}, maxKeys = 32) {
  const result = {};
  const entries = Object.entries(metadata && typeof metadata === "object" ? metadata : {}).slice(0, Math.max(1, Math.min(64, Number(maxKeys) || 32)));
  for (const [rawKey, value] of entries) {
    const key = String(rawKey || "").slice(0, 64);
    if (!key || (SENSITIVE_KEY.test(key) && key !== "documentRef")) continue;
    if (value === undefined || value === null) continue;

    if (typeof value === "string") {
      result[key] = sanitizeDiagnosticText(value, 500);
    } else if (typeof value === "number") {
      if (Number.isFinite(value)) result[key] = value;
    } else if (typeof value === "boolean") {
      result[key] = value;
    } else if (Array.isArray(value)) {
      result[key] = value.slice(0, 12).map((item) => sanitizeDiagnosticText(item, 120));
    }
  }
  return result;
}

export function normalizeDiagnosticEvent({
  timestamp = new Date().toISOString(),
  level = "info",
  component = "unknown",
  event = "UNKNOWN_EVENT",
  release = null,
  sessionRef = null,
  errorCode = null,
  durationMs = null,
  retryCount = null,
  metadata = {}
} = {}) {
  const normalizedLevel = LEVELS.has(String(level)) ? String(level) : "info";
  const normalizedEvent = String(event || "UNKNOWN_EVENT").replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 96);
  const normalizedComponent = String(component || "unknown").replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 64);
  const normalizedErrorCode = errorCode
    ? String(errorCode).replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 96)
    : null;

  return {
    schemaVersion: DIAGNOSTIC_SCHEMA_VERSION,
    timestamp: String(timestamp || new Date().toISOString()).slice(0, 40),
    level: normalizedLevel,
    component: normalizedComponent,
    event: normalizedEvent,
    release: release ? sanitizeDiagnosticText(release, 120) : null,
    sessionRef: sessionRef ? sanitizeDiagnosticText(sessionRef, 80) : null,
    errorCode: normalizedErrorCode,
    durationMs: durationMs === null ? null : boundedNumber(durationMs, {max: 7 * 24 * 60 * 60 * 1000}),
    retryCount: retryCount === null ? null : boundedNumber(retryCount, {max: 100000}),
    metadata: sanitizeDiagnosticMetadata(metadata)
  };
}
