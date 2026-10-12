import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const policySource = fs.readFileSync(path.join(ROOT, "shared", "diagnostic-policy.js"), "utf8");
const policyUrl = "data:text/javascript;base64," + Buffer.from(policySource, "utf8").toString("base64");
const {
  DIAGNOSTIC_SCHEMA_VERSION,
  normalizeDiagnosticEvent,
  sanitizeDiagnosticMetadata,
  sanitizeDiagnosticText
} = await import(policyUrl);

assert.equal(DIAGNOSTIC_SCHEMA_VERSION, 2);

const sample = sanitizeDiagnosticText(
  "https://kurum.uyap.gov.tr/doc?id=123456789012 C:\\Users\\Buse\\UYAP_Arsiv\\evrak.pdf token=abcdefghijklmnopqrstuvwxyz123456 2026/12345 user@example.com"
);
assert(!sample.includes("kurum.uyap.gov.tr"), "URL leaked");
assert(!sample.includes("Users\\Buse"), "Windows path leaked");
assert(!sample.includes("123456789012"), "long numeric identifier leaked");
assert(!sample.includes("2026/12345"), "case number leaked");
assert(!sample.includes("user@example.com"), "email leaked");
assert(!sample.includes("abcdefghijklmnopqrstuvwxyz123456"), "token leaked");

const metadata = sanitizeDiagnosticMetadata({
  documentId: "987654321012345",
  sourceDocumentId: "987654321012345",
  fileName: "secret.pdf",
  filePath: "C:\\secret\\secret.pdf",
  url: "https://uyap.gov.tr/?token=secret",
  documentRef: "abc123",
  downloadId: 42,
  retryCount: 2,
  mode: "recovery"
});
assert.equal(metadata.documentId, undefined);
assert.equal(metadata.sourceDocumentId, undefined);
assert.equal(metadata.fileName, undefined);
assert.equal(metadata.filePath, undefined);
assert.equal(metadata.url, undefined);
assert.equal(metadata.documentRef, "abc123");
assert.equal(metadata.downloadId, 42);
assert.equal(metadata.retryCount, 2);
assert.equal(metadata.mode, "recovery");

const event = normalizeDiagnosticEvent({
  level: "error",
  component: "archive-engine",
  event: "DOWNLOAD_FAILED",
  release: "2.9.0 RC15.9.38",
  errorCode: "NATIVE_OPERATION_FAILED",
  durationMs: 1530,
  retryCount: 1,
  metadata: {
    documentId: "987654321012345",
    detail: "https://uyap.gov.tr/x token=abcdefghijklmnopqrstuvwxyz123456",
    documentRef: "abc123"
  }
});
assert.equal(event.schemaVersion, 2);
assert.equal(event.level, "error");
assert.equal(event.component, "archive-engine");
assert.equal(event.event, "DOWNLOAD_FAILED");
assert.equal(event.errorCode, "NATIVE_OPERATION_FAILED");
assert.equal(event.metadata.documentId, undefined);
assert.equal(event.metadata.documentRef, "abc123");
assert(!event.metadata.detail.includes("uyap.gov.tr"));
assert(!event.metadata.detail.includes("abcdefghijklmnopqrstuvwxyz123456"));

const environments = JSON.parse(fs.readFileSync(path.join(ROOT, "config", "environments.json"), "utf8"));
for (const [name, profile] of Object.entries(environments.profiles)) {
  assert.equal(profile.diagnostics.schemaVersion, 2, name + ": diagnostics schema");
  assert.equal(profile.diagnostics.includeRawLegalData, false, name + ": raw legal data");
  assert.equal(profile.diagnostics.cloudUploadByDefault, false, name + ": cloud upload default");
  assert(profile.diagnostics.maxEventPayloadBytes <= 4096, name + ": payload limit");
}

const manager = fs.readFileSync(path.join(ROOT, "background", "download-manager.js"), "utf8");
assert(manager.includes("UYAP_PRIVACY_LIGHT_DIAGNOSTIC"));
assert(manager.includes("DIAGNOSTIC_SCHEMA_VERSION"));
assert(manager.includes("sanitizeDiagnosticMetadata"));
assert(manager.includes("documentRef = await diagnosticReference"));

const worker = fs.readFileSync(path.join(ROOT, "background", "service-worker.js"), "utf8");
assert(worker.includes("logOperationalError"));
assert(!worker.includes('console.error("UYAP kuyruk kurtarma hatası"'));
assert(!worker.includes('console.error("UYAP operasyon alarmı işlenemedi"'));

console.log("Phase 1 structured logging and privacy-light diagnostics: PASS");
