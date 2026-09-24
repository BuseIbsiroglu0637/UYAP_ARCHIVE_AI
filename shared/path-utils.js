const RESERVED_NAMES = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i;
const INVALID_PATH_CHARS = /[<>:"/\\|?*\u0000-\u001f]/g;
const MIME_EXTENSIONS = Object.freeze({
  "application/pdf": ".pdf",
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/pjpeg": ".jpg",
  "image/png": ".png",
  "image/gif": ".gif",
  "image/bmp": ".bmp",
  "image/x-ms-bmp": ".bmp",
  "image/tif": ".tif",
  "image/tiff": ".tif",
  "application/tif": ".tif",
  "application/tiff": ".tif"
});

export function sanitizePathSegment(value, maxLength = 80) {
  let result = String(value ?? "")
    .normalize("NFC")
    .replace(INVALID_PATH_CHARS, "_")
    .replace(/\s+/g, " ")
    .replace(/[. ]+$/g, "")
    .trim();

  if (!result) result = "Adsiz";
  if (RESERVED_NAMES.test(result)) result = `_${result}`;

  return result.slice(0, maxLength).replace(/[. ]+$/g, "") || "Adsiz";
}

export function normalizeExtension(value, fallback = ".udf") {
  const raw = String(value ?? "").trim().toLowerCase();
  if (!raw) return fallback;
  const extension = raw.startsWith(".") ? raw : `.${raw}`;
  return /^\.[a-z0-9]{1,10}$/.test(extension) ? extension : fallback;
}

export function isTechnicalFolderLabel(value) {
  const label = String(value ?? "").trim();
  return /^evrak-[a-f0-9]{12,64}$/i.test(label);
}

export function buildTargetPath(document) {
  const court = sanitizePathSegment(document.courtName || document.institutionName || "Mahkeme_Bilinmiyor");
  const isKurum = String(document.sourceSystem || '').toUpperCase() === 'UYAP_KURUM';
  const rawCaseId = document.caseId || (isKurum ? 'Kurum_Toplu_Arsiv' : 'Dosya_Bilinmiyor');
  const caseFolder = sanitizePathSegment(isKurum
    ? (/^Kurum_Arsiv_/i.test(String(rawCaseId)) ? 'Kurum_Toplu_Arsiv' : `${rawCaseId}_Dosya`)
    : `${rawCaseId}_Esas`);
  const rawFolders = Array.isArray(document.folderNames) ? [...document.folderNames] : [];
  if (document.containerLabel && !rawFolders.some((part) => String(part).trim() === String(document.containerLabel).trim())) {
    rawFolders.unshift(document.containerLabel);
  }
  const folderNames = rawFolders
    .filter((part) => part && !isTechnicalFolderLabel(part))
    .map((part) => sanitizePathSegment(part));

  // Prefer the numeric UYAP source id once it becomes known. Internal d-<hash>
  // ids remain a safe fallback for inventory records that never reveal one.
  const identity = document.sourceDocumentId || document.documentId || "Belirsiz";
  const id = sanitizePathSegment(identity, 72);
  const date = document.documentDate && document.documentDate !== "Tarih_Bilinmiyor"
    ? document.documentDate
    : null;
  const title = sanitizePathSegment([date, document.documentTitle].filter(Boolean).join("_"), Math.max(24, 139 - id.length));
  const baseName = `${title}_${id}`;

  return [
    "UYAP_Arsiv",
    court,
    caseFolder,
    ...folderNames,
    `${baseName}${normalizeExtension(document.extension, ".bin")}`
  ].join("/");
}

export function inferExtension(filenameOrUrl, mimeType = null) {
  const clean = String(filenameOrUrl ?? "").split(/[?#]/, 1)[0].replaceAll("\\", "/");
  const match = clean.match(/\.([a-z0-9]{1,10})$/i);
  if (match) return normalizeExtension(match[1], null);

  const mime = String(mimeType ?? "").split(";", 1)[0].trim().toLowerCase();
  return MIME_EXTENSIONS[mime] ?? null;
}

export function detectDownloadExtension(downloadItem) {
  const fromFilename = inferExtension(downloadItem?.filename);
  if (fromFilename) return { extension: fromFilename, source: "original_filename" };

  const fromMime = inferExtension(null, downloadItem?.mime);
  if (fromMime) return { extension: fromMime, source: "mime_type" };

  const fromFinalUrl = inferExtension(downloadItem?.finalUrl);
  if (fromFinalUrl) return { extension: fromFinalUrl, source: "final_url" };

  const fromUrl = inferExtension(downloadItem?.url);
  if (fromUrl) return { extension: fromUrl, source: "url" };

  return { extension: ".bin", source: "unknown" };
}

export function replacePathExtension(path, extension) {
  const normalized = normalizeExtension(extension, ".bin");
  const value = String(path ?? "");
  const slashIndex = value.lastIndexOf("/");
  const directory = slashIndex >= 0 ? value.slice(0, slashIndex + 1) : "";
  const filename = slashIndex >= 0 ? value.slice(slashIndex + 1) : value;
  const withoutExtension = filename.replace(/\.[a-z0-9]{1,10}$/i, "");
  return `${directory}${withoutExtension || "Adsiz"}${normalized}`;
}

export function buildStagingPath(sessionId, documentId) {
  return [
    "UYAP_Arsiv",
    "_incoming",
    sanitizePathSegment(sessionId, 64),
    `${sanitizePathSegment(documentId, 120)}.part`
  ].join("/");
}

export function buildUnmatchedPath(sessionId, attemptId, originalFilename = null, extension = ".bin") {
  const rawBase = String(originalFilename ?? "").replaceAll("\\", "/").split("/").at(-1) || "evrak";
  const detected = inferExtension(rawBase) || normalizeExtension(extension, ".bin");
  const stem = sanitizePathSegment(rawBase.replace(/\.[a-z0-9]{1,10}$/i, "") || "evrak", 96);
  return [
    "UYAP_Arsiv",
    "_unmatched",
    sanitizePathSegment(sessionId, 64),
    `${sanitizePathSegment(attemptId, 80)}__${stem}${detected}`
  ].join("/");
}
