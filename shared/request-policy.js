export function validateDownloadRequest(request) {
  if (!request || typeof request.url !== 'string') throw new Error('SAVE_REQUEST_NOT_CAPTURED');
  let url;
  try { url = new URL(request.url); } catch { throw new Error('INVALID_DOWNLOAD_URL'); }
  if (url.protocol !== 'https:' || url.username || url.password ||
      !(url.hostname === 'uyap.gov.tr' || url.hostname.endsWith('.uyap.gov.tr'))) {
    throw new Error('DOWNLOAD_ORIGIN_NOT_ALLOWED');
  }
  const path = url.pathname.toLowerCase();
  if (path.endsWith('.ajx') || /(?:dosya|evrak).*_(?:brd|liste|turu)\.ajx$/i.test(path)) {
    throw new Error('DOWNLOAD_METADATA_REQUEST_NOT_FILE');
  }
  const method = request.method || 'GET';
  if (!['GET', 'POST'].includes(method)) throw new Error('DOWNLOAD_METHOD_NOT_ALLOWED');
  const result = {url: url.href, method};
  if (method === 'POST') {
    if (typeof request.body !== 'string' || new TextEncoder().encode(request.body).length > 1024 * 1024) {
      throw new Error('DOWNLOAD_BODY_NOT_SUPPORTED');
    }
    result.body = request.body;
    result.headers = [{name: 'Content-Type', value: 'application/x-www-form-urlencoded'}];
  }
  return result;
}
