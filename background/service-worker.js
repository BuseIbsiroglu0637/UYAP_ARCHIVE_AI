import {runOperation, getOperation, clearOperation, runNewJob, resumeSavedSession, getSourceDiagnostic, getFormatCensus, listKurumCases, openKurumCase} from "./scan-manager.js";
import { isUyapDownload } from "../shared/download-origin.js";
import { CLICK_DOWNLOAD_TIMEOUT_MS, MESSAGE } from "../shared/constants.js";
import {captureSaveRequest} from './save-capture.js';
import {pingNativeAgent, askNativeAgent} from './native-agent.js';
import {restoreArchiveDownloadUi} from './download-ui.js';
import {logOperationalError} from './operational-logger.js';
import {buildAiContext, buildAiNativeBindings, getAiServiceStatus} from "./ai-context.js";
import {focusSessionFromNotification, openLoginRouteFromNotification, openKurumLoginRoute} from './session-notifications.js';
import {
  createSession,
  determineBrowserFilename,
  determineProvisionalBrowserFilename,
  getPendingDownload,
  getReportData,
  getDiagnosticData,
  getProblemItems,
  getSessionSummary,
  listSessions,
  listResumableSessions,
  listArchiveRegistry,
  selectExistingArchive,
  getSelectedArchive,
  openArchiveFolder,
  openFinalPackageFolder,
  handleAlarm,
  handleDownloadCreated,
  handleDownloadChanged,
  pauseSession,
  persistKurumCaseOpenContext,
  recoverSession,
  recoverRunningSessionAfterNavigation,
  handleSessionTabUnavailable,
  handleSessionExpiredSignal,
  handleSessionAuthNavigation,
  observeUyapNetworkRequest,
  handleUyapNetworkRedirect,
  handleUyapNetworkCompleted,
  attemptResumeLoginSession,
  retryFailed,
  retrySelectedProblems,
  confirmHtmlExclusion,
  resetSessionRuntime,
  repackageVerifiedArchive,
  startSession,
  syncSessionGuardUi
} from "./download-manager.js";

let recoveryTask = null;
function trustedKurumContextSender(sender = {}) {
  if (sender.id !== chrome.runtime.id || !Number.isInteger(sender.tab?.id) || !Number.isInteger(sender.frameId)) return false;
  const trustedKurumUrl = (value) => {
    try {
      const url = new URL(String(value || ''));
      return url.protocol === 'https:' && url.hostname.toLowerCase() === 'kurum.uyap.gov.tr';
    } catch { return false; }
  };
  // Content scripts deliberately run in every frame. For about:blank/srcdoc
  // transition frames MessageSender.origin is the authoritative inherited
  // origin, while tab.url proves the enclosing portal tab is Kurum UYAP.
  const tabTrusted = trustedKurumUrl(sender.tab?.url);
  const frameTrusted = trustedKurumUrl(sender.origin) || trustedKurumUrl(sender.url);
  return tabTrusted && frameTrusted;
}
async function testProductNotification() {
  if (typeof chrome?.notifications?.create !== "function") {
    return {ok:false, error:"Bildirim API kullanılamıyor."};
  }
  const id = `uyap-ui-test-${Date.now()}`;
  await chrome.notifications.create(id, {
    type: "basic",
    iconUrl: chrome.runtime.getURL("icons/uyap-archive-128.png"),
    title: "UYAP Archive bildirim testi",
    message: "Bildirimler hazır. Oturum veya müdahale gerektiren durumlarda bu kanaldan uyarı alabilirsiniz."
  });
  return {ok:true, notificationId:id};
}

function scheduleRecovery() {
  if (!recoveryTask) {
    recoveryTask = recoverSession()
      .catch((error) => logOperationalError("RECOVERY_SCHEDULE_FAILED", error))
      .finally(() => { recoveryTask = null; });
  }
  return recoveryTask;
}

