const AI_CONTEXT_SCHEMA_VERSION = 1;
const DEFAULT_LIMIT = 20_000;

function n(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function cleanText(value, max = 240) {
  const text = String(value || "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return text.slice(0, max);
}

function documentType(document) {
  const value = document?.extension || document?.contentKind || document?.actualMime || "";
  return cleanText(String(value).replace(/^\./, "").toUpperCase(), 32) || "DOSYA";
}

function documentDate(document) {
  return cleanText(
    document?.documentDate ||
    document?.verifiedAt ||
    document?.completedAt ||
    document?.updatedAt ||
    "",
    64
  ) || null;
}

function titleFor(document) {
  return cleanText(
    document?.documentTitle ||
    document?.displayName ||
    document?.originalFilename ||
    "Adsız evrak",
    240
  );
}

function isAiEligible(document) {
  return String(document?.status || "").toLowerCase() === "verified";
}

function sourceRef(index) {
  return `D${String(index + 1).padStart(6, "0")}`;
}

export function buildAiNativeBindings(report, {limit = DEFAULT_LIMIT} = {}) {
  if (!report || typeof report !== "object") throw new Error("AI_CONTEXT_REPORT_REQUIRED");
  const rawDocuments = Array.isArray(report.documents) ? report.documents : [];
  const cappedLimit = Math.max(1, Math.min(n(limit) || DEFAULT_LIMIT, DEFAULT_LIMIT));
  return rawDocuments
    .filter(isAiEligible)
    .slice(0, cappedLimit)
    .map((document, index) => ({
      sourceRef: sourceRef(index),
      sessionId: String(report?.session?.sessionId || report?.sessionId || ""),
      documentId: String(document?.documentId || "")
    }))
    .filter((item) => item.documentId);
}

export function buildAiContext(report, {limit = DEFAULT_LIMIT} = {}) {
  if (!report || typeof report !== "object") throw new Error("AI_CONTEXT_REPORT_REQUIRED");
  const rawDocuments = Array.isArray(report.documents) ? report.documents : [];
  const cappedLimit = Math.max(1, Math.min(n(limit) || DEFAULT_LIMIT, DEFAULT_LIMIT));
  const eligible = rawDocuments.filter(isAiEligible).slice(0, cappedLimit);
  const excluded = rawDocuments.length - eligible.length;

  const documents = eligible.map((document, index) => ({
    sourceRef: sourceRef(index),
    title: titleFor(document),
    documentType: documentType(document),
    documentDate: documentDate(document),
    sourceSystem: cleanText(document?.sourceSystem || report?.session?.sourceSystem || report?.sourceSystem || "UYAP", 32),
    archiveStatus: String(document?.status || "").toLowerCase(),
    physicalProof: "verified"
  }));

  return {
    schemaVersion: AI_CONTEXT_SCHEMA_VERSION,
    kind: "UYAP_AI_CONTEXT",
    generatedAt: new Date().toISOString(),
    session: {
      sessionId: cleanText(report?.session?.sessionId || report?.sessionId || "", 128) || null,
      sourceSystem: cleanText(report?.session?.sourceSystem || report?.sourceSystem || "UYAP", 32),
      total: n(report?.session?.total || report?.total),
      eligibleDocuments: documents.length,
      excludedDocuments: Math.max(0, excluded)
    },
    policy: {
      verifiedOnly: true,
      rawDocumentContentIncluded: false,
      absolutePathsIncluded: false,
      uyapDocumentIdsIncluded: false,
      citationsRequired: true,
      cloudUploadAllowedByDefault: false
    },
    documents
  };
}

export function getAiServiceStatus() {
  return {
    schemaVersion: 1,
    kind: "UYAP_AI_SERVICE_STATUS",
    configured: false,
    enabled: false,
    provider: null,
    mode: "disabled",
    reason: "AI_PROVIDER_NOT_CONFIGURED",
    rawDocumentUploadByDefault: false,
    citationsRequired: true
  };
}
