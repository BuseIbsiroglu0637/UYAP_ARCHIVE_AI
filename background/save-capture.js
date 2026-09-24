import {validateDownloadRequest} from '../shared/request-policy.js';

// Serialized by scripting.executeScript; this function must be self-contained.
// Hooks exist only while one extension-triggered Kaydet attempt is being resolved.
// They observe requests passively: the real UYAP save runs first, while a
// validated request is retained only as a short-lived fallback. Cookies and
// response bodies are never read or persisted.
export async function captureSaveInPage(attemptId, captureWindowMs = 1_500) {
  const target = document.querySelector(`[data-uyap-archive-save-attempt="${CSS.escape(attemptId)}"]`);
  if (!target) return {ok:false, error:'SAVE_MENU_NOT_FOUND'};

  const requests = [];
  const restore = [];
  const xhrMeta = new WeakMap();
  let captureError = null;
  let firstCaptureAt = null;

  function normalizeBody(body) {
    if (body == null) return null;
    if (typeof body === 'string') return body;
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) return body.toString();
    captureError ||= 'DOWNLOAD_BODY_NOT_SUPPORTED';
    return null;
  }

  function scoreRequest(request) {
    try {
      const url = new URL(request.url, document.baseURI);
      const path = url.pathname.toLowerCase();
      if (path.endsWith('.ajx') || /(?:dosya|evrak).*_(?:brd|liste|turu)\.ajx$/i.test(path)) return -100;
      let score = 0;
      const mime = String(url.searchParams.get('mimeType') || '').toLowerCase();
      if (/(?:viewer\.jsp|view_document_brd\.uyap)$/i.test(path) && /^(?:udf|pdf|tiff?|jpeg?|jpg|png|gif|bmp|docx?|rtf|xml|html?)$/i.test(mime)) score += 90;
      if (/\.(?:pdf|udf|tiff?|jpe?g|png|gif|bmp|docx?|xlsx?|pptx?|rtf|xml|odt|ods|odp|zip|7z|rar)(?:$|[?#])/i.test(url.href)) score += 70;
      if (url.searchParams.has('evrakId')) score += 30;
      if (url.searchParams.has('dosyaId')) score += 15;
      if (/(?:download|indir|evrak|document|viewer)/i.test(path)) score += 15;
      if (request.method === 'POST') score += 2;
      return score;
    } catch { return -100; }
  }

  function capture(url, method = 'GET', body = null) {
    try {
      const absolute = new URL(String(url), document.baseURI);
      if (
        absolute.protocol !== 'https:' ||
        absolute.username ||
        absolute.password ||
        !(absolute.hostname === 'uyap.gov.tr' || absolute.hostname.endsWith('.uyap.gov.tr'))
      ) {
        throw new Error('DOWNLOAD_ORIGIN_NOT_ALLOWED');
      }

      const normalizedMethod = String(method || 'GET').toUpperCase();
      const normalizedBody = normalizeBody(body);
      if (captureError) return;

      requests.push({
        url:absolute.href,
        method:normalizedMethod,
        ...(normalizedBody !== null ? {body:normalizedBody} : {})
      });
      firstCaptureAt ||= Date.now();
    } catch (error) {
      captureError ||= error?.message || String(error);
    }
  }

  function patch(object, name, value) {
    const original = object?.[name];
    if (!object || typeof original === 'undefined') return false;
    try {
      object[name] = value;
      restore.push(() => {
        try { object[name] = original; } catch { /* best effort */ }
      });
      return true;
    } catch {
      return false;
    }
  }

  function captureForm(form) {
    if (form.querySelector('input[type="password"]')) {
      captureError = 'LOGIN_FORM_NOT_DOWNLOAD';
      return;
    }

    const values = new URLSearchParams();
    for (const [key, value] of new FormData(form)) {
      if (typeof value !== 'string') {
        captureError = 'FILE_UPLOAD_NOT_DOWNLOAD';
        return;
      }
      values.append(key, value);
    }

    const method = (form.method || 'GET').toUpperCase();
    const url = new URL(form.action || document.baseURI, document.baseURI);
    if (method === 'GET') {
      url.search = values.toString();
      capture(url.href, 'GET');
    } else if (
      method === 'POST' &&
      (!form.enctype || form.enctype === 'application/x-www-form-urlencoded')
    ) {
      capture(url.href, method, values.toString());
    } else {
      captureError = 'FORM_ENCODING_NOT_SUPPORTED';
    }
  }

  const anchorEvent = event => {
    const anchor = event.target?.closest?.('a[href]');
    if (!anchor || /^(?:#|javascript:)/i.test(anchor.getAttribute('href') || '')) return;
    // Passive observation only. The real UYAP handler is allowed to continue so
    // blob/native browser downloads can be observed by chrome.downloads.
    capture(anchor.href, 'GET');
  };

  const submitEvent = event => {
    // Do not cancel the real form submission. We only record a validated
    // fallback request while UYAP remains responsible for the primary save.
    captureForm(event.target);
  };

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  try {
    const originalWindowOpen = window.open;
    patch(window, 'open', function(url, ...args) {
      capture(url, 'GET');
      return originalWindowOpen.call(this, url, ...args);
    });

    const originalFormSubmit = HTMLFormElement.prototype.submit;
    patch(HTMLFormElement.prototype, 'submit', function(...args) {
      captureForm(this);
      return originalFormSubmit.apply(this, args);
    });

    const originalRequestSubmit = HTMLFormElement.prototype.requestSubmit;
    if (typeof originalRequestSubmit === 'function') {
      patch(HTMLFormElement.prototype, 'requestSubmit', function(...args) {
        captureForm(this);
        return originalRequestSubmit.apply(this, args);
      });
    }

    const originalAnchorClick = HTMLAnchorElement.prototype.click;
    patch(HTMLAnchorElement.prototype, 'click', function(...args) {
      capture(this.href, 'GET');
      return originalAnchorClick.apply(this, args);
    });

    // Observe fetch but let the real request continue. Native-first is safer
    // than reproducing an unknown portal handler because UYAP may transform the
    // response into a blob/download in page code after fetch resolves.
    if (typeof window.fetch === 'function') {
      const originalFetch = window.fetch;
      patch(window, 'fetch', function(input, init = {}) {
        const url = typeof input === 'string' || input instanceof URL
          ? String(input)
          : input?.url;
        const method = init?.method || input?.method || 'GET';
        const body = init?.body ?? null;
        capture(url, method, body);
        return originalFetch.call(this, input, init);
      });
    }

    // jQuery.ajax and many legacy UYAP handlers eventually use XHR. open() is
    // allowed to initialise the object, but send() is intercepted so the probe
    // cannot create an unbound browser download or duplicate server request.
    if (typeof XMLHttpRequest !== 'undefined' && XMLHttpRequest?.prototype) {
      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSend = XMLHttpRequest.prototype.send;

      patch(XMLHttpRequest.prototype, 'open', function(method, url, ...rest) {
        xhrMeta.set(this, {method:String(method || 'GET').toUpperCase(), url});
        return originalOpen.call(this, method, url, ...rest);
      });

      patch(XMLHttpRequest.prototype, 'send', function(body = null) {
        const meta = xhrMeta.get(this);
        if (!meta) {
          captureError ||= 'XHR_REQUEST_NOT_INITIALIZED';
          return originalSend.call(this, body);
        }
        capture(meta.url, meta.method, body);
        return originalSend.call(this, body);
      });
    }

    const iframeSrc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
    if (iframeSrc?.configurable) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'src', {
        ...iframeSrc,
        set(url) {
          capture(url, 'GET');
          return iframeSrc.set.call(this, url);
        }
      });
      restore.push(() => {
        try { Object.defineProperty(HTMLIFrameElement.prototype, 'src', iframeSrc); } catch { /* best effort */ }
      });
    }

    const originalSetAttribute = Element.prototype.setAttribute;
    patch(Element.prototype, 'setAttribute', function(name, value) {
      if (this instanceof HTMLIFrameElement && String(name).toLowerCase() === 'src') {
        capture(value, 'GET');
      }
      return originalSetAttribute.call(this, name, value);
    });

    document.addEventListener('click', anchorEvent, true);
    document.addEventListener('submit', submitEvent, true);

    const rect = target.getBoundingClientRect();
    const options = {
      bubbles:true,
      cancelable:true,
      view:window,
      button:0,
      clientX:rect.left + rect.width / 2,
      clientY:rect.top + rect.height / 2
    };

    for (const type of ['mousedown', 'mouseup', 'click']) {
      target.dispatchEvent(new MouseEvent(type, {
        ...options,
        buttons:type === 'mousedown' ? 1 : 0
      }));
    }

    // Synchronous handlers return immediately. Asynchronous AJAX handlers get
    // a short bounded window; after the first request we only wait briefly for
    // a second request so ambiguity remains detectable without slowing every
    // normal download by the full timeout.
    const deadline = Date.now() + Math.max(180, Number(captureWindowMs) || 550);
    while (!captureError && Date.now() < deadline) {
      if (firstCaptureAt) {
        const elapsed = Date.now() - firstCaptureAt;
        const bestScore = requests.reduce((best, item) => Math.max(best, scoreRequest(item)), -100);
        // Metadata AJAX often fires first. Give UYAP enough time to emit the
        // actual viewer/download request instead of blindly accepting the first
        // network call. Strong viewer/file candidates can finish sooner.
        if (bestScore >= 130 && elapsed >= 75) break;
        if (bestScore >= 100 && elapsed >= 120) break;
        if (bestScore >= 80 && elapsed >= 160) break;
        if (bestScore >= 20 && elapsed >= 350) break;
      }
      await sleep(25);
    }
  } catch {
    captureError ||= 'SAVE_HANDLER_FAILED';
  } finally {
    document.removeEventListener('click', anchorEvent, true);
    document.removeEventListener('submit', submitEvent, true);
    for (const undo of restore.reverse()) undo();
    target.removeAttribute('data-uyap-archive-save-attempt');
  }

  const unique = [...new Map(requests.map(request => [JSON.stringify(request), request])).values()];
  const ranked = unique.map(item => ({item, score:scoreRequest(item)})).sort((a,b)=>b.score-a.score);
  const usable = ranked.filter(row => row.score >= 20);
  const request = !captureError && usable.length && (usable.length === 1 || usable[0].score > usable[1].score) ? usable[0].item : null;
  const captureStatus = captureError
    || (request ? 'FALLBACK_REQUEST_CAPTURED'
      : unique.length && !usable.length ? 'METADATA_ONLY_CAPTURED'
        : usable.length > 1 ? 'AMBIGUOUS_SAVE_REQUEST'
          : 'NATIVE_DOWNLOAD_EXPECTED');

  // The probe's primary job is to trigger/observe the real UYAP Save action.
  // Lack of a reproducible fallback request is not an error by itself; Chrome's
  // native download event may still be the authoritative path.
  return {ok:true, request, captureStatus};
}

export async function captureSaveRequest(tabId, frameId, attemptId, captureWindowMs = 1_500) {
  const results = await chrome.scripting.executeScript({
    target:{tabId, frameIds:[frameId]},
    world:'MAIN',
    func:captureSaveInPage,
    args:[attemptId, Math.min(2_500, Math.max(180, Number(captureWindowMs) || 550))]
  });
  const result = results[0]?.result;
  if (!result?.ok) throw new Error(result?.error || 'SAVE_CAPTURE_FAILED');
  return {
    request: result.request ? validateDownloadRequest(result.request) : null,
    captureStatus: result.captureStatus || 'NATIVE_DOWNLOAD_EXPECTED'
  };
}