chrome.runtime.onInstalled.addListener(() => { void syncSessionGuardUi(); void scheduleRecovery(); });
chrome.runtime.onStartup.addListener(() => { void syncSessionGuardUi(); void listArchiveRegistry({limit:1}).catch(()=>{}); void scheduleRecovery(); });
async function handleOperationalAlarmSafely(alarm) {
  try {
    await handleAlarm(alarm);
  } catch (error) {
    logOperationalError("OPERATIONAL_ALARM_FAILED", error, {metadata:{alarmKind:String(alarm?.name || "unknown").split(":")[0]}});
    // RC15.9.19: a one-shot chrome.alarms event disappears after firing. If
    // its handler throws, immediately run the durable recovery bootstrap so
    // RUNNING/RECOVERING jobs cannot remain frozen with no future alarm.
    await scheduleRecovery();
  }
}
chrome.alarms.onAlarm.addListener((alarm) => void handleOperationalAlarmSafely(alarm));
chrome.downloads.onCreated.addListener((item) => void handleDownloadCreated(item));
chrome.downloads.onChanged.addListener((delta) => void handleDownloadChanged(delta));
void syncSessionGuardUi();
void restoreArchiveDownloadUi().finally(() => { void syncSessionGuardUi(); void listArchiveRegistry({limit:1}).catch(()=>{}); return scheduleRecovery(); });
function handleUyapNavigation(details) {
  if (details.frameId !== 0) return;
  void handleSessionAuthNavigation(details.tabId, details.url)
    .then((authHandled) => authHandled ? true : attemptResumeLoginSession(details.tabId))
    .then((resumed) => resumed ? true : recoverRunningSessionAfterNavigation(details.tabId))
    .catch((error) => logOperationalError("NAVIGATION_RECOVERY_FAILED", error));
}

const uyapNavigationFilter = {
  url: [
    {hostEquals: "uyap.gov.tr"},
    {hostSuffix: ".uyap.gov.tr"},
    {hostEquals: "www.turkiye.gov.tr", pathPrefix: "/uyap-portali-kurum-girisi"},
    {hostEquals: "giris.turkiye.gov.tr", pathPrefix: "/Giris"}
  ]
};
const externalKurumAuthNavigationFilter = {
  url: [
    {hostEquals: "www.turkiye.gov.tr", pathPrefix: "/uyap-portali-kurum-girisi"},
    {hostEquals: "giris.turkiye.gov.tr", pathPrefix: "/Giris"}
  ]
};
// External e-Devlet auth navigation is authoritative as soon as it commits.
// Normal UYAP navigation still waits for onCompleted so a freshly loading
// archive page is not mistaken for a lost source page.
chrome.webNavigation.onCommitted?.addListener(handleUyapNavigation, externalKurumAuthNavigationFilter);
chrome.webNavigation.onCompleted.addListener(handleUyapNavigation, uyapNavigationFilter);
chrome.webNavigation.onHistoryStateUpdated?.addListener(handleUyapNavigation, uyapNavigationFilter);

// RC15.9: read-only network observer. It never blocks, cancels or rewrites UYAP
// traffic; it only shortens correlation waits and detects auth redirects earlier.
const uyapWebRequestFilter = {urls:["https://*.uyap.gov.tr/*", "https://uyap.gov.tr/*"]};
chrome.webRequest?.onBeforeRequest?.addListener((details) => {
  void observeUyapNetworkRequest(details).catch(()=>{});
}, uyapWebRequestFilter);
chrome.webRequest?.onBeforeRedirect?.addListener((details) => {
  void handleUyapNetworkRedirect(details).catch((error) => logOperationalError("NETWORK_REDIRECT_RECOVERY_FAILED", error));
}, uyapWebRequestFilter);
chrome.webRequest?.onCompleted?.addListener((details) => {
  void handleUyapNetworkCompleted(details).catch((error) => logOperationalError("NETWORK_COMPLETION_RECOVERY_FAILED", error));
}, uyapWebRequestFilter);
chrome.tabs.onRemoved?.addListener((tabId) => {
  void handleSessionTabUnavailable(tabId)
    .catch((error) => logOperationalError("TAB_RECOVERY_FAILED", error));
});

