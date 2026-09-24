import { sessionStore } from './store.js';
import {
  KURUM_DIRECT_LOGIN_URL,
  KURUM_EDEVLET_LOGIN_URL,
  isSessionRelevantUrl,
  loginUrlForKurumRoute
} from '../shared/auth-routes.js';

const LOGIN_NOTIFICATION_PREFIX = 'uyap-login-required:';
const NOTIFICATION_ICON = 'icons/uyap-warning-128.png';
const UI_SETTINGS_KEY = 'uyapUiSettingsV1';

async function uiSettings() {
  try {
    const stored = await chrome.storage.local.get(UI_SETTINGS_KEY);
    return stored?.[UI_SETTINGS_KEY] || {};
  } catch {
    return {};
  }
}


function isKurumSession(session) {
  return String(session?.sourceSystem || '').toUpperCase() === 'UYAP_KURUM';
}

export function loginNotificationId(sessionId) {
  return `${LOGIN_NOTIFICATION_PREFIX}${sessionId}`;
}

export function parseLoginNotificationId(notificationId) {
  const raw = String(notificationId || '');
  if (!raw.startsWith(LOGIN_NOTIFICATION_PREFIX)) return null;
  const sessionId = raw.slice(LOGIN_NOTIFICATION_PREFIX.length).trim();
  return sessionId || null;
}

function notificationTitle(session) {
  return isKurumSession(session)
    ? 'UYAP Kurum oturumu sona erdi'
    : 'UYAP oturumu sona erdi';
}

function notificationMessage(session) {
  const product = isKurumSession(session) ? 'UYAP Kurum' : 'UYAP';
  return `${product} arşivleme işi güvenli biçimde duraklatıldı. Yeniden giriş yaptığınızda kuyruk kaldığı yerden otomatik devam edecek.`;
}

function notificationOptions(session, {minimal = false, includeButtons = true} = {}) {
  const options = {
    type: 'basic',
    iconUrl: chrome.runtime.getURL(NOTIFICATION_ICON),
    title: notificationTitle(session),
    message: notificationMessage(session)
  };
  if (!minimal) {
    options.contextMessage = 'Arşiv işi ve doğrulanmış evraklar korunuyor.';
    options.priority = 2;
    options.requireInteraction = true;
  }
  if (includeButtons && isKurumSession(session)) {
    options.buttons = [
      {title: "UYAP Kurum'u aç"},
      {title: 'e-Devlet girişini aç'}
    ];
  }
  return options;
}

export async function showLoginRequiredNotification(session, {ensureOnly = false} = {}) {
  if (!session?.sessionId || typeof chrome?.notifications?.create !== 'function') {
    return {shown:false, reason:'NOTIFICATIONS_UNAVAILABLE'};
  }
  const settings = await uiSettings();
  if (settings.loginNotification === false) return {shown:false, reason:'DISABLED_BY_USER'};
  const id = loginNotificationId(session.sessionId);
  try {
    if (ensureOnly && typeof chrome.notifications.getAll === 'function') {
      const active = await chrome.notifications.getAll();
      if (active && Object.prototype.hasOwnProperty.call(active, id)) {
        return {shown:false, existing:true, id};
      }
    }
    try {
      await chrome.notifications.create(id, notificationOptions(session));
      return {shown:true, id, fallback:false};
    } catch (primaryError) {
      // Some Windows/Chrome combinations reject advisory fields even though a
      // basic notification is still available. Keep the two Kurum login
      // buttons on the first fallback, then strip even those on the final core
      // fallback so the alert itself has the highest chance of surfacing.
      try {
        await chrome.notifications.create(id, notificationOptions(session, {minimal:true}));
        return {shown:true, id, fallback:true, fallbackMode:'minimal-with-buttons', primaryError:String(primaryError)};
      } catch (secondaryError) {
        await chrome.notifications.create(id, notificationOptions(session, {minimal:true, includeButtons:false}));
        return {shown:true, id, fallback:true, fallbackMode:'core', primaryError:String(primaryError), secondaryError:String(secondaryError)};
      }
    }
  } catch (error) {
    return {shown:false, reason:String(error), id};
  }
}

export async function clearLoginRequiredNotification(sessionId) {
  if (!sessionId || typeof chrome?.notifications?.clear !== 'function') return false;
  try {
    return Boolean(await chrome.notifications.clear(loginNotificationId(sessionId)));
  } catch {
    return false;
  }
}

async function uyapTabs(session) {
  if (typeof chrome?.tabs?.query !== 'function') return [];
  const patterns = ['https://*.uyap.gov.tr/*', 'https://uyap.gov.tr/*'];
  if (isKurumSession(session)) patterns.push('https://www.turkiye.gov.tr/uyap-portali-kurum-girisi*');
  try {
    return await chrome.tabs.query({url:patterns}) || [];
  } catch {
    return [];
  }
}

async function candidateTabForSession(session) {
  if (Number.isInteger(session?.tabId) && typeof chrome?.tabs?.get === 'function') {
    try {
      const tab = await chrome.tabs.get(session.tabId);
      // During LOGIN_REQUIRED the preserved source tab may temporarily be on an
      // external identity-provider page. It is still the user's authoritative
      // recovery tab, so focus it even when its current URL is not readable.
      if (tab?.id != null && (
        (session.status === 'paused' && session.pauseReason === 'LOGIN_REQUIRED') ||
        isSessionRelevantUrl(session, tab.url)
      )) return tab;
    } catch {
      // Source tab may have been closed. Fall through to another relevant tab.
    }
  }
  const tabs = await uyapTabs(session);
  return tabs.find((tab) => Number.isInteger(tab?.id) && isSessionRelevantUrl(session, tab.url)) || null;
}

