function isUyapHttpsUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:") {
      return url.hostname === "uyap.gov.tr" || url.hostname.endsWith(".uyap.gov.tr");
    }
    // Browser-generated blob downloads preserve the page origin in URL.origin.
    if (url.protocol === "blob:" && url.origin?.startsWith("https://")) {
      const origin = new URL(url.origin);
      return origin.hostname === "uyap.gov.tr" || origin.hostname.endsWith(".uyap.gov.tr");
    }
    return false;
  } catch {
    return false;
  }
}

export function isUyapDownload(item) {
  if (item.byExtensionId) return false;
  return [item.url, item.finalUrl, item.referrer].some(isUyapHttpsUrl);
}


export function isLateDuplicateUyapDownload(item, record, nowMs = Date.now()) {
  if (!record || !isUyapDownload(item)) return false;
  const expected = String(record.sourceDocumentId || '').trim();
  if (!/^\d{4,30}$/.test(expected)) return false;
  const raw = String(item?.filename || '').replaceAll('\\','/');
  const basename = raw.split('/').at(-1) || '';
  const match = basename.match(/^evrak[_ -]?(\d{4,30})(?:[._ -]|$)/i);
  if (match?.[1] !== expected) return false;
  const createdAt = Number(record.createdAt || 0);
  if (!Number.isFinite(createdAt) || nowMs - createdAt < -500 || nowMs - createdAt > 30_000) return false;
  const startedAt = Date.parse(item?.startTime || '');
  if (Number.isFinite(startedAt) && Math.abs(startedAt - createdAt) > 30_000) return false;
  return true;
}