chrome.notifications?.onClicked?.addListener((notificationId) => {
  void focusSessionFromNotification(notificationId)
    .catch((error) => logOperationalError("NOTIFICATION_FOCUS_FAILED", error));
});
chrome.notifications?.onButtonClicked?.addListener((notificationId, buttonIndex) => {
  void openLoginRouteFromNotification(notificationId, buttonIndex)
    .catch((error) => logOperationalError("LOGIN_ROUTE_NOTIFICATION_FAILED", error));
});


chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  getPendingDownload()
    .then(async (pending) => {
      const fresh = pending && Date.now() - pending.createdAt < Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0));
      if (!fresh) {
        suggest();
        return;
      }
      const targetPath = await determineBrowserFilename(item, pending);
      if (targetPath) {
        suggest({ filename: targetPath, conflictAction: "uniquify" });
        return;
      }

      // RC15.3: a fresh UYAP/canonical download that cannot yet be correlated
      // is never allowed to spill into the user's download root. Route it into
      // a session-scoped _unmatched quarantine first; completion-time evidence
      // can still correlate and promote it without weakening final validation.
      const provisionalPath = await determineProvisionalBrowserFilename(item, pending);
      if (provisionalPath) {
        suggest({ filename: provisionalPath, conflictAction: "uniquify" });
        return;
      }
      suggest();
    })
    .catch(() => suggest());
  return true;
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  const run = async () => {
    if (request.type === 'capture_save') {
      const pending = await getPendingDownload();
      if (!pending || pending.attemptId !== request.attemptId || pending.documentId !== request.documentId ||
          pending.tabId !== sender.tab?.id || !Number.isInteger(sender.frameId)) throw new Error('CAPTURE_NOT_AUTHORIZED');
      return captureSaveRequest(sender.tab.id,sender.frameId,request.attemptId,pending.captureWindowMs || request.captureWindowMs || 1_500);
    }
    if (request.type === MESSAGE.KURUM_CASE_CONTEXT_OBSERVED) {
      if (!trustedKurumContextSender(sender)) throw new Error('KURUM_CONTEXT_NOT_AUTHORIZED');
      return persistKurumCaseOpenContext(request.selection || null, sender.tab.id);
    }
    if (request.type === MESSAGE.SESSION_EXPIRED_SIGNAL) {
      if (!Number.isInteger(sender.tab?.id)) throw new Error('SESSION_SIGNAL_NOT_AUTHORIZED');
      let hostname = '';
      try { hostname = new URL(sender.url || sender.tab.url || '').hostname.toLowerCase(); } catch {}
      if (!(hostname === 'uyap.gov.tr' || hostname.endsWith('.uyap.gov.tr'))) {
        throw new Error('SESSION_SIGNAL_NOT_AUTHORIZED');
      }
      return handleSessionExpiredSignal(sender.tab.id, request.evidence || null);
    }
    // Only extension UI pages may mutate the queue or request filesystem actions.
    if (sender.tab && !sender.url?.startsWith(chrome.runtime.getURL(''))) throw new Error('MESSAGE_NOT_AUTHORIZED');
    switch (request.type) {
      case 'ping_agent':
        return pingNativeAgent();
      case "get_operation":
        return getOperation();
      case "scan_session":
        return runOperation({tabId:request.tabId});
      case MESSAGE.NEW_JOB:
        return runNewJob({tabId:request.tabId});
      case MESSAGE.CREATE_SESSION:
        return createSession({ documents: request.documents, tabId: sender.tab?.id ?? request.tabId });
      case MESSAGE.START_SESSION:
        return runOperation({tabId:request.tabId,sessionId:request.sessionId,start:true});
      case MESSAGE.PAUSE_SESSION:
        await clearOperation();
        return pauseSession(request.sessionId);
      case MESSAGE.GET_ACTIVE_SESSION:
        return getSessionSummary(request.sessionId);
      case MESSAGE.GET_SESSIONS:
        return listSessions({limit:request.limit});
      case MESSAGE.GET_RESUMABLE_SESSIONS:
        return listResumableSessions({limit:request.limit,excludeSessionId:request.excludeSessionId||null});
      case MESSAGE.RESUME_SAVED_SESSION:
        return resumeSavedSession({sessionId:request.sessionId,tabId:request.tabId,autoStart:request.autoStart!==false});
      case MESSAGE.GET_REPORT_DATA:
        return getReportData(request.sessionId);
      case MESSAGE.GET_AI_CONTEXT: {
        const report = await getReportData(request.sessionId);
        return buildAiContext(report, {limit:request.limit});
      }
      case MESSAGE.GET_AI_SERVICE_STATUS:
        return getAiServiceStatus();
      case MESSAGE.BUILD_AI_INDEX: {
        const report = await getReportData(request.sessionId);
        const downloadsRoot = report?.session?.downloadsRoot;
        if (!downloadsRoot) throw new Error("AI yerel indeksi için arşiv kökü henüz hazır değil.");
        const bindings = buildAiNativeBindings(report, {limit:request.limit});
        return askNativeAgent({
          action:"ai_build_index",
          sessionId:request.sessionId,
          downloadsRoot,
          bindings
        });
      }
      case MESSAGE.GET_AI_INDEX_STATUS: {
        const report = await getReportData(request.sessionId);
        const downloadsRoot = report?.session?.downloadsRoot;
        if (!downloadsRoot) return {ok:true,kind:"UYAP_AI_INDEX_STATUS",schemaVersion:1,ready:false,sessionId:request.sessionId,documents:0,chunks:0};
        return askNativeAgent({action:"ai_index_status",sessionId:request.sessionId,downloadsRoot});
      }
      case MESSAGE.SEARCH_AI_INDEX: {
        const report = await getReportData(request.sessionId);
        const downloadsRoot = report?.session?.downloadsRoot;
        if (!downloadsRoot) throw new Error("AI araması için arşiv kökü henüz hazır değil.");
        return askNativeAgent({
          action:"ai_search",
          sessionId:request.sessionId,
          downloadsRoot,
          query:String(request.query || "").slice(0,500),
          limit:Math.max(1,Math.min(Number(request.limit || 8),20))
        });
      }
      case MESSAGE.GET_ARCHIVE_REGISTRY:
        return listArchiveRegistry({sessionId:request.sessionId||null,downloadsRoot:request.downloadsRoot||null,limit:request.limit,forceRefresh:request.forceRefresh===true});
      case MESSAGE.SELECT_EXISTING_ARCHIVE:
        return selectExistingArchive();
      case MESSAGE.GET_SELECTED_ARCHIVE:
        return getSelectedArchive();
      case MESSAGE.GET_DIAGNOSTIC_DATA:
        return getDiagnosticData(request.sessionId);
      case MESSAGE.GET_PROBLEM_ITEMS:
        return getProblemItems(request.sessionId, {limit:request.limit});
      case MESSAGE.RETRY_SELECTED_PROBLEMS: {
        const retryResult = await retrySelectedProblems(
          request.sessionId,
          request.keys || [],
          {retryAll:request.retryAll === true, queue:request.queue === "deferred" ? "deferred" : "review"}
        );
        const retrySession = retryResult?.session || null;
        const touched = Number(retryResult?.queued || 0) + Number(retryResult?.expedited || 0) + Number(retryResult?.revalidated || 0);
        const alreadyActive = ["running", "recovering"].includes(String(retrySession?.status || ""));
        if (touched > 0 && retrySession?.sessionId && !alreadyActive && retrySession.status !== "completed") {
          try {
            const resumed = await resumeSavedSession({sessionId:retrySession.sessionId, autoStart:true});
            return {
              ...retryResult,
              session: resumed || retryResult.session,
              autoResumed: true,
              resumeStatus: resumed?.status || null
            };
          } catch (error) {
            const queuedCount = Number(retryResult?.queued || 0) + Number(retryResult?.expedited || 0);
            const prefix = queuedCount > 0
              ? `${queuedCount} kayıt kuyruğa alındı ancak iş otomatik yeniden başlatılamadı.`
              : "Kayıt yerelden doğrulandı ancak iş final kontrol için otomatik yeniden başlatılamadı.";
            throw new Error(`${prefix} ${String(error).replace(/^(?:Error:\s*)+/, "")}`);
          }
        }
        return retryResult;
      }
      case MESSAGE.CONFIRM_HTML_EXCLUSION:
        return confirmHtmlExclusion(request.sessionId, request.key);
      case MESSAGE.OPEN_LOGIN_ROUTE:
        return openKurumLoginRoute(request.sessionId, request.route === 'edevlet' ? 'edevlet' : 'direct');
      case MESSAGE.SOURCE_DIAGNOSTIC:
        return getSourceDiagnostic(request.tabId);
      case MESSAGE.FORMAT_LAB_REPORT: {
        const summary = await getSessionSummary(request.sessionId);
        if (!summary?.sessionId) throw new Error('Format Laboratuvarı için arşiv işi seçin.');
        const census = Number.isInteger(request.tabId) ? await getFormatCensus(request.tabId).catch((error)=>({ok:false,error:String(error).replace(/^(?:Error:\s*)+/, '')})) : null;
        const audit = summary.downloadsRoot ? await askNativeAgent({action:'format_audit',sessionId:summary.sessionId,downloadsRoot:summary.downloadsRoot}) : {ok:false,error:'Arşiv kökü henüz oluşmadı.'};
        return {schemaVersion:1,kind:'UYAP_FORMAT_LAB_REPORT',generatedAt:new Date().toISOString(),session:{sessionId:summary.sessionId,total:summary.total,counts:summary.counts,sourceSystem:summary.sourceSystem},census,audit,requestResolver:{metadataAjaxIgnored:true,knownMetadataSuffixes:['.ajx'],viewerMimePreferred:true,firstRequestBlindFallback:false}};
      }
      case MESSAGE.LIST_KURUM_CASES:
        return listKurumCases(request.tabId);
      case MESSAGE.OPEN_KURUM_CASE: {
        const opened = await openKurumCase(request.tabId, request.caseId);
        return request.newJob === true
          ? runNewJob({tabId:opened.tabId})
          : runOperation({tabId:opened.tabId, start:true});
      }
      case MESSAGE.RETRY_FAILED:
        return runOperation({sessionId:request.sessionId,tabId:request.tabId,retry:true});
      case MESSAGE.RESET_RUNTIME:
        await clearOperation();
        return resetSessionRuntime(request.sessionId);
      case MESSAGE.REPACKAGE_ARCHIVE:
        await clearOperation();
        return repackageVerifiedArchive(request.sessionId);
      case MESSAGE.TEST_NOTIFICATION:
        return testProductNotification();
      case MESSAGE.OPEN_ARCHIVE_FOLDER:
        return openArchiveFolder(request.sessionId || null);
      case MESSAGE.OPEN_FINAL_PACKAGE_FOLDER:
        return openFinalPackageFolder(request.sessionId || null);
      default:
        throw new Error(`Bilinmeyen mesaj: ${request.type}`);
    }
  };

  run().then((data) => sendResponse({ ok: true, data })).catch((error) => {
    logOperationalError("RUNTIME_MESSAGE_FAILED", error, {metadata:{messageType:String(request?.type || "unknown").slice(0,80)}});
    sendResponse({ ok: false, error: String(error) });
  });
  return true;
});
