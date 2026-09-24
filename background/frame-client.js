export const CONTENT_VERSION = chrome.runtime?.getManifest?.()?.version || '2.9.0';
const files = ['content/uyap-profile.js','content/source-adapters.js','content/tree-expansion.js','content/content.js'];
const injecting = new Map();
const injectionStats = { count:0, failures:0, maxConcurrent:0 };
const frameClientScopeId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`;

export async function sendToFrame(tabId, frameId, message) {
  try {
    const probe = await chrome.tabs.sendMessage(tabId, {type:'archive_probe'}, {frameId});
    if (probe?.version === CONTENT_VERSION) return chrome.tabs.sendMessage(tabId, message, {frameId});
  } catch { /* Unpacked extension updates do not inject into already open tabs. */ }
  const key = `${tabId}:${frameId}`;
  if (!injecting.has(key)) {
    injectionStats.count += 1;
    const promise = chrome.scripting.executeScript({target:{tabId,frameIds:[frameId]},files})
      .catch((error) => { injectionStats.failures += 1; throw error; })
      .finally(() => injecting.delete(key));
    injecting.set(key, promise);
    injectionStats.maxConcurrent = Math.max(injectionStats.maxConcurrent, injecting.size);
  }
  await injecting.get(key);
  const injectedProbe = await chrome.tabs.sendMessage(tabId, {type:'archive_probe'}, {frameId});
  if (injectedProbe?.version !== CONTENT_VERSION) {
    throw new Error(`CONTENT_VERSION_MISMATCH: expected ${CONTENT_VERSION}, got ${injectedProbe?.version || 'none'}`);
  }
  return chrome.tabs.sendMessage(tabId, message, {frameId});
}

export async function uyapFrames(tabId) {
  const frames = await chrome.webNavigation.getAllFrames({tabId});
  return (frames || []).filter(frame => {
    if (!frame.url) return true;
    try {
      const url = new URL(frame.url);
      return (url.protocol === 'https:' && (url.hostname === 'uyap.gov.tr' || url.hostname.endsWith('.uyap.gov.tr'))) ||
        ['about:', 'blob:'].includes(url.protocol);
    } catch { return false; }
  });
}

export function getFrameClientStats() {
  return {
    scopeId: frameClientScopeId,
    activeInjections: injecting.size,
    injectionCount: injectionStats.count,
    injectionFailures: injectionStats.failures,
    maxConcurrentInjections: injectionStats.maxConcurrent
  };
}
