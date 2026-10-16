import { STATUS } from "../shared/constants.js";

export const DB_MIGRATION_FRAMEWORK_VERSION = 1;
export const CURRENT_DB_VERSION = 6;
export const MIN_SUPPORTED_SOURCE_VERSION = 0;

function ensureIndex(store, name, keyPath, options = { unique: false }) {
  if (!store.indexNames.contains(name)) {
    store.createIndex(name, keyPath, options);
  }
}

function normalizeLegacyDocument(record) {
  const warnings = Array.isArray(record?.warnings) ? record.warnings : [];
  const warningFlag = warnings.length > 0 ? 1 : 0;
  const retryPriority = record?.status === STATUS.RETRY_WAIT && record?.manualRetryPriority === true ? 0 : 1;
  const changed = record?.warningFlag !== warningFlag || record?.retryPriority !== retryPriority;
  if (!changed) return null;
  return { ...record, warningFlag, retryPriority };
}

export function describeMigrationPath(oldVersion, newVersion = CURRENT_DB_VERSION) {
  const from = Number(oldVersion ?? 0);
  const to = Number(newVersion ?? CURRENT_DB_VERSION);
  if (!Number.isInteger(from) || from < MIN_SUPPORTED_SOURCE_VERSION) {
    return {supported:false, from, to, reason:"INVALID_SOURCE_VERSION", steps:[]};
  }
  if (!Number.isInteger(to) || to !== CURRENT_DB_VERSION) {
    return {supported:false, from, to, reason:"UNKNOWN_TARGET_VERSION", steps:[]};
  }
  if (from > to) {
    return {supported:false, from, to, reason:"DOWNGRADE_NOT_SUPPORTED", steps:[]};
  }

  const steps = ["ensure-current-stores-and-indexes"];
  if (from < 5) steps.unshift("normalize-document-derived-index-fields");
  return {
    supported: true,
    frameworkVersion: DB_MIGRATION_FRAMEWORK_VERSION,
    from,
    to,
    destructive: false,
    requiresBackup: false,
    steps
  };
}

export function applyDatabaseUpgrade({db, transaction, oldVersion = 0, newVersion = CURRENT_DB_VERSION}) {
  if (!db || !transaction) throw new Error("DB_MIGRATION_CONTEXT_REQUIRED");
  const plan = describeMigrationPath(oldVersion, newVersion);
  if (!plan.supported) throw new Error(`DB_MIGRATION_UNSUPPORTED:${plan.reason}:${plan.from}->${plan.to}`);

  const documents = db.objectStoreNames.contains("documents")
    ? transaction.objectStore("documents")
    : db.createObjectStore("documents", { keyPath: "key" });

  ensureIndex(documents, "sessionId", "sessionId");
  ensureIndex(documents, "status", "status");
  ensureIndex(documents, "downloadId", "downloadId");
  ensureIndex(documents, "sessionStatus", ["sessionId", "status"]);
  ensureIndex(documents, "sessionStatusRetryAt", ["sessionId", "status", "retryAt"]);
  ensureIndex(documents, "sessionStatusRetryPriorityAt", ["sessionId", "status", "retryPriority", "retryAt"]);
  ensureIndex(documents, "sessionStatusWarning", ["sessionId", "status", "warningFlag"]);
  ensureIndex(documents, "sessionStatusError", ["sessionId", "status", "errorCode"]);
  ensureIndex(documents, "sessionBatchStatus", ["sessionId", "largeBatchNo", "status"]);
  ensureIndex(documents, "sessionStatusBatch", ["sessionId", "status", "largeBatchNo"]);
  ensureIndex(documents, "sessionBatchStatusRetryAt", ["sessionId", "largeBatchNo", "status", "retryAt"]);
  ensureIndex(documents, "sessionBatchStatusRetryPriorityAt", ["sessionId", "largeBatchNo", "status", "retryPriority", "retryAt"]);

  if (Number(oldVersion) < 5) {
    const cursorRequest = documents.openCursor();
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result;
      if (!cursor) return;
      const normalized = normalizeLegacyDocument(cursor.value);
      if (normalized) cursor.update(normalized);
      cursor.continue();
    };
  }

  const sessions = db.objectStoreNames.contains("sessions")
    ? transaction.objectStore("sessions")
    : db.createObjectStore("sessions", { keyPath: "sessionId" });
  ensureIndex(sessions, "updatedAt", "updatedAt");

  const diagnosticEvents = db.objectStoreNames.contains("diagnosticEvents")
    ? transaction.objectStore("diagnosticEvents")
    : db.createObjectStore("diagnosticEvents", { keyPath: "id", autoIncrement: true });
  ensureIndex(diagnosticEvents, "sessionId", "sessionId");
  ensureIndex(diagnosticEvents, "sessionAt", ["sessionId", "atMs"]);

  return plan;
}
