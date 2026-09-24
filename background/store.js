import {
  countByIndex,
  get,
  getAll,
  getAllByIndex,
  getFirstByIndex,
  getFirstByIndexRange,
  put,
  putMany,
  patchMany,
  update
} from "./db.js";
import { STATUS } from "../shared/constants.js";

function normalizeDocument(document) {
  const warnings = Array.isArray(document?.warnings) ? document.warnings : [];
  const retryPriority = document?.status === STATUS.RETRY_WAIT && document?.manualRetryPriority === true ? 0 : 1;
  return {
    ...document,
    warningFlag: warnings.length > 0 ? 1 : 0,
    retryPriority
  };
}

async function updateDocument(key, changes) {
  const normalizedChanges = { ...changes };
  if (Object.hasOwn(changes, "warnings")) {
    const warnings = Array.isArray(changes.warnings) ? changes.warnings : [];
    normalizedChanges.warningFlag = warnings.length > 0 ? 1 : 0;
  }
  if (Object.hasOwn(changes, "status")) {
    normalizedChanges.retryPriority = changes.status === STATUS.RETRY_WAIT && changes.manualRetryPriority === true ? 0 : 1;
  } else if (Object.hasOwn(changes, "manualRetryPriority")) {
    normalizedChanges.retryPriority = changes.manualRetryPriority === true ? 0 : 1;
  }
  return update("documents", key, normalizedChanges);
}

export const documentStore = {
  patchRuntimeMany: (documents) => patchMany("documents", documents.map(document => ({
    key:document.key,
    changes:Object.fromEntries(["tabId", "frameId", "runtimeDocumentId", "runtimeBindingMethod", "runtimeBoundAt", "sourceDocumentId", "sourceDocumentIdOrigin"]
      .filter(key => Object.hasOwn(document, key)).map(key => [key, document[key]]))
  }))),
  get: (key) => get("documents", key),
  put: (document) => put("documents", normalizeDocument(document)),
  putMany: (documents) => putMany("documents", documents.map(normalizeDocument)),
  async putManyChunked(documents, chunkSize = 500) {
    const safeSize = Math.max(50, Math.min(1000, Number(chunkSize) || 500));
    for (let offset = 0; offset < documents.length; offset += safeSize) {
      await putMany("documents", documents.slice(offset, offset + safeSize).map(normalizeDocument));
      // Yield between large IndexedDB commits so the MV3 worker and popup stay responsive.
      if (offset + safeSize < documents.length) await new Promise((resolve) => setTimeout(resolve, 0));
    }
  },
  update: updateDocument,
  bySession: (sessionId) => getAllByIndex("documents", "sessionId", sessionId),
  byStatus: (status) => getAllByIndex("documents", "status", status),
  bySessionStatus: (sessionId, status) => getAllByIndex("documents", "sessionStatus", [sessionId, status]),
  firstBySession: (sessionId) => getFirstByIndex("documents", "sessionId", sessionId),
  firstBySessionStatus: (sessionId, status) =>
    getFirstByIndex("documents", "sessionStatus", [sessionId, status]),
  nextQueued: (sessionId, largeBatchNo = null) =>
    Number.isInteger(largeBatchNo)
      ? getFirstByIndex("documents", "sessionBatchStatus", [sessionId, largeBatchNo, STATUS.QUEUED])
      : getFirstByIndex("documents", "sessionStatus", [sessionId, STATUS.QUEUED]),
  nextReadyManualRetry: (sessionId, now = new Date().toISOString()) =>
    getFirstByIndexRange(
      "documents",
      "sessionStatusRetryPriorityAt",
      [sessionId, STATUS.RETRY_WAIT, 0, ""],
      [sessionId, STATUS.RETRY_WAIT, 0, now]
    ),
  nextReadyRetry: (sessionId, now = new Date().toISOString(), largeBatchNo = null) =>
    Number.isInteger(largeBatchNo)
      ? getFirstByIndexRange(
          "documents",
          "sessionBatchStatusRetryAt",
          [sessionId, largeBatchNo, STATUS.RETRY_WAIT, ""],
          [sessionId, largeBatchNo, STATUS.RETRY_WAIT, now]
        )
      : getFirstByIndexRange(
          "documents",
          "sessionStatusRetryAt",
          [sessionId, STATUS.RETRY_WAIT, ""],
          [sessionId, STATUS.RETRY_WAIT, now]
        ),
  nextFutureRetry: (sessionId, now = new Date().toISOString(), largeBatchNo = null) =>
    Number.isInteger(largeBatchNo)
      ? getFirstByIndexRange(
          "documents",
          "sessionBatchStatusRetryAt",
          [sessionId, largeBatchNo, STATUS.RETRY_WAIT, now],
          [sessionId, largeBatchNo, STATUS.RETRY_WAIT, "\uffff"],
          { lowerOpen: true }
        )
      : getFirstByIndexRange(
          "documents",
          "sessionStatusRetryAt",
          [sessionId, STATUS.RETRY_WAIT, now],
          [sessionId, STATUS.RETRY_WAIT, "\uffff"],
          { lowerOpen: true }
        ),
  async hasActiveDownload(sessionId) {
    const awaiting = await getFirstByIndex(
      "documents",
      "sessionStatus",
      [sessionId, STATUS.AWAITING_BROWSER]
    );
    if (awaiting) return true;
    return Boolean(await getFirstByIndex(
      "documents",
      "sessionStatus",
      [sessionId, STATUS.DOWNLOADING]
    ));
  },
  countBySessionStatus: (sessionId, status) =>
    countByIndex("documents", "sessionStatus", [sessionId, status]),
  countBySessionBatchStatus: (sessionId, largeBatchNo, status) =>
    countByIndex("documents", "sessionBatchStatus", [sessionId, largeBatchNo, status]),
  firstBySessionStatusBatch: (sessionId, status, maxBatch = Number.MAX_SAFE_INTEGER) =>
    getFirstByIndexRange(
      "documents",
      "sessionStatusBatch",
      [sessionId, status, 0],
      [sessionId, status, maxBatch]
    ),
  firstBySessionStatusError: (sessionId, status, errorCode) =>
    getFirstByIndex("documents", "sessionStatusError", [sessionId, status, errorCode]),
  countBySessionStatusError: (sessionId, status, errorCode) =>
    countByIndex("documents", "sessionStatusError", [sessionId, status, errorCode]),
  async countWarnings(sessionId) {
    const [verified, skipped] = await Promise.all([
      countByIndex(
        "documents",
        "sessionStatusWarning",
        [sessionId, STATUS.VERIFIED, 1]
      ),
      countByIndex(
        "documents",
        "sessionStatusWarning",
        [sessionId, STATUS.SKIPPED, 1]
      )
    ]);
    return verified + skipped;
  },
  async byDownloadId(downloadId) {
    return getFirstByIndex("documents", "downloadId", downloadId);
  }
};

export const sessionStore = {
  get: (sessionId) => get("sessions", sessionId),
  all: () => getAll("sessions"),
  put: (session) => put("sessions", session),
  update: (sessionId, changes) => update("sessions", sessionId, changes)
};

export const diagnosticStore = {
  put: (event) => put("diagnosticEvents", event),
  bySession: (sessionId) => getAllByIndex("diagnosticEvents", "sessionId", sessionId),
  countBySession: (sessionId) => countByIndex("diagnosticEvents", "sessionId", sessionId)
};
