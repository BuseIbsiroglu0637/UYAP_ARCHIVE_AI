const ARCHIVE_DOWNLOAD_UI_ALARM = 'archive-download-ui-lease';
const ARCHIVE_DOWNLOAD_UI_KEY = 'archiveDownloadUiSuppressed';
const DEFAULT_LEASE_MS = 15_000;
const MIN_PERSISTENT_ALARM_MS = 30_000;
let localRestoreTimer = null;

function uiApiAvailable() {
  return Boolean(globalThis.chrome?.downloads?.setUiOptions);
}

function clearLocalRestoreTimer() {
  if (!localRestoreTimer) return;
  clearTimeout(localRestoreTimer);
  localRestoreTimer = null;
}

export async function suppressArchiveDownloadUi(leaseMs = DEFAULT_LEASE_MS) {
  if (!uiApiAvailable()) return { ok: false, supported: false };
  try {
    const delay = Math.max(2_000, Number(leaseMs) || DEFAULT_LEASE_MS);
    await chrome.downloads.setUiOptions({ enabled: false });
    await chrome.storage?.local?.set?.({ [ARCHIVE_DOWNLOAD_UI_KEY]: true });
    clearLocalRestoreTimer();
    await chrome.alarms?.create?.(ARCHIVE_DOWNLOAD_UI_ALARM, { when: Date.now() + Math.max(delay, MIN_PERSISTENT_ALARM_MS) });
    if (delay < MIN_PERSISTENT_ALARM_MS) {
      localRestoreTimer = setTimeout(() => {
        localRestoreTimer = null;
        void restoreArchiveDownloadUi();
      }, delay);
      localRestoreTimer?.unref?.();
    }
    return { ok: true, supported: true };
  } catch (error) {
    console.warn('UYAP indirme arayüzü gizlenemedi', error);
    return { ok: false, supported: true, error: String(error) };
  }
}

export async function restoreArchiveDownloadUi() {
  clearLocalRestoreTimer();
  if (!uiApiAvailable()) return { ok: false, supported: false };
  try {
    await chrome.downloads.setUiOptions({ enabled: true });
    await chrome.storage?.local?.remove?.(ARCHIVE_DOWNLOAD_UI_KEY);
    await chrome.alarms?.clear?.(ARCHIVE_DOWNLOAD_UI_ALARM);
    return { ok: true, supported: true };
  } catch (error) {
    console.warn('UYAP indirme arayüzü geri açılamadı', error);
    return { ok: false, supported: true, error: String(error) };
  }
}


export async function forgetManagedDownload(downloadId, { removeFile = false } = {}) {
  const id = Number(downloadId);
  if (!Number.isInteger(id)) return { ok: false, skipped: true };
  let removed = false;
  let erased = false;
  if (removeFile && typeof chrome?.downloads?.removeFile === "function") {
    try { await chrome.downloads.removeFile(id); removed = true; } catch {}
  }
  if (typeof chrome?.downloads?.erase === "function") {
    try {
      await chrome.downloads.erase({ id });
      erased = true;
    } catch {}
  }
  return { ok: removed || erased, removed, erased };
}

export function isArchiveDownloadUiAlarm(alarm) {
  return alarm?.name === ARCHIVE_DOWNLOAD_UI_ALARM;
}

export async function handleArchiveDownloadUiAlarm(alarm) {
  if (!isArchiveDownloadUiAlarm(alarm)) return false;
  await restoreArchiveDownloadUi();
  return true;
}

export const downloadUiInternals = Object.freeze({
  alarmName: ARCHIVE_DOWNLOAD_UI_ALARM,
  storageKey: ARCHIVE_DOWNLOAD_UI_KEY,
  defaultLeaseMs: DEFAULT_LEASE_MS,
  minPersistentAlarmMs: MIN_PERSISTENT_ALARM_MS
});
