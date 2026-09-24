import { DB_NAME, DB_VERSION, STATUS } from "../shared/constants.js";

let databasePromise;

function requestAsPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transactionDone(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
  });
}

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

export function openDatabase() {
  if (databasePromise) return databasePromise;

  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      const db = request.result;
      const transaction = request.transaction;

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

      // Older records did not have warningFlag/retryPriority. Populate both
      // in-place so summaries stay indexed and user-requested deferred retries
      // can be prioritized without scanning a 12k-document session.
      if (request.oldVersion < 5) {
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
    };

    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => {
        db.close();
        databasePromise = undefined;
      };
      resolve(db);
    };
    request.onerror = () => {
      databasePromise = undefined;
      reject(request.error);
    };
    request.onblocked = () => {
      // Existing extension contexts will close on versionchange. Keeping the
      // promise pending is safer than opening a second incompatible handle.
    };
  });

  return databasePromise;
}

export async function put(storeName, value) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readwrite");
  transaction.objectStore(storeName).put(value);
  await transactionDone(transaction);
  return value;
}

export async function putMany(storeName, values) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readwrite");
  const store = transaction.objectStore(storeName);
  for (const value of values) store.put(value);
  await transactionDone(transaction);
}

export async function get(storeName, key) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  return requestAsPromise(transaction.objectStore(storeName).get(key));
}

export async function getAll(storeName) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  return requestAsPromise(transaction.objectStore(storeName).getAll());
}

export async function getAllByIndex(storeName, indexName, value) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  const index = transaction.objectStore(storeName).index(indexName);
  return requestAsPromise(index.getAll(IDBKeyRange.only(value)));
}

export async function getFirstByIndex(storeName, indexName, value) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  const index = transaction.objectStore(storeName).index(indexName);
  const cursor = await requestAsPromise(index.openCursor(IDBKeyRange.only(value), "next"));
  return cursor?.value ?? null;
}

export async function getFirstByIndexRange(
  storeName,
  indexName,
  lower,
  upper,
  { lowerOpen = false, upperOpen = false, direction = "next" } = {}
) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  const index = transaction.objectStore(storeName).index(indexName);
  const range = IDBKeyRange.bound(lower, upper, lowerOpen, upperOpen);
  const cursor = await requestAsPromise(index.openCursor(range, direction));
  return cursor?.value ?? null;
}

export async function countByIndex(storeName, indexName, value) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readonly");
  const index = transaction.objectStore(storeName).index(indexName);
  return requestAsPromise(index.count(IDBKeyRange.only(value)));
}

export async function update(storeName, key, changes) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readwrite");
  const done = transactionDone(transaction);
  const store = transaction.objectStore(storeName);
  let next = null;
  const request = store.get(key);
  request.onsuccess = () => {
    if (!request.result) return;
    next = { ...request.result, ...changes, updatedAt: new Date().toISOString() };
    store.put(next);
  };
  await done;
  return next;
}

// Merge field patches against current rows in one transaction. In particular,
// a slow DOM scan must never write its old QUEUED status over a new local proof.
export async function patchMany(storeName, patches) {
  const db = await openDatabase();
  const transaction = db.transaction(storeName, "readwrite");
  const done = transactionDone(transaction);
  const store = transaction.objectStore(storeName);
  for (const {key, changes} of patches) {
    const request = store.get(key);
    request.onsuccess = () => {
      if (request.result) store.put({...request.result, ...changes, updatedAt:new Date().toISOString()});
    };
  }
  await done;
}