async function focusTab(tab) {
  if (!tab?.id) return false;
  if (Number.isInteger(tab.windowId) && typeof chrome?.windows?.update === 'function') {
    await chrome.windows.update(tab.windowId, {focused:true});
  }
  if (typeof chrome?.tabs?.update === 'function') {
    await chrome.tabs.update(tab.id, {active:true});
  }
  return true;
}

export async function focusSessionFromNotification(notificationId) {
  const sessionId = parseLoginNotificationId(notificationId);
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (!session) return false;
  const tab = await candidateTabForSession(session);
  if (!tab?.id) {
    if (isKurumSession(session)) {
      const opened = await openKurumLoginRoute(sessionId, 'direct');
      return Boolean(opened?.ok);
    }
    return false;
  }
  try {
    return await focusTab(tab);
  } catch {
    return false;
  }
}

export async function openKurumLoginRoute(sessionId, route = 'direct') {
  const session = await sessionStore.get(sessionId);
  if (!session || !isKurumSession(session)) throw new Error('UYAP_KURUM_SESSION_REQUIRED');
  const targetUrl = loginUrlForKurumRoute(route);
  let authTab = null;

  // RC15.9: never repurpose the preserved case/source tab for authentication.
  // Re-login runs in a dedicated AUTH tab so the source tab can be refreshed
  // and rebound after shared browser cookies/session state are restored.
  if (Number.isInteger(session.authTabId) && typeof chrome?.tabs?.get === 'function') {
    try { authTab = await chrome.tabs.get(session.authTabId); } catch { authTab = null; }
  }
  if (authTab?.id != null && typeof chrome?.tabs?.update === 'function') {
    authTab = await chrome.tabs.update(authTab.id, {url:targetUrl, active:true});
  } else if (typeof chrome?.tabs?.create === 'function') {
    authTab = await chrome.tabs.create({url:targetUrl, active:true});
  } else {
    throw new Error('LOGIN_TAB_UNAVAILABLE');
  }

  if (Number.isInteger(authTab?.windowId) && typeof chrome?.windows?.update === 'function') {
    await chrome.windows.update(authTab.windowId, {focused:true});
  }
  await sessionStore.update(sessionId, {
    authTabId: Number.isInteger(authTab?.id) ? authTab.id : null,
    sourceTabIdAtLogin: Number.isInteger(session.tabId) ? session.tabId : session.sourceTabIdAtLogin || null,
    preferredLoginRoute: route === 'edevlet' ? 'edevlet' : 'direct',
    preferredLoginUrl: targetUrl,
    loginRouteOpenedAt: new Date().toISOString()
  });
  return {ok:true, route:route === 'edevlet' ? 'edevlet' : 'direct', url:targetUrl, tabId:authTab?.id ?? null, sourceTabId:session.tabId ?? null};
}

export async function openLoginRouteFromNotification(notificationId, buttonIndex) {
  const sessionId = parseLoginNotificationId(notificationId);
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (!isKurumSession(session)) return false;
  const route = Number(buttonIndex) === 1 ? 'edevlet' : 'direct';
  const result = await openKurumLoginRoute(sessionId, route);
  return Boolean(result?.ok);
}

export async function showArchiveCompletionNotification(session) {
  if (!session?.sessionId || typeof chrome?.notifications?.create !== 'function') return {shown:false, reason:'NOTIFICATIONS_UNAVAILABLE'};
  const settings = await uiSettings();
  if (settings.completionNotification === false) return {shown:false, reason:'DISABLED_BY_USER'};
  const total = Number(session.total || 0);
  const parts = Number(session.finalIntegrity?.partCount || session.archiveParts?.length || 0);
  const id = `uyap-completed:${session.sessionId}`;
  try {
    await chrome.notifications.create(id, {
      type:'basic',
      iconUrl: chrome.runtime.getURL('icons/uyap-archive-128.png'),
      title:'UYAP arşivi tamamlandı',
      message:`${total.toLocaleString('tr-TR')} evrak doğrulandı${parts ? ` • ${parts} ZIP parçası` : ''}. Bütünlük kapısı geçti.`,
      contextMessage: `${notificationTitle(session).replace(' oturumu sona erdi','')} • ${session.caseId || session.courtName || session.institutionName || 'Arşiv işi'}`
    });
    return {shown:true,id};
  } catch (error) { return {shown:false,reason:String(error),id}; }
}

export async function showArchiveProblemNotification(session) {
  if (!session?.sessionId || typeof chrome?.notifications?.create !== 'function') return {shown:false, reason:'NOTIFICATIONS_UNAVAILABLE'};
  const settings = await uiSettings();
  if (settings.problemNotification === false) return {shown:false, reason:'DISABLED_BY_USER'};
  const unresolved = Number(session.unresolved || 0);
  const id = `uyap-problem:${session.sessionId}`;
  try {
    await chrome.notifications.create(id, {
      type:'basic',
      iconUrl: chrome.runtime.getURL(NOTIFICATION_ICON),
      title:'UYAP arşivinde müdahale gerekli',
      message:`${unresolved || 'Bazı'} kayıt otomatik olarak tamamlanamadı. Doğrulanmış dosyalar korunuyor.`,
      contextMessage: `${isKurumSession(session) ? 'UYAP Kurum' : 'UYAP'} • Sorun Merkezi'nden inceleyin.`
    });
    return {shown:true,id};
  } catch (error) { return {shown:false,reason:String(error),id}; }
}

export const LOGIN_ROUTE_URLS = Object.freeze({
  direct: KURUM_DIRECT_LOGIN_URL,
  edevlet: KURUM_EDEVLET_LOGIN_URL
});
