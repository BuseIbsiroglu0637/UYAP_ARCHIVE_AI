export const KURUM_DIRECT_LOGIN_URL = 'https://kurum.uyap.gov.tr/';
export const KURUM_EDEVLET_LOGIN_URL = 'https://www.turkiye.gov.tr/uyap-portali-kurum-girisi';

const UYAP_LOGIN_PATH_PATTERN = /(?:^|\/)(?:login|giris|giriş|oauth|sso|logout|cikis|çıkış|oturum)(?:\/|$|[?&#_.=-])/i;

export function parseHttpUrl(value) {
  try {
    const url = new URL(String(value || ''));
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    return url;
  } catch {
    return null;
  }
}

export function isUyapHostUrl(value) {
  const url = parseHttpUrl(value);
  if (!url) return false;
  const hostname = url.hostname.toLowerCase();
  return hostname === 'uyap.gov.tr' || hostname.endsWith('.uyap.gov.tr');
}

export function isKurumUyapUrl(value) {
  const url = parseHttpUrl(value);
  if (!url) return false;
  return url.hostname.toLowerCase() === 'kurum.uyap.gov.tr';
}

export function isEdevletKurumEntryUrl(value) {
  const url = parseHttpUrl(value);
  if (!url) return false;
  return url.hostname.toLowerCase() === 'www.turkiye.gov.tr' &&
    /^\/uyap-portali-kurum-girisi(?:\/|$)/i.test(url.pathname);
}

export function classifyAuthRoute(value) {
  const url = parseHttpUrl(value);
  if (!url) return null;
  const hostname = url.hostname.toLowerCase();
  const routeText = `${url.pathname}${url.search}${url.hash}`;

  if (isEdevletKurumEntryUrl(url.href)) {
    return {
      kind: 'KURUM_EDEVLET_ENTRY',
      sourceSystem: 'UYAP_KURUM',
      loginRequired: true,
      url: KURUM_EDEVLET_LOGIN_URL
    };
  }

  if (hostname === 'giris.turkiye.gov.tr' && /^\/Giris(?:\/|$)/i.test(url.pathname)) {
    return {
      kind: 'KURUM_EDEVLET_AUTH',
      sourceSystem: 'UYAP_KURUM',
      loginRequired: true,
      url: KURUM_EDEVLET_LOGIN_URL
    };
  }

  if (hostname === 'kurum.uyap.gov.tr' && UYAP_LOGIN_PATH_PATTERN.test(routeText)) {
    return {
      kind: 'KURUM_DIRECT_LOGIN',
      sourceSystem: 'UYAP_KURUM',
      loginRequired: true,
      url: KURUM_DIRECT_LOGIN_URL
    };
  }

  if ((hostname === 'uyap.gov.tr' || hostname.endsWith('.uyap.gov.tr')) && UYAP_LOGIN_PATH_PATTERN.test(routeText)) {
    return {
      kind: 'UYAP_LOGIN',
      sourceSystem: 'UYAP',
      loginRequired: true,
      url: url.href
    };
  }

  return null;
}

export function loginUrlForKurumRoute(route) {
  return route === 'edevlet' ? KURUM_EDEVLET_LOGIN_URL : KURUM_DIRECT_LOGIN_URL;
}

export function isSessionRelevantUrl(session, value) {
  if (isUyapHostUrl(value)) return true;
  return String(session?.sourceSystem || '').toUpperCase() === 'UYAP_KURUM' && isEdevletKurumEntryUrl(value);
}
