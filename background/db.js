import { DB_NAME, DB_VERSION } from "../shared/constants.js";
import { applyDatabaseUpgrade } from "./db-migrations.js";

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

export function openDatabase() {
  if (databasePromise) return databasePromise;

  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      applyDatabaseUpgrade({
        db: request.result,
        transaction: request.transaction,
        oldVersion: request.oldVersion,
        newVersion: request.newVersion || DB_VERSION
      });
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
