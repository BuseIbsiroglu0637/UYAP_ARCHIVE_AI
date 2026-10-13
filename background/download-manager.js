import { isLateDuplicateUyapDownload, isUyapDownload } from "../shared/download-origin.js";
import { directDownloadEvidence, retryableNativeOperation } from "../shared/adoption-policy.js";
import {
  CLICK_DOWNLOAD_TIMEOUT_MS,
  MESSAGE,
  RETRY_DELAYS_MS,
  HTML_RETRY_DELAYS_MS,
  SESSION_HEARTBEAT_INTERVAL_MS,
  SESSION_HEARTBEAT_RETRY_MS,
  SESSION_HEARTBEAT_TIMEOUT_MS,
  RELIABILITY_SCHEMA_VERSION,
  SESSION_STATUS,
  STATUS
} from "../shared/constants.js";
import { buildStagingPath, buildTargetPath, buildUnmatchedPath, detectDownloadExtension, replacePathExtension } from "../shared/path-utils.js";
import { retryDelayForError } from "../shared/retry-policy.js";
import {
  classifyHeartbeatResponse,
  heartbeatFailurePolicy,
  recoveryBudgetAfterVerified
} from "../shared/session-health.js";
import { SMALL_FILE_WARNING, browserCompletionPreflight } from "../shared/download-validation.js";
import { diagnosticStore, documentStore, sessionStore } from "./store.js";
import { askNativeAgent, pingNativeAgent, getNativeBridgeStats } from "./native-agent.js";
import {sendToFrame, uyapFrames, getFrameClientStats} from './frame-client.js';
import {validateDownloadRequest} from '../shared/request-policy.js';
import { clearRecoveredPacingError, nextPacingState, pacingDelayMs, shouldRunDeferredRetry, summarizePerformance, updatePerformanceMetrics } from '../shared/performance-policy.js';
import { attemptStrategyForDocument, appendRetryStrategyHistory, contentEndpointCooldownMs, htmlRetryDecision, isIncompleteContentError, isIsolatedContentError, nextRetryStrategy, retryStrategySupported } from '../shared/failure-policy.js';
import { suppressArchiveDownloadUi, restoreArchiveDownloadUi, handleArchiveDownloadUiAlarm, forgetManagedDownload } from './download-ui.js';
import { FINAL_RECOVERY_MAX_PASSES, planFinalRecovery } from '../shared/final-recovery.js';
import { showLoginRequiredNotification, clearLoginRequiredNotification, showArchiveCompletionNotification, showArchiveProblemNotification } from './session-notifications.js';
import { classifyAuthRoute, isUyapHostUrl } from '../shared/auth-routes.js';
import { DIAGNOSTIC_SCHEMA_VERSION, diagnosticReference, normalizeDiagnosticEvent, sanitizeDiagnosticMetadata, sanitizeDiagnosticText as sanitizePrivacyText } from "../shared/diagnostic-policy.js";

const ACTIVE_SESSION_KEY = "activeSessionId";
const PENDING_DOWNLOAD_KEY = "pendingDownload";
const LAST_DOWNLOADS_ROOT_KEY = "lastDownloadsRoot";
const PORTABLE_RESUME_ENABLED_KEY = "portableResumeEnabled";
const LOCAL_ARCHIVE_RECONCILIATION_VERSION = 5;
const RECENT_FALLBACKS_KEY = "recentFallbackDownloads";
const RECENT_FALLBACK_WINDOW_MS = 30_000;
const UNKNOWN_FILE_TYPE_WARNING = "FILE_TYPE_UNDETERMINED";
const HEARTBEAT_ALARM_PREFIX = "session-heartbeat:";
const LOGIN_WATCH_ALARM_PREFIX = "session-login-watch:";
const LOGIN_WATCH_INTERVAL_MS = 10_000;
const QUEUE_WAKE_ALARM_PREFIX = "queue-wake:";
const DOWNLOAD_WATCHDOG_ALARM_PREFIX = "download-watchdog:";
const CLICK_TIMEOUT_ALARM_PREFIX = "click-timeout:";
const SESSION_RECOVERY_ALARM_PREFIX = "session-recovery:";
const QUEUE_GUARD_ALARM_PREFIX = "queue-guard:";
const SESSION_DRAIN_ALARM_PREFIX = "session-drain:";
let queueLock = false;
let heartbeatTask = null;
let queueRequested = false;
const completingDownloads = new Set();
const preservedNativeRetryDownloads = new Set();
const TRANSIENT_NATIVE_ERROR_CODE = "NATIVE_AGENT_UNAVAILABLE";
const MAX_NATIVE_OPERATION_RETRIES = 3;
const SOURCE_PATH_SYSTEMIC_THRESHOLD = 3;
const SOURCE_PATH_SYSTEMIC_WINDOW_MS = 2 * 60_000;
const SOURCE_PATH_SYSTEMIC_PAUSE_REASON = "SOURCE_PATH_SYSTEMIC";
const NATIVE_OPERATION_ERROR_CODES = new Set(["NATIVE_DB_BUSY", "NATIVE_FILE_BUSY", "NATIVE_OPERATION_FAILED"]);
const ARCHIVE_REGISTRY_CACHE_TTL_MS = 60_000;
let archiveRegistryCache = null;
let archiveRegistryInFlight = null;
function invalidateArchiveRegistryCache(){ archiveRegistryCache = null; }

function nativeOperationFailureCode(agent = {}) {
  const explicit = String(agent.errorCode || "").trim();
  if (explicit && !explicit.startsWith("NATIVE_AGENT_") && !explicit.startsWith("NATIVE_HOST_")) return explicit;
  const message = String(agent.error || "");
  if (/database is locked|database table is locked|SQLITE_BUSY/i.test(message)) return "NATIVE_DB_BUSY";
  if (/PermissionError|WinError\s*32|sharing violation|used by another process/i.test(message)) return "NATIVE_FILE_BUSY";
  return "NATIVE_OPERATION_FAILED";
}

function nativeBridgeTransportFailed(agent = {}) {
  // A negative message delivered through onMessage proves the host process and
  // pipe are alive. Only failures created by the bridge itself are transport
  // outages that require a ping/reconnect cycle.
  return agent.bridgeResponseReceived !== true;
}

function withoutRecoveredNativeWarnings(warnings = []) {
  return (Array.isArray(warnings) ? warnings : []).filter((warning) => String(warning || "") !== TRANSIENT_NATIVE_ERROR_CODE);
}

function terminalDocumentResolved(document = {}) {
  return [STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status);
}

function hasRecoveredNativeGhost(document = {}) {
  const warnings = Array.isArray(document.warnings) ? document.warnings : [];
  return terminalDocumentResolved(document) &&
    (String(document.errorCode || "") === TRANSIENT_NATIVE_ERROR_CODE || warnings.includes(TRANSIENT_NATIVE_ERROR_CODE));
}

async function cleanupResolvedNativeTransientState(sessionId, { reason = "native-health-recovered", forceDocuments = false } = {}) {
  const cleanupStartedAtMs = Date.now();
  const initialSession = await sessionStore.get(sessionId);
  if (!initialSession) return {documents:0, pacing:false, superseded:false};
  const initialFailureToken = initialSession.nativeTransientFailureToken || null;
  let changedDocuments = 0;

  const shouldScanDocuments = forceDocuments || Number(initialSession.transientNativeCleanupSchemaVersion || 0) < 1;
  if (shouldScanDocuments) {
    const documents = await documentStore.bySession(sessionId);
    for (const document of documents) {
      if (!hasRecoveredNativeGhost(document)) continue;
      await documentStore.update(document.key, {
        warnings: withoutRecoveredNativeWarnings(document.warnings),
        errorCode: String(document.errorCode || "") === TRANSIENT_NATIVE_ERROR_CODE ? null : document.errorCode || null,
        errorMessage: String(document.errorCode || "") === TRANSIENT_NATIVE_ERROR_CODE ? null : document.errorMessage || null,
        transientNativeRecoveredAt: new Date().toISOString(),
        transientNativeRecoveryReason: reason
      });
      changedDocuments += 1;
    }
  }

  // Document cleanup can take noticeable time in a 12k case. Re-read the
  // session immediately before changing health/pacing so an outage that began
  // during the scan is never erased by an older successful ping/validation.
  const session = await sessionStore.get(sessionId);
  if (!session) return {documents:changedDocuments, pacing:false, superseded:false};
  const latestFailureAtMs = Date.parse(String(session.nativeTransientFailureAt || ""));
  const failureTokenChanged = Boolean(session.nativeTransientFailureToken) &&
    session.nativeTransientFailureToken !== initialFailureToken;
  const superseded = session.nativeTransientFailureActive === true &&
    (failureTokenChanged || (Number.isFinite(latestFailureAtMs) && latestFailureAtMs > cleanupStartedAtMs));
  if (superseded) {
    if (changedDocuments) {
      await recordDiagnosticEvent(sessionId, "TRANSIENT_NATIVE_DOCUMENT_STATE_CLEANED", {
        reason,
        documents: changedDocuments,
        sessionRecoverySuperseded: true
      });
    }
    return {documents:changedDocuments, pacing:false, superseded:true};
  }

  const pacingBefore = session.pacing || {};
  const pacingAfter = clearRecoveredPacingError(pacingBefore, TRANSIENT_NATIVE_ERROR_CODE);
  const pacingChanged = JSON.stringify(pacingAfter) !== JSON.stringify(pacingBefore);
  const changes = {
    nativeTransientFailureActive: false,
    nativeTransientRecoveredAt: new Date().toISOString(),
    nativeTransientRecoveryReason: reason,
    transientNativeCleanupSchemaVersion: 1
  };
  if (pacingChanged) changes.pacing = pacingAfter;
  if (session.status !== SESSION_STATUS.RECOVERING && session.sessionGuardStatus === "NATIVE_RECOVERY") {
    changes.sessionGuardStatus = "HEALTHY";
    changes.queueError = null;
  }
  await sessionStore.update(sessionId, changes);

  if (changedDocuments || pacingChanged) {
    await recordDiagnosticEvent(sessionId, "TRANSIENT_NATIVE_STATE_CLEANED", {
      reason,
      documents: changedDocuments,
      pacingRecovered: pacingChanged,
      removedPacingErrors: Number(pacingAfter?.recoveredErrorCount || 0)
    });
  }
  return {documents:changedDocuments,pacing:pacingChanged,superseded:false};
}

// RC15.8 AUTO pipeline: browser download correlation stays concurrency=1, but
// completed bytes may be validated by the fast native lane while the next UYAP
// download is prepared. The depth is intentionally bounded to avoid a runaway
// speculative queue when UYAP or the native host degrades.
const validatingDocuments = new Map();
const MAX_PIPELINED_VALIDATIONS = 2;
const localArchiveBatchTasks = new Map();
const localArchivePrefetchTasks = new Map();
// RC15.9: network request URLs are ephemeral correlation evidence only. Never
// persist raw request URLs/query tokens in extension storage or diagnostics.
const transientNetworkHints = new Map();
const NETWORK_HINT_TTL_MS = 30_000;
const DIRECT_DOWNLOAD_BIND_WINDOW_MS = 60_000;
const DIRECT_DOWNLOAD_EXACT_BIND_WINDOW_MS = 120_000;
const DIRECT_DOWNLOAD_SINGLE_CANDIDATE_WINDOW_MS = 15_000;
const DIRECT_DOWNLOAD_GENERIC_DOCUMENT_WINDOW_MS = 4_000;
const DIRECT_DOWNLOAD_UNKNOWN_DOCUMENT_WINDOW_MS = 2_000;
const DIRECT_DOWNLOAD_GENERIC_GRACE_MS = 250;
const EXPECTED_DOWNLOAD_BASELINE_LIMIT = 64;
const EXACT_CLICK_DOWNLOAD_TIMEOUT_MS = 120_000;
const DOWNLOAD_WATCHDOG_POLL_MS = 30_000;
const DOWNLOAD_STALL_TIMEOUT_MS = 75_000;
const DOWNLOAD_PROGRESS_HARD_LIMIT_MS = 30 * 60_000;
const SESSION_LOSS_DRAIN_MS = 20_000;
const START_FAILURE_CIRCUIT_THRESHOLD = 3;
const ARCHIVE_PART_TARGET_BYTES = 1 * 1024 * 1024 * 1024;
const ARCHIVE_PART_MAX_DOCUMENTS = 1000;
const LARGE_CASE_THRESHOLD = 3000;
const LARGE_CASE_BATCH_SIZE = 500;
const LARGE_CASE_MIN_BATCH_TRANSITION_MS = 50;
// RC15.9.3: adaptive HTML bypass. HTML responses are real UYAP responses, but
// isolated/small bursts must not freeze the whole queue. Fresh primary documents
// keep moving; a short global cooldown is used only for a strong endpoint-wide
// burst. Half-open recovery tolerates one noisy document before re-opening.
const HTML_CIRCUIT_CONSECUTIVE_THRESHOLD = 6;
const HTML_CIRCUIT_WINDOW_MIN = 10;
const HTML_CIRCUIT_WINDOW_FAILURES = 8;
const HTML_CIRCUIT_WINDOW_RATE = 0.80;
const HTML_CIRCUIT_HALF_OPEN_CONSECUTIVE_THRESHOLD = 2;
const HTML_CIRCUIT_STABLE_SUCCESSES = 2;
const HTML_CIRCUIT_MAX_SOFT_PROBES = 3;
const HTML_CIRCUIT_COOLDOWNS_MS = Object.freeze([15_000, 30_000, 60_000]);
const UYAP_INVALID_REQUEST_TAIL_HEX = "496e76616c69642072657175657374";

function isUyapInvalidRequestPdfTail(value = {}) {
  return String(value?.errorCode || "") === "PDF_TRAILING_DATA" &&
    String(value?.pdfTrailingCoreHex || "").toLowerCase() === UYAP_INVALID_REQUEST_TAIL_HEX;
}

function htmlCircuitCooldownMs(openCount = 1) {
  const index = Math.max(0, Math.min(HTML_CIRCUIT_COOLDOWNS_MS.length - 1, Math.trunc(Number(openCount || 1)) - 1));
  return HTML_CIRCUIT_COOLDOWNS_MS[index];
}

function htmlCircuitState(session = {}) {
  const explicit = String(session?.htmlCircuitState || "").trim().toLowerCase();
  if (["closed", "open", "half_open"].includes(explicit)) return explicit;
  if (session?.htmlCircuitHalfOpen === true) return "half_open";
  if (session?.contentCooldownUntil && Date.parse(session.contentCooldownUntil) > Date.now()) return "open";
  return "closed";
}

function htmlCircuitDeadlineMs(session = {}) {
  const values = [session?.htmlCircuitNextProbeAt, session?.contentCooldownUntil]
    .map((value) => Date.parse(String(value || "")))
    .filter(Number.isFinite);
  return values.length ? Math.max(...values) : 0;
}

function htmlCircuitShouldOpen(session = {}, projected = {}) {
  const state = htmlCircuitState(session);
  const consecutiveHtml = Math.max(0, Number(projected?.consecutiveHtml || 0));
  const attempts = Math.max(0, Number(projected?.windowAttempts || 0));
  const htmlFailures = Math.max(0, Number(projected?.windowHtmlFailures || 0));
  const htmlRate = Math.max(0, Number(projected?.windowHtmlRate || 0));
  if (state === "open") return false;
  if (state === "half_open") {
    return consecutiveHtml >= HTML_CIRCUIT_HALF_OPEN_CONSECUTIVE_THRESHOLD;
  }
  return consecutiveHtml >= HTML_CIRCUIT_CONSECUTIVE_THRESHOLD ||
    (attempts >= HTML_CIRCUIT_WINDOW_MIN &&
     htmlFailures >= HTML_CIRCUIT_WINDOW_FAILURES &&
     htmlRate >= HTML_CIRCUIT_WINDOW_RATE);
}

function archiveWillBePartitioned(total = 0, knownVerifiedBytes = 0) {
  const count = Math.max(0, Number(total || 0));
  const bytes = Math.max(0, Number(knownVerifiedBytes || 0));
  return count > ARCHIVE_PART_MAX_DOCUMENTS || (count > 1 && bytes > ARCHIVE_PART_TARGET_BYTES);
}

function partitionedArchiveModeForSession(session = {}) {
  if (session?.partitionedArchiveMode === true) return true;
  return archiveWillBePartitioned(session?.total, session?.verifiedArchiveBytes);
}

function adaptPacingForPartitionedArchive(pacing = {}, enabled = false) {
  if (!enabled) return pacing;

  // RC15.2: HTML_RESPONSE is a per-document content-endpoint outcome, not a
  // global health failure by itself. Do not make an otherwise healthy 12k
  // primary queue crawl merely because old/noisy documents raised the rolling
  // HTML percentage. nextPacingState() already escalates genuine HTML bursts
  // (high-rate + consecutive failures) and hard session/native failures.
  const systemicRate = Math.max(0, Number(pacing.windowSystemicErrorRate || 0));
  const consecutiveSystemic = Math.max(0, Number(pacing.consecutiveFailures || 0));

  if ((consecutiveSystemic >= 3 || (systemicRate >= 0.30 && consecutiveSystemic >= 1)) &&
      !["cautious", "backoff"].includes(pacing.mode)) {
    return { ...pacing, mode: "cautious", backoffReason: "partitioned_systemic_pressure", successStreak: 0 };
  }
  if ((consecutiveSystemic >= 2 || (systemicRate >= 0.20 && consecutiveSystemic >= 1)) && pacing.mode === "fast") {
    return { ...pacing, mode: "normal", backoffReason: "partitioned_systemic_pressure", successStreak: 0 };
  }
  return pacing;
}

function largeCaseBatchTransitionDelayMs(session = {}) {
  const pacing = session?.pacing || {};
  const mode = String(pacing.mode || "normal");
  const htmlRate = Math.max(0, Number(pacing.windowHtmlRate || 0));
  const errorRate = Math.max(0, Number(pacing.windowErrorRate || 0));
  if (mode === "backoff") return 2_000;
  if (mode === "cautious") return 1_000;
  if (htmlRate >= 0.15 || errorRate >= 0.20) return 750;
  if (htmlRate >= 0.05 || errorRate >= 0.10) return 500;
  return LARGE_CASE_MIN_BATCH_TRANSITION_MS;
}
const STRUCTURAL_START_ERRORS = new Set([
  "DOWNLOAD_START_FAILED",
  "NATIVE_DOWNLOAD_NOT_CORRELATED",
  "CLICK_DOWNLOAD_TIMEOUT"
]);
const SESSION_WIDE_START_FAILURES = new Set([
  // A real DOM/click-path failure can indicate that the source page itself
  // changed. Correlation misses/timeouts are transport-level events and must
  // stay per-document on 10k+ jobs; otherwise three harmless misses trigger a
  // full-session O(n) recovery loop.
  "DOWNLOAD_START_FAILED"
]);

const MIN_PERSISTENT_ALARM_DELAY_MS = 30_000;
const localOperationalTimers = new Map();

function cancelLocalOperationalTimer(name) {
  const timer = localOperationalTimers.get(name);
  if (!timer) return;
  clearTimeout(timer);
  localOperationalTimers.delete(name);
}

async function scheduleOperationalAlarm(name, options = {}) {
  const requestedWhen = Number(options?.when);
  const now = Date.now();
  const target = Number.isFinite(requestedWhen) ? Math.max(now, requestedWhen) : now + MIN_PERSISTENT_ALARM_DELAY_MS;
  cancelLocalOperationalTimer(name);

  // Packaged MV3 extensions cannot rely on sub-30-second chrome.alarms.
  // Use a fast in-memory timer for the normal hot path and always arm a
  // >=30 s persistent fallback so a service-worker suspension self-heals.
  const persistentWhen = Math.max(target, now + MIN_PERSISTENT_ALARM_DELAY_MS);
  await chrome.alarms.create(name, {...options, when: persistentWhen});
  const delay = Math.max(0, target - now);
  if (delay < MIN_PERSISTENT_ALARM_DELAY_MS) {
    const timer = setTimeout(() => {
      localOperationalTimers.delete(name);
      // RC15.9.19: never delete the durable MV3 fallback before the hot-path
      // handler has completed successfully. If an iframe disappears or a
      // transient Chrome API call throws, the >=30 s persistent alarm remains
      // armed and can wake a suspended service worker for another attempt.
      void (async () => {
        try {
          await handleAlarm({name, scheduledTime: target});
          const current = typeof chrome?.alarms?.get === "function"
            ? await chrome.alarms.get(name).catch(() => null)
            : null;
          // handleAlarm may have replaced this named alarm with a new retry.
          // Clear only the original fallback, never a newly scheduled retry.
          if (current && Math.abs(Number(current.scheduledTime || 0) - persistentWhen) < 250) {
            await chrome.alarms.clear(name).catch(() => false);
          }
        } catch (error) {
          console.error("UYAP operational alarm hot-path failed; persistent fallback kept", name, error);
        }
      })();
    }, delay);
    timer?.unref?.();
    localOperationalTimers.set(name, timer);
  }
}

async function clearOperationalAlarm(name) {
  cancelLocalOperationalTimer(name);
  return chrome.alarms.clear(name);
}

function downloadBasename(item) {
  const raw = String(item?.filename || "").replaceAll("\\", "/");
  return raw.split("/").at(-1) || "";
}

function nativeUyapFilenameSourceId(item) {
  const basename = downloadBasename(item);
  const match = basename.match(/^evrak[_ -]?(\d{4,30})(?:[._ -]|$)/i);
  return match?.[1] || null;
}

function downloadStartAtMs(item) {
  const parsed = Date.parse(item?.startTime || "");
  return Number.isFinite(parsed) ? parsed : null;
}

function clickTimeoutForDocument(document) {
  const sourceId = String(document?.sourceDocumentId || '').trim();
  return /^\d{4,30}$/.test(sourceId) ? EXACT_CLICK_DOWNLOAD_TIMEOUT_MS : CLICK_DOWNLOAD_TIMEOUT_MS;
}

async function rememberRecentFallback(document, attemptId, downloadId) {
  const stored = await chrome.storage.local.get(RECENT_FALLBACKS_KEY);
  const now = Date.now();
  const current = Array.isArray(stored?.[RECENT_FALLBACKS_KEY]) ? stored[RECENT_FALLBACKS_KEY] : [];
  const next = current
    .filter((entry) => now - Number(entry?.createdAt || 0) <= RECENT_FALLBACK_WINDOW_MS)
    .slice(-7);
  next.push({
    key: document.key,
    sessionId: document.sessionId,
    attemptId,
    sourceDocumentId: document.sourceDocumentId || null,
    extensionDownloadId: downloadId,
    createdAt: now
  });
  await chrome.storage.local.set({[RECENT_FALLBACKS_KEY]: next});
}

async function suppressLateDuplicateUyapDownload(item) {
  if (!item || item.byExtensionId) return false;
  const stored = await chrome.storage.local.get(RECENT_FALLBACKS_KEY);
  const now = Date.now();
  const entries = (Array.isArray(stored?.[RECENT_FALLBACKS_KEY]) ? stored[RECENT_FALLBACKS_KEY] : [])
    .filter((entry) => now - Number(entry?.createdAt || 0) <= RECENT_FALLBACK_WINDOW_MS);
  if (!entries.length) return false;
  for (const entry of entries) {
    if (!isLateDuplicateUyapDownload(item, entry, now)) continue;
    const document = await documentStore.get(entry.key);
    if (!document || document.attemptId !== entry.attemptId || Number(document.downloadId) !== Number(entry.extensionDownloadId)) continue;
    try { await chrome.downloads.cancel(item.id); } catch {}
    try { await chrome.downloads.removeFile?.(item.id); } catch {}
    try { await chrome.downloads.erase?.({id:item.id}); } catch {}
    await documentStore.update(document.key, {
      lateDuplicateSuppressed: Number(document.lateDuplicateSuppressed || 0) + 1,
      lastLateDuplicateSuppressedAt: new Date(now).toISOString()
    });
    await chrome.storage.local.set({[RECENT_FALLBACKS_KEY]: entries.filter((x) => x !== entry)});
    return true;
  }
  if (entries.length !== (stored?.[RECENT_FALLBACKS_KEY] || []).length) {
    await chrome.storage.local.set({[RECENT_FALLBACKS_KEY]: entries});
  }
  return false;
}

async function expectedDownloadBaselineIds() {
  try {
    const items = await chrome.downloads.search({limit: EXPECTED_DOWNLOAD_BASELINE_LIMIT, orderBy:['-startTime']});
    return (items || []).map((item) => Number(item?.id)).filter(Number.isInteger).slice(0, EXPECTED_DOWNLOAD_BASELINE_LIMIT);
  } catch {
    return [];
  }
}

function isLeaseFresh(pending, now = Date.now()) {
  const armedAt = Number(pending?.leaseArmedAt || pending?.createdAt || 0);
  const expiresAt = Number(pending?.leaseExpiresAt || (armedAt + Number(pending?.clickTimeoutMs || DIRECT_DOWNLOAD_BIND_WINDOW_MS)));
  return Number.isFinite(armedAt) && Number.isFinite(expiresAt) && now >= armedAt - 500 && now <= expiresAt;
}

async function activeInProgressBrowserDownload(sessionId) {
  const document = await documentStore.firstBySessionStatus(sessionId, STATUS.DOWNLOADING);
  if (!document || !Number.isInteger(document.downloadId)) return null;
  let item = null;
  try { [item] = await chrome.downloads.search({id: document.downloadId}); } catch {}
  return item?.state === 'in_progress' ? {document, item} : null;
}

async function tryRestoreKurumSourceView(session, tabId = session?.tabId) {
  if (String(session?.sourceSystem || '').toUpperCase() !== 'UYAP_KURUM' || !Number.isInteger(tabId)) return false;
  const openContext = session?.sourceContext?.kurumCaseOpenContext || await getCachedKurumCaseOpenContext(session?.caseId);
  let frames = [];
  try { frames = await uyapFrames(tabId); } catch { frames = []; }
  const frameIds = [...new Set([0, ...(frames || []).map(frame => frame.frameId)].filter(Number.isInteger))].slice(0, 24);
  for (const frameId of frameIds) {
    try {
      const response = await sendToFrame(tabId, frameId, {
        type:'open_kurum_case_smart',
        caseId:session?.caseId || null,
        courtName:session?.courtName || session?.institutionName || null,
        openContext:openContext || null
      });
      if (!response?.ok) continue;
      const deadline = Date.now() + 10_000;
      while (Date.now() < deadline) {
        if (await sessionPageReady(session, tabId).catch(() => false)) return true;
        await new Promise(resolve => setTimeout(resolve, 250));
      }
    } catch {}
  }
  return false;
}

function isLeaseCandidateItem(item, document, pending, now = Date.now()) {
  if (!item || item.byExtensionId || !pending || Number(pending.leaseVersion) !== 1 || !Array.isArray(pending.baselineDownloadIds) || !isLeaseFresh(pending, now)) return false;
  const baseline = new Set((pending.baselineDownloadIds || []).map(Number));
  if (baseline.has(Number(item.id))) return false;
  if (!isUyapDownload(item)) return false;
  const detectedExtension = detectDownloadExtension(item).extension;
  const imageLike = [".jpg", ".jpeg", ".png", ".gif", ".bmp"].includes(detectedExtension);
  const strictDocumentType = [".pdf", ".udf", ".zip", ".tif", ".tiff"].includes(detectedExtension);
  const unknownDocumentType = detectedExtension === ".bin";
  // Classic UYAP frequently exposes no numeric source id until after the
  // browser download starts. For non-image legal documents we allow a much
  // tighter single-candidate lease, but only on classic UYAP and only when the
  // browser explicitly reports a UYAP-origin download. Unknown/binary types
  // remain fail-closed.
  const classicUyap = String(document?.sourceSystem || 'UYAP').toUpperCase() === 'UYAP';
  if (!imageLike && !((strictDocumentType || unknownDocumentType) && classicUyap)) return false;
  const armedAt = Number(pending.leaseArmedAt || pending.createdAt || 0);
  const startedAt = downloadStartAtMs(item);
  const candidateWindowMs = imageLike
    ? DIRECT_DOWNLOAD_SINGLE_CANDIDATE_WINDOW_MS
    : (unknownDocumentType ? DIRECT_DOWNLOAD_UNKNOWN_DOCUMENT_WINDOW_MS : DIRECT_DOWNLOAD_GENERIC_DOCUMENT_WINDOW_MS);
  if (!imageLike && startedAt === null) return false;
  if (startedAt !== null && (startedAt < armedAt - 500 || startedAt > armedAt + candidateWindowMs)) return false;
  const filenameSourceId = nativeUyapFilenameSourceId(item);
  const expectedSourceId = String(document?.sourceDocumentId || '').trim();
  if (/^\d{4,30}$/.test(expectedSourceId) && filenameSourceId && filenameSourceId !== expectedSourceId) return false;
  return true;
}

async function correlateLeaseSingleCandidate(item, document, pending, {graceMs = 0} = {}) {
  const exact = correlateDirectUyapDownload(item, document, pending);
  if (exact) return {...exact, correlationMode:'exact', candidateCount:1};
  if (!isLeaseCandidateItem(item, document, pending)) return null;
  if (graceMs > 0) await new Promise((resolve) => setTimeout(resolve, graceMs));

  let items = [];
  const armedAt = Number(pending.leaseArmedAt || pending.createdAt || Date.now());
  try {
    items = await chrome.downloads.search({
      startedAfter: new Date(armedAt - 500).toISOString(),
      limit: EXPECTED_DOWNLOAD_BASELINE_LIMIT
    });
  } catch {
    try { items = await chrome.downloads.search({}); } catch { items = []; }
  }
  const byId = new Map();
  for (const candidate of [...(items || []), item]) {
    if (Number.isInteger(Number(candidate?.id))) byId.set(Number(candidate.id), candidate);
  }
  const candidates = [];
  for (const candidate of byId.values()) {
    if (!isLeaseCandidateItem(candidate, document, pending)) continue;
    const alreadyBound = await documentStore.byDownloadId?.(candidate.id);
    if (alreadyBound && alreadyBound.key !== document?.key) continue;
    candidates.push(candidate);
  }
  if (candidates.length !== 1 || Number(candidates[0].id) !== Number(item.id)) {
    if (document?.key) {
      await documentStore.update(document.key, {
        correlationMode: 'single_candidate_lease_rejected',
        correlationCandidateCount: candidates.length
      });
    }
    return null;
  }
  const expectedSourceId = String(document?.sourceDocumentId || '').trim();
  return {
    sourceDocumentId: /^\d{4,30}$/.test(expectedSourceId) ? expectedSourceId : null,
    bindingMethod: 'native-uyap-single-candidate-lease',
    correlationMode: 'single_candidate_lease',
    candidateCount: 1
  };
}

function sourceIdFromUyapUrl(item, expectedSourceId) {
  if (!expectedSourceId) return null;
  for (const value of [item?.url, item?.finalUrl]) {
    try {
      const url = new URL(value);
      for (const candidate of url.searchParams.values()) {
        if (candidate === expectedSourceId) return expectedSourceId;
      }
    } catch {
      // URL evidence is optional.
    }
  }
  return null;
}

function correlateDirectUyapDownload(item, document, pending) {
  if (!pending || item?.byExtensionId) return null;
  if (document?.attemptId !== pending.attemptId) return null;
  if (![STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING].includes(document?.status)) return null;
  if (document.status === STATUS.DOWNLOADING && Number.isInteger(document.downloadId) && document.downloadId !== item?.id) return null;

  const filenameSourceId = nativeUyapFilenameSourceId(item);
  const expectedSourceId = String(document?.sourceDocumentId || "").trim();
  const numericExpected = /^\d{4,30}$/.test(expectedSourceId);
  const uyapOrigin = isUyapDownload(item);
  const exactEvidence = numericExpected && (
    filenameSourceId === expectedSourceId ||
    (uyapOrigin && sourceIdFromUyapUrl(item, expectedSourceId) === expectedSourceId)
  );
  const bindWindowMs = exactEvidence ? DIRECT_DOWNLOAD_EXACT_BIND_WINDOW_MS : DIRECT_DOWNLOAD_BIND_WINDOW_MS;
  const ageMs = Date.now() - Number(pending.createdAt || 0);
  if (!Number.isFinite(ageMs) || ageMs < -500 || ageMs > bindWindowMs) return null;

  const startedAt = downloadStartAtMs(item);
  if (startedAt !== null && Math.abs(startedAt - Number(pending.createdAt || 0)) > bindWindowMs) return null;

  if (numericExpected) {
    if (filenameSourceId === expectedSourceId) {
      return {
        sourceDocumentId: expectedSourceId,
        bindingMethod: uyapOrigin ? "native-uyap-exact-source-id" : "native-canonical-exact-source-id"
      };
    }
    if (uyapOrigin && sourceIdFromUyapUrl(item, expectedSourceId)) {
      return {
        sourceDocumentId: expectedSourceId,
        bindingMethod: "native-uyap-request-source-id"
      };
    }
    return null;
  }

  // Live UYAP sometimes hides the numeric source id in the scanned DOM and
  // only reveals it in the browser download name. While exactly one automatic
  // Save attempt is armed, a fresh canonical evrak_<numeric-id> filename is
  // strong enough to correlate even if Chrome omits/refactors origin metadata.
  if (!filenameSourceId) return null;
  return {
    sourceDocumentId: filenameSourceId,
    bindingMethod: uyapOrigin
      ? "native-uyap-discovered-source-id"
      : "native-canonical-fresh-pending"
  };
}


function isSafeProvisionalUyapCandidate(item, document, pending, now = Date.now()) {
  if (!item || item.byExtensionId || !pending || !document) return false;
  if (document.attemptId !== pending.attemptId || document.status !== STATUS.AWAITING_BROWSER) return false;
  if (!isLeaseFresh(pending, now)) return false;
  const baseline = new Set((pending.baselineDownloadIds || []).map(Number));
  if (baseline.has(Number(item.id))) return false;

  const detected = detectDownloadExtension(item).extension;
  const supported = new Set([".pdf", ".udf", ".zip", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".html", ".htm", ".bin"]);
  if (!supported.has(detected)) return false;

  const canonicalSourceId = nativeUyapFilenameSourceId(item);
  const uyapOrigin = isUyapDownload(item);
  if (!uyapOrigin && !canonicalSourceId) return false;

  const expectedSourceId = String(document.sourceDocumentId || "").trim();
  if (/^\d{4,30}$/.test(expectedSourceId) && canonicalSourceId && canonicalSourceId !== expectedSourceId) return false;

  const startedAt = downloadStartAtMs(item);
  const armedAt = Number(pending.leaseArmedAt || pending.createdAt || 0);
  const hardWindow = Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0));
  if (startedAt !== null && (startedAt < armedAt - 750 || startedAt > armedAt + hardWindow)) return false;
  return true;
}

async function registerProvisionalUnmatchedDownload(downloadItem, pending) {
  if (!pending) return null;
  const document = await documentStore.get(pending.key);
  if (document?.provisionalUnmatched && document.attemptId === pending.attemptId &&
      Number(document.downloadId) === Number(downloadItem?.id) && document.provisionalUnmatchedPath) {
    return document.provisionalUnmatchedPath;
  }
  if (!isSafeProvisionalUyapCandidate(downloadItem, document, pending)) return null;

  const detected = detectDownloadExtension(downloadItem);
  const originalFilename = downloadBasename(downloadItem);
  const provisionalPath = buildUnmatchedPath(
    pending.sessionId,
    pending.attemptId,
    originalFilename,
    detected.extension
  );
  const browserDownloadStartedAtMs = downloadStartAtMs(downloadItem) || Date.now();
  const attemptStartedAtMs = Number(document?.attemptStartedAtMs || pending.createdAt || 0);

  await documentStore.update(document.key, {
    downloadId: downloadItem.id,
    status: STATUS.DOWNLOADING,
    browserFilename: downloadItem.filename,
    originalFilename,
    extension: detected.extension,
    extensionSource: detected.source,
    provisionalUnmatchedPath: provisionalPath,
    provisionalUnmatched: true,
    bindingMethod: "native-uyap-provisional-unmatched",
    correlationMode: "provisional_unmatched",
    correlationCandidateCount: 1,
    downloadStartedAtMs: browserDownloadStartedAtMs,
    perfClickToDownloadMs: metricDuration(attemptStartedAtMs, browserDownloadStartedAtMs),
    watchdogLastBytesReceived: Math.max(0, Number(downloadItem?.bytesReceived || downloadItem?.fileSize || 0)),
    watchdogLastProgressAt: new Date().toISOString(),
    watchdogLastObservedAt: new Date().toISOString()
  });
  await setPendingDownload({
    ...pending,
    downloadId: downloadItem.id,
    provisionalUnmatchedPath: provisionalPath,
    provisionalOriginalFilename: originalFilename,
    provisionalExtension: detected.extension
  });
  await sessionStore.update(pending.sessionId, {consecutiveStartFailures: 0});
  await recordDiagnosticEvent(pending.sessionId, "DOWNLOAD_ROUTED_TO_UNMATCHED", {
    documentId: document.documentId || null,
    downloadId: downloadItem.id,
    provisionalPath,
    extension: detected.extension,
    uyapOrigin: isUyapDownload(downloadItem),
    canonicalFilename: Boolean(nativeUyapFilenameSourceId(downloadItem))
  });
  return provisionalPath;
}

export async function determineProvisionalBrowserFilename(downloadItem, pending) {
  return registerProvisionalUnmatchedDownload(downloadItem, pending);
}

async function finalizeProvisionalCorrelation(document, item, pending) {
  if (!document?.provisionalUnmatched) return {ok:true, document};
  const correlationItem = {
    ...item,
    filename: document.originalFilename || item?.filename
  };
  const correlationPending = pending && pending.key === document.key && pending.attemptId === document.attemptId
    ? pending
    : {
        key: document.key,
        sessionId: document.sessionId,
        documentId: document.runtimeDocumentId || document.documentId,
        logicalDocumentId: document.documentId,
        attemptId: document.attemptId,
        tabId: document.tabId,
        stagingPath: document.stagingPath,
        createdAt: Number(document.attemptStartedAtMs || Date.parse(document.startedAt || "") || Date.now()),
        leaseVersion: 1,
        leaseArmedAt: Number(document.attemptStartedAtMs || Date.parse(document.startedAt || "") || Date.now()),
        leaseExpiresAt: Number(document.attemptStartedAtMs || Date.parse(document.startedAt || "") || Date.now()) + clickTimeoutForDocument(document),
        baselineDownloadIds: []
      };

  let correlation = null;
  const provisionalSourceId = nativeUyapFilenameSourceId(correlationItem);
  const expectedSourceId = String(document.sourceDocumentId || "").trim();
  if (provisionalSourceId) {
    if (/^\d{4,30}$/.test(expectedSourceId) && provisionalSourceId !== expectedSourceId) {
      return {ok:false, document};
    }
    correlation = {
      sourceDocumentId: provisionalSourceId,
      bindingMethod: isUyapDownload(correlationItem)
        ? "native-uyap-provisional-canonical"
        : "native-canonical-provisional",
      correlationMode: "provisional_canonical",
      candidateCount: 1
    };
  } else {
    correlation = correlateDirectUyapDownload(correlationItem, document, correlationPending);
    if (!correlation) {
      correlation = await correlateLeaseSingleCandidate(correlationItem, document, correlationPending);
    }
    if (!correlation && !/^\d{4,30}$/.test(expectedSourceId) && isUyapDownload(correlationItem) &&
        Number(document.downloadId) === Number(item?.id) && document.correlationMode === "provisional_unmatched") {
      // The download was quarantined while a single active Save lease was fresh.
      // If UYAP never exposes a numeric id, preserve the same one-candidate lease
      // semantics already used by the normal path; content validation remains mandatory.
      correlation = {
        sourceDocumentId: null,
        bindingMethod: "native-uyap-provisional-single-candidate-lease",
        correlationMode: "provisional_single_candidate_lease",
        candidateCount: 1
      };
    }
  }
  if (!correlation) return {ok:false, document};

  const detected = detectDownloadExtension(correlationItem);
  const updates = {
    provisionalUnmatched: false,
    sourceDocumentId: correlation.sourceDocumentId || document.sourceDocumentId || null,
    sourceDocumentIdOrigin: correlation.sourceDocumentId
      ? (document.sourceDocumentIdOrigin || "download-correlation")
      : document.sourceDocumentIdOrigin || null,
    bindingMethod: `${correlation.bindingMethod}-provisional-unmatched`,
    correlationMode: `${correlation.correlationMode || "exact"}_provisional_unmatched`,
    correlationCandidateCount: correlation.candidateCount || 1,
    extension: detected.extension || document.extension,
    extensionSource: detected.source || document.extensionSource,
    targetPath: buildTargetPath({
      ...document,
      sourceDocumentId: correlation.sourceDocumentId || document.sourceDocumentId || null,
      extension: detected.extension || document.extension
    })
  };
  const updated = await documentStore.update(document.key, updates);
  await recordDiagnosticEvent(document.sessionId, "UNMATCHED_DOWNLOAD_CORRELATED", {
    documentId: document.documentId || null,
    downloadId: item?.id || document.downloadId || null,
    bindingMethod: updates.bindingMethod,
    correlationMode: updates.correlationMode
  });
  return {ok:true, document: updated || {...document, ...updates}};
}

async function recoverStrandedCanonicalDownload(document) {
  if (!document || document.status !== STATUS.RETRY_WAIT) return false;
  if (!STRUCTURAL_START_ERRORS.has(document.errorCode)) return false;
  if (!document.attemptId || !document.stagingPath) return false;

  const attemptStart = Number(document.attemptStartedAtMs || Date.parse(document.startedAt || ""));
  if (!Number.isFinite(attemptStart) || attemptStart <= 0) return false;
  const observedFailureAt = Math.max(
    attemptStart + 1_000,
    Date.parse(document.retryQueuedAt || "") || 0,
    Date.parse(document.updatedAt || "") || 0
  );
  const failureAt = Math.min(
    attemptStart + clickTimeoutForDocument(document),
    observedFailureAt
  );

  let items = [];
  try {
    items = await chrome.downloads.search({
      startedAfter: new Date(attemptStart - 1_000).toISOString(),
      startedBefore: new Date(failureAt + 10_000).toISOString(),
      limit: EXPECTED_DOWNLOAD_BASELINE_LIMIT
    });
  } catch {
    return false;
  }

  const expectedSourceId = String(document.sourceDocumentId || "").trim();
  const candidates = [];
  for (const item of items || []) {
    if (!item || item.byExtensionId || item.state !== "complete" || item.exists === false) continue;
    const existing = await documentStore.byDownloadId(item.id);
    if (existing && existing.key !== document.key) continue;
    const sourceId = nativeUyapFilenameSourceId(item);
    if (!sourceId) continue;
    if (/^\d{4,30}$/.test(expectedSourceId) && sourceId !== expectedSourceId) continue;
    const startedAt = downloadStartAtMs(item);
    if (startedAt !== null && (startedAt < attemptStart - 1_000 || startedAt > failureAt + 10_000)) continue;
    candidates.push({item, sourceId});
  }
  if (candidates.length !== 1) {
    if (candidates.length > 1) {
      await recordDiagnosticEvent(document.sessionId, "STRAY_DOWNLOAD_RECOVERY_AMBIGUOUS", {
        documentId: document.documentId || null,
        candidates: candidates.length
      });
    }
    return false;
  }

  const {item, sourceId} = candidates[0];
  const detected = detectDownloadExtension(item);
  const syntheticPending = {
    key: document.key,
    sessionId: document.sessionId,
    documentId: document.runtimeDocumentId || document.documentId,
    logicalDocumentId: document.documentId,
    attemptId: document.attemptId,
    tabId: document.tabId,
    stagingPath: document.stagingPath,
    createdAt: attemptStart,
    leaseVersion: 1,
    leaseArmedAt: attemptStart,
    leaseExpiresAt: failureAt + 10_000,
    baselineDownloadIds: []
  };
  await attachBrowserDownload(item, syntheticPending, {
    stagingPath: document.stagingPath,
    sourceDocumentId: sourceId,
    bindingMethod: "native-canonical-stray-history-recovery",
    correlationMode: "canonical_stray_history_recovery",
    correlationCandidateCount: 1,
    originalFilename: downloadBasename(item),
    extension: detected.extension,
    extensionSource: detected.source,
    finalizePending: true
  });
  await recordDiagnosticEvent(document.sessionId, "STRAY_BROWSER_DOWNLOAD_ADOPTED", {
    documentId: document.documentId || null,
    downloadId: item.id,
    sourceDocumentId: sourceId,
    originalFilename: downloadBasename(item)
  });
  await handleDownloadChanged({id:item.id, state:{current:"complete"}});
  return true;
}


async function recoverStrandedCanonicalDownloadsAtStart(sessionId) {
  let recovered = 0;
  for (const errorCode of ["CLICK_DOWNLOAD_TIMEOUT", "NATIVE_DOWNLOAD_NOT_CORRELATED", "DOWNLOAD_START_FAILED"]) {
    for (let i = 0; i < 4; i += 1) {
      const document = await documentStore.firstBySessionStatusError(sessionId, STATUS.RETRY_WAIT, errorCode);
      if (!document) break;
      const adopted = await recoverStrandedCanonicalDownload(document);
      if (!adopted) break;
      recovered += 1;
    }
  }
  if (recovered > 0) {
    await recordDiagnosticEvent(sessionId, "STRAY_DOWNLOAD_STARTUP_RECOVERY", {recovered});
  }
  return recovered;
}

function queueWakeAlarmName(sessionId) { return `${QUEUE_WAKE_ALARM_PREFIX}${sessionId}`; }
function watchdogAlarmName(sessionId) { return `${DOWNLOAD_WATCHDOG_ALARM_PREFIX}${sessionId}`; }
function clickTimeoutAlarmName(sessionId) { return `${CLICK_TIMEOUT_ALARM_PREFIX}${sessionId}`; }
function recoveryAlarmName(sessionId) { return `${SESSION_RECOVERY_ALARM_PREFIX}${sessionId}`; }
function queueGuardAlarmName(sessionId) { return `${QUEUE_GUARD_ALARM_PREFIX}${sessionId}`; }
function sessionDrainAlarmName(sessionId) { return `${SESSION_DRAIN_ALARM_PREFIX}${sessionId}`; }

async function scheduleQueueGuard(sessionId) {
  // A minute-level persistent guard is deliberately independent from the hot
  // sub-30-second queue timer. If MV3 suspends/restarts the worker and an
  // in-memory timer disappears, this alarm eventually wakes the job and
  // re-enters the durable queue state machine.
  await chrome.alarms.create(queueGuardAlarmName(sessionId), {
    delayInMinutes: 1,
    periodInMinutes: 1
  });
}

function metricDuration(startMs, endMs = Date.now()) {
  const start = Number(startMs);
  const end = Number(endMs);
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? end - start : null;
}

const DIAGNOSTIC_EVENT_EXPORT_LIMIT = 1000;
const DIAGNOSTIC_PROBLEM_LIMIT = 500;

function compactDiagnosticDetail(detail = {}) {
  return sanitizeDiagnosticMetadata(detail);
}

async function recordDiagnosticEvent(sessionId, type, detail = {}) {
  if (!sessionId || !type) return;
  try {
    const atMs = Date.now();
    const sourceDetail = {...(detail || {})};
    const rawDocumentIdentity = sourceDetail.documentId || sourceDetail.sourceDocumentId || null;
    delete sourceDetail.documentId;
    delete sourceDetail.sourceDocumentId;

    if (rawDocumentIdentity) {
      sourceDetail.documentRef = await diagnosticReference(sessionId, rawDocumentIdentity);
    }

    const level = sourceDetail.level
      || (sourceDetail.errorCode || sourceDetail.error ? "error" : /(?:FAILED|REJECTED|TIMEOUT|AMBIGUOUS|EXHAUSTED|BLOCKED)/.test(String(type)) ? "warn" : "info");
    delete sourceDetail.level;

    const normalized = normalizeDiagnosticEvent({
      timestamp: new Date(atMs).toISOString(),
      level,
      component: "archive-engine",
      event: type,
      release: chrome.runtime.getManifest().version_name || chrome.runtime.getManifest().version || null,
      sessionRef: "<session>",
      errorCode: sourceDetail.errorCode || null,
      durationMs: sourceDetail.durationMs ?? null,
      retryCount: sourceDetail.retryCount ?? sourceDetail.attempts ?? null,
      metadata: sourceDetail
    });

    await diagnosticStore.put({
      sessionId,
      atMs,
      at: normalized.timestamp,
      type: normalized.event,
      schemaVersion: normalized.schemaVersion,
      level: normalized.level,
      component: normalized.component,
      release: normalized.release,
      errorCode: normalized.errorCode,
      durationMs: normalized.durationMs,
      retryCount: normalized.retryCount,
      ...normalized.metadata
    });
  } catch {
    // Tanılama yardımcı bir katmandır; ana kuyruğu asla durdurmamalıdır.
  }
}

function sanitizeDiagnosticText(value, maxLength = 400) {
  return sanitizePrivacyText(value, maxLength);
}

function runtimeFootprintSample() {
  const native = getNativeBridgeStats();
  const frames = getFrameClientStats();
  const heapUsedBytes = Number(globalThis.performance?.memory?.usedJSHeapSize);
  return {
    heapUsedBytes: Number.isFinite(heapUsedBytes) && heapUsedBytes > 0 ? heapUsedBytes : null,
    nativePendingRequests: native.pendingRequests,
    nativeMaxQueueDepth: native.maxQueueDepth,
    nativeConnectCount: native.connectCount,
    nativeDisconnectCount: native.disconnectCount,
    nativeTimeoutCount: native.timeoutCount,
    frameInjections: frames.activeInjections,
    frameInjectionMax: frames.maxConcurrentInjections,
    completingDownloads: completingDownloads.size
  };
}

async function applyAttemptPerformance(sessionId, sample = {}, { ok = true, errorCode = null, schedule = true, nativeHealthy = false } = {}) {
  const session = await sessionStore.get(sessionId);
  if (!session) return null;
  const nowMs = Date.now();
  const pacingBase = ok && nativeHealthy
    ? clearRecoveredPacingError(session.pacing || {}, TRANSIENT_NATIVE_ERROR_CODE)
    : (session.pacing || {});
  let pacing = nextPacingState(pacingBase, { ok, errorCode, outcomeKey: sample.documentKey || null });
  const partitionedArchiveMode = partitionedArchiveModeForSession(session);
  pacing = adaptPacingForPartitionedArchive(pacing, partitionedArchiveMode);
  const delayMs = pacingDelayMs(pacing, Math.random(), { partitionedArchiveMode });
  const performance = updatePerformanceMetrics(session.performance || {}, {
    ...sample,
    ...runtimeFootprintSample(),
    verified: ok,
    errorCode
  }, nowMs);
  const normalNextDownloadAt = schedule ? nowMs + delayMs : Number(session.nextDownloadAt || 0);
  const circuitDeadline = htmlCircuitState(session) === "open" ? htmlCircuitDeadlineMs(session) : 0;
  const nextDownloadAt = schedule ? Math.max(normalNextDownloadAt, circuitDeadline) : Number(session.nextDownloadAt || 0);
  const changes = {
    pacing: { ...pacing, delayMs, updatedAt: new Date(nowMs).toISOString() },
    performance
  };
  if (schedule) changes.nextDownloadAt = nextDownloadAt;
  await sessionStore.update(sessionId, changes);
  return { pacing, performance, delayMs, nextDownloadAt };
}

async function scheduleQueueWake(sessionId, when = Date.now() + 20) {
  const now = Date.now();
  // The pacing policy already chooses the safe inter-document delay. The old
  // hard 250 ms floor silently overrode FAST values (including partitioned
  // 180-249 ms), adding idle time without adding a safety gate. Keep a small
  // scheduler floor only; hasActiveDownload() still enforces strict sequential
  // processing and the persistent alarm remains a suspension fallback. RC15.6
  // keeps only a tiny event-loop floor so a healthy queue is effectively zero-idle.
  const target = Math.max(now + 20, Number(when) || now + 20);
  const session = await sessionStore.get(sessionId);
  const existing = Number(session?.nextQueueWakeAt);
  // nextQueueWakeAt is persisted state, not proof that Chrome still owns the
  // corresponding alarm. Recreate the named alarm idempotently every time,
  // while never pushing an already-earlier wake farther into the future.
  // This self-heals MV3/service-worker restarts and stale persisted wake state.
  const effectiveTarget = Number.isFinite(existing) && existing > now
    ? Math.min(existing, target)
    : target;
  await sessionStore.update(sessionId, {nextQueueWakeAt: effectiveTarget});
  await scheduleOperationalAlarm(queueWakeAlarmName(sessionId), {when: effectiveTarget});
}

async function clearSessionRuntimeAlarms(sessionId) {
  if (!sessionId) return;
  await Promise.all([
    clearOperationalAlarm(queueWakeAlarmName(sessionId)),
    clearOperationalAlarm(watchdogAlarmName(sessionId)),
    clearOperationalAlarm(clickTimeoutAlarmName(sessionId)),
    clearOperationalAlarm(recoveryAlarmName(sessionId)),
    clearOperationalAlarm(queueGuardAlarmName(sessionId)),
    clearOperationalAlarm(sessionDrainAlarmName(sessionId)),
    clearOperationalAlarm(`${HEARTBEAT_ALARM_PREFIX}${sessionId}`),
    clearOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`)
  ]);
  await sessionStore.update(sessionId, {nextQueueWakeAt: null});
}

export async function compactOperationalAlarms() {
  if (typeof chrome?.alarms?.getAll !== "function") return 0;
  const alarms = await chrome.alarms.getAll();
  const stale = (alarms || []).filter((alarm) =>
    alarm?.name?.startsWith("retry:") ||
    alarm?.name?.startsWith(DOWNLOAD_WATCHDOG_ALARM_PREFIX) ||
    alarm?.name?.startsWith(CLICK_TIMEOUT_ALARM_PREFIX) ||
    alarm?.name?.startsWith(QUEUE_WAKE_ALARM_PREFIX) ||
    alarm?.name?.startsWith(HEARTBEAT_ALARM_PREFIX) ||
    alarm?.name?.startsWith(LOGIN_WATCH_ALARM_PREFIX) ||
    alarm?.name?.startsWith(SESSION_RECOVERY_ALARM_PREFIX) ||
    alarm?.name?.startsWith(QUEUE_GUARD_ALARM_PREFIX) ||
    alarm?.name?.startsWith(SESSION_DRAIN_ALARM_PREFIX)
  );
  for (const alarm of stale) await clearOperationalAlarm(alarm.name);
  const sessionId = await getActiveSessionId();
  if (sessionId) await sessionStore.update(sessionId, {nextQueueWakeAt: null});
  return stale.length;
}

function sourceFingerprint(document) {
  return [
    document.sourceSystem,
    document.institutionName,
    document.courtName,
    document.caseId,
    document.sourceVersion,
    document.documentId,
    document.documentTitle,
    document.documentDate,
    document.documentType,
    document.unitDocumentNumber,
    document.sender
  ].map((value) => String(value ?? "")).join("|");
}

function normalizedBindingValue(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().toLocaleLowerCase("tr-TR");
}

function semanticRuntimeBindingKey(document) {
  return JSON.stringify([
    normalizedBindingValue(document?.documentTitle),
    normalizedBindingValue(document?.documentDate),
    normalizedBindingValue(document?.unitDocumentNumber),
    normalizedBindingValue(document?.sender),
    normalizedBindingValue(document?.documentType),
    normalizedBindingValue(document?.containerLabel),
    Boolean(document?.isAttachment),
    (document?.folderNames || []).map(normalizedBindingValue)
  ]);
}

async function scanCurrentCaseDocuments(session, tabId = session?.tabId, {quick = false} = {}) {
  if (!session || !Number.isInteger(tabId)) return [];
  const frames = await uyapFrames(tabId).catch(() => []);
  const scanned = [];
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type: MESSAGE.SCAN_PAGE, quick});
      if (!response?.ok) continue;
      for (const document of response?.data?.documents || []) {
        if ((document.sourceSystem || 'UYAP') !== (session.sourceSystem || 'UYAP')) continue;
        if (!kurumContextMatches(session, document)) continue;
        scanned.push({...document, frameId: frame.frameId});
      }
    } catch { /* next frame */ }
  }
  return scanned;
}

async function probeSessionSourceContext(session, tabId) {
  if (!session || !Number.isInteger(tabId)) return null;
  const frames = await uyapFrames(tabId).catch(() => []);
  let matchingButNotReady = null;
  let mismatch = null;
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type: MESSAGE.PROBE_PAGE});
      const data = response?.data;
      if (!response?.ok || !data) continue;
      if ((data.sourceSystem || 'UYAP') !== (session.sourceSystem || 'UYAP')) continue;
      const candidate = {frameId:frame.frameId, ...data};
      if (kurumContextMatches(session, candidate)) {
        if (data.hasDocuments) return {...candidate, matches:true};
        if (!matchingButNotReady) matchingButNotReady = {...candidate, matches:true};
        continue;
      }
      // A different observed case is a context change even when that case's
      // document branch is not ready yet. Do not hide it behind hasDocuments.
      if (!mismatch && candidate.caseId) mismatch = {...candidate, matches:false};
    } catch { /* next frame */ }
  }
  return mismatch || matchingButNotReady;
}

async function pauseForSourceContextChanged(sessionId, binding = null) {
  await clearSessionRuntimeAlarms(sessionId);
  await clearPendingDownload();
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: 'SOURCE_CONTEXT_CHANGED',
    pausedAt: new Date().toISOString(),
    resumeAfterRecovery: false,
    recoveryStartedAt: null,
    sessionGuardStatus: 'CONTEXT_CHANGED',
    queueError: null,
    runtimeBound: binding?.bound || 0,
    runtimeUnmatched: binding?.unmatched || 0,
    runtimeScanned: binding?.scanned || 0,
    observedCaseId: binding?.observedCaseId || null,
    observedCourtName: binding?.observedCourtName || null
  });
  await recordDiagnosticEvent(sessionId, 'SOURCE_CONTEXT_CHANGED', {
    expectedCaseId: (await sessionStore.get(sessionId))?.caseId || null,
    observedCaseId: binding?.observedCaseId || null
  });
  return false;
}

async function refreshSessionRuntimeBindings(sessionId, tabId, { activeBatchOnly = false } = {}) {
  const session = await sessionStore.get(sessionId);
  if (!session || !Number.isInteger(tabId)) return {bound:0, unmatched:0, scanned:0};
  const [persisted, fresh, sourceContext] = await Promise.all([
    documentStore.bySession(sessionId),
    scanCurrentCaseDocuments(session, tabId),
    probeSessionSourceContext(session, tabId)
  ]);
  const unfinishedAll = persisted.filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status));
  const activeBatchNo = activeBatchOnly && session.largeCaseMode
    ? Math.max(1, Number(session.largeCaseCurrentBatch || 1))
    : null;
  const unfinished = activeBatchNo
    ? unfinishedAll.filter((document) => Number(document.largeBatchNo || 0) === activeBatchNo)
    : unfinishedAll;
  const indexes = {source:new Map(), sourceIdentity:new Map(), document:new Map(), unit:new Map(), evidence:new Map(), semantic:new Map()};
  const push = (map, key, index) => {
    if (!key) return;
    const list = map.get(key) || [];
    list.push(index);
    map.set(key, list);
  };
  for (let index = 0; index < fresh.length; index += 1) {
    const document = fresh[index];
    push(indexes.source, String(document.sourceDocumentId || "").trim(), index);
    push(indexes.sourceIdentity, String(document.sourceIdentityHash || "").trim(), index);
    push(indexes.document, document.documentId, index);
    push(indexes.unit, normalizedBindingValue(document.unitDocumentNumber), index);
    if (!document.isAttachment) push(indexes.evidence, document.identityEvidence, index);
    push(indexes.semantic, semanticRuntimeBindingKey(document), index);
  }

  const usedFresh = new Set();
  const updates = [];
  let bound = 0;
  for (const document of unfinished) {
    const lookups = [
      [indexes.source, String(document.sourceDocumentId || "").trim(), "source-id"],
      [indexes.sourceIdentity, String(document.sourceIdentityHash || "").trim(), "source-identity-hash"],
      [indexes.document, document.documentId, "document-id"],
      [indexes.unit, normalizedBindingValue(document.unitDocumentNumber), "unit-number"],
      [indexes.evidence, document.isAttachment ? "" : document.identityEvidence, "identity-evidence"],
      [indexes.semantic, semanticRuntimeBindingKey(document), "semantic"]
    ];
    let selected = null;
    for (const [map, key, method] of lookups) {
      if (!key) continue;
      const candidates = (map.get(key) || []).filter((index) => !usedFresh.has(index));
      if (candidates.length === 1) { selected = {index:candidates[0], method}; break; }
    }
    if (!selected) {
      updates.push({...document, tabId, runtimeDocumentId:null, runtimeBindingMethod:null});
      continue;
    }
    const current = fresh[selected.index];
    usedFresh.add(selected.index);
    bound += 1;
    updates.push({
      ...document,
      tabId,
      frameId: current.frameId,
      runtimeDocumentId: current.documentId,
      runtimeBindingMethod: selected.method,
      runtimeBoundAt: new Date().toISOString(),
      sourceDocumentId: document.sourceDocumentId || current.sourceDocumentId || null,
      sourceDocumentIdOrigin: document.sourceDocumentIdOrigin || (current.sourceDocumentId ? "scan" : null)
    });
  }
  if (updates.length) await documentStore.patchRuntimeMany(updates);
  const contextMismatch = Boolean(sourceContext && sourceContext.matches === false);
  return {
    bound,
    unmatched: Math.max(0, unfinished.length - bound),
    scanned: fresh.length,
    contextMismatch,
    observedCaseId: contextMismatch ? sourceContext.caseId || null : null,
    observedCourtName: contextMismatch ? (sourceContext.courtName || sourceContext.institutionName || null) : null,
    activeBatchOnly: Boolean(activeBatchNo),
    activeBatchNo,
    unfinishedInScope: unfinished.length,
    unfinishedTotal: unfinishedAll.length
  };
}

function targetedRuntimeBindingEvidence(document = {}) {
  return {
    sourceDocumentId: document.sourceDocumentId || null,
    sourceIdentityHash: document.sourceIdentityHash || null,
    identityEvidence: document.identityEvidence || null,
    documentTitle: document.documentTitle || null,
    documentDate: document.documentDate || null,
    unitDocumentNumber: document.unitDocumentNumber || null,
    sender: document.sender || null,
    documentType: document.documentType || null,
    containerLabel: document.containerLabel || null,
    folderNames: Array.isArray(document.folderNames) ? document.folderNames.slice(0, 24) : [],
    isAttachment: Boolean(document.isAttachment)
  };
}

async function rebindSingleDocument(document, {probeContextOnFailure = true} = {}) {
  if (!document?.sessionId || !Number.isInteger(document?.tabId)) {
    return {ok:false, bound:0, unmatched:1, scanned:0, error:'TARGETED_REBIND_NO_TAB'};
  }
  const session = await sessionStore.get(document.sessionId);
  if (!session) return {ok:false, bound:0, unmatched:1, scanned:0, error:'TARGETED_REBIND_NO_SESSION'};
  const frames = await uyapFrames(document.tabId).catch(() => []);
  const frameIds = [...new Set([
    document.frameId,
    ...(frames || []).map((frame) => frame.frameId)
  ].filter(Number.isInteger))];
  const errors = [];
  for (const frameId of frameIds) {
    try {
      const response = await sendToFrame(document.tabId, frameId, {
        type: MESSAGE.REBIND_DOCUMENT,
        documentId: document.documentId,
        kurumLocator: document?.sourceContext?.kurumLocator || null,
        evidence: targetedRuntimeBindingEvidence(document)
      });
      if (!response?.ok) {
        errors.push(response?.error || `Çerçeve ${frameId}: hedefli evrak bağı kurulamadı`);
        continue;
      }
      const runtimeDocumentId = response.runtimeDocumentId || document.documentId;
      const now = new Date().toISOString();
      const updated = await documentStore.update(document.key, {
        tabId: document.tabId,
        frameId,
        runtimeDocumentId,
        runtimeBindingMethod: response.method || 'targeted-runtime-rebind',
        runtimeBoundAt: now
      });
      await recordDiagnosticEvent(document.sessionId, 'TARGETED_RUNTIME_REBIND', {
        method: response.method || 'targeted-runtime-rebind',
        frameId
      });
      return {
        ok:true,
        bound:1,
        unmatched:0,
        scanned:1,
        document:updated || {...document, frameId, runtimeDocumentId}
      };
    } catch (error) {
      errors.push(String(error));
    }
  }

  let sourceContext = null;
  if (probeContextOnFailure) {
    sourceContext = await probeSessionSourceContext(session, document.tabId).catch(() => null);
    if (sourceContext?.matches === false) {
      return {
        ok:false,
        bound:0,
        unmatched:1,
        scanned:0,
        contextMismatch:true,
        observedCaseId:sourceContext.caseId || null,
        observedCourtName:sourceContext.courtName || sourceContext.institutionName || null,
        error:errors.at(-1) || 'TARGETED_REBIND_CONTEXT_CHANGED'
      };
    }
  }
  await recordDiagnosticEvent(document.sessionId, 'TARGETED_RUNTIME_REBIND_FAILED', {
    error:sanitizeDiagnosticText(errors.at(-1) || 'TARGETED_REBIND_NOT_FOUND', 240)
  });
  return {
    ok:false,
    bound:0,
    unmatched:1,
    scanned:0,
    error:errors.at(-1) || 'TARGETED_REBIND_NOT_FOUND'
  };
}

async function repairMassDownloadStartFailures(sessionId) {
  const documents = await documentStore.bySession(sessionId);
  const recoverable = documents.filter((document) =>
    [STATUS.FAILED, STATUS.RETRY_WAIT].includes(document.status) &&
    STRUCTURAL_START_ERRORS.has(document.errorCode)
  );
  const threshold = Math.max(3, Math.min(25, Math.ceil(documents.length * 0.01)));
  if (recoverable.length < threshold) return 0;
  const now = new Date().toISOString();
  await documentStore.putMany(recoverable.map((document) => ({
    ...document,
    status: STATUS.QUEUED,
    attempts: 0,
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryStrategy: null,
    retryStrategyHistory: [],
    retryExhausted: false,
    attemptStrategy: null,
    startedAt: null,
    errorCode: null,
    errorMessage: null,
    updatedAt: now
  })));
  await sessionStore.update(sessionId, {
    recoveredMassStartFailures: recoverable.length,
    recoveredMassStartFailuresAt: now,
    consecutiveStartFailures: 0
  });
  return recoverable.length;
}

function normalizedKurumCourt(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase('tr-TR');
}

function kurumContextMatches(sessionLike, pageLike) {
  const expectedCase = String(sessionLike?.caseId || '').replace(/\s+/g, '');
  const actualCase = String(pageLike?.caseId || '').replace(/\s+/g, '');
  if (!expectedCase || !actualCase || expectedCase !== actualCase) return false;
  const sourceSystem = String(sessionLike?.sourceSystem || pageLike?.sourceSystem || '').toUpperCase();
  if (sourceSystem !== 'UYAP_KURUM') {
    return normalizedKurumCourt(sessionLike?.courtName || sessionLike?.institutionName) === normalizedKurumCourt(pageLike?.courtName || pageLike?.institutionName);
  }
  const expectedCourt = normalizedKurumCourt(sessionLike?.courtName || sessionLike?.institutionName);
  const actualCourt = normalizedKurumCourt(pageLike?.courtName || pageLike?.institutionName);
  const generic = (value) => !value || value === 'uyap kurum';
  // caseId remains mandatory. Court is additionally mandatory whenever both
  // sides expose a concrete institution; generic fallback must not create a
  // false "source page not ready" state.
  return generic(expectedCourt) || generic(actualCourt) || expectedCourt === actualCourt;
}

function bindingIsComplete(binding) {
  return Boolean(binding?.scanned) && binding.bound > 0 && binding.unmatched === 0;
}

async function pauseForUnsafeBinding(sessionId, binding, reason = "SOURCE_REBIND_REQUIRED") {
  await clearSessionRuntimeAlarms(sessionId);
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: reason,
    pausedAt: new Date().toISOString(),
    runtimeBound: binding?.bound || 0,
    runtimeUnmatched: binding?.unmatched || 0,
    runtimeScanned: binding?.scanned || 0
  });
}

function startPathRecoveryDelayMs(count = 0) {
  const step = Math.max(0, Math.trunc(Number(count) || 0));
  return [300, 800, 2_000, 5_000][Math.min(step, 3)];
}

function sourceWaitRecoveryDelayMs(count = 0) {
  const step = Math.max(0, Math.trunc(Number(count) || 0));
  return [800, 2_000, 5_000, 10_000, 30_000][Math.min(step, 4)];
}

function recoveryProbeDelayMs(count = 0) {
  const step = Math.max(0, Math.trunc(Number(count) || 0));
  return [800, 2_000, 5_000, 10_000, 30_000][Math.min(step, 4)];
}

function sourceRebindRecoveryDelayMs(count = 0, total = 0) {
  const step = Math.max(0, Math.trunc(Number(count) || 0));
  const large = Math.max(0, Number(total || 0)) > 500;
  const profile = large
    ? [1_500, 3_000, 8_000, 15_000, 30_000]
    : [500, 1_500, 3_000, 8_000, 15_000];
  return profile[Math.min(step, profile.length - 1)];
}

async function enterStartPathRecovery(sessionId, errorMessage = null, binding = null) {
  if (binding?.contextMismatch) return pauseForSourceContextChanged(sessionId, binding);
  const session = await sessionStore.get(sessionId);
  const recoveryCount = Math.max(0, Number(session?.startPathRecoveryCount || 0));
  const delayMs = startPathRecoveryDelayMs(recoveryCount);
  await clearPendingDownload();
  await Promise.all([
    clearOperationalAlarm(queueWakeAlarmName(sessionId)),
    clearOperationalAlarm(clickTimeoutAlarmName(sessionId)),
    clearOperationalAlarm(watchdogAlarmName(sessionId))
  ]);
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.RECOVERING,
    pauseReason: "START_PATH_DEGRADED",
    recoveryStartedAt: session?.recoveryStartedAt || new Date().toISOString(),
    resumeAfterRecovery: true,
    consecutiveStartFailures: 0,
    startPathRecoveryCount: recoveryCount + 1,
    startPathNextRetryAt: new Date(Date.now() + delayMs).toISOString(),
    queueError: errorMessage ? String(errorMessage).slice(0, 1000) : null,
    runtimeBound: binding?.bound ?? session?.runtimeBound ?? null,
    runtimeUnmatched: binding?.unmatched ?? session?.runtimeUnmatched ?? null,
    runtimeScanned: binding?.scanned ?? session?.runtimeScanned ?? null,
    sessionGuardStatus: "AUTO_RECOVERY"
  });
  await recordDiagnosticEvent(sessionId, "START_PATH_RECOVERY", {
    delayMs,
    recoveryCount: recoveryCount + 1,
    error: sanitizeDiagnosticText(errorMessage || "START_PATH_DEGRADED", 240),
    bound: binding?.bound ?? null,
    unmatched: binding?.unmatched ?? null
  });
  await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + delayMs});
  return false;
}

async function recordStructuralStartFailure(document, errorCode, errorMessage = null) {
  if (!SESSION_WIDE_START_FAILURES.has(errorCode)) {
    // Correlation and timeout failures are isolated to the active document.
    // Reset the circuit counter so they cannot accidentally trip a later
    // source-wide recovery and force an 11k-row runtime rewrite.
    await sessionStore.update(document.sessionId, {consecutiveStartFailures: 0});
    return false;
  }
  const session = await sessionStore.get(document.sessionId);
  const failures = (session?.consecutiveStartFailures || 0) + 1;
  if (failures < START_FAILURE_CIRCUIT_THRESHOLD || session?.status !== SESSION_STATUS.RUNNING) {
    await sessionStore.update(document.sessionId, {consecutiveStartFailures: failures});
    return false;
  }
  // Do not permanently stop a 12k-item job because the live UYAP Save path
  // glitched for a few records. Neutral-requeue the current item and move the
  // session into bounded automatic recovery. No attempt budget is consumed.
  await documentStore.update(document.key, {
    status: STATUS.QUEUED,
    attempts: Math.max(0, (document.attempts || 1) - 1),
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryStrategy: null,
    retryStrategyHistory: [],
    retryExhausted: false,
    attemptStrategy: null,
    startedAt: null,
    errorCode: null,
    errorMessage: null
  });
  await enterStartPathRecovery(document.sessionId, errorMessage || errorCode);
  return true;
}

function isRuntimeRebindError(error) {
  return /Evrak bu çerçevede bulunamadı|UYAP evrak çerçevesi bulunamadı/i.test(String(error || ""));
}

async function getActiveSessionId() {
  const result = await chrome.storage.local.get(ACTIVE_SESSION_KEY);
  return result[ACTIVE_SESSION_KEY] ?? null;
}

export async function suspendActiveSessionForNewJob() {
  const sessionId = await getActiveSessionId();
  if (!sessionId) return null;
  const session = await sessionStore.get(sessionId);
  if (!session) {
    await chrome.storage.local.remove(ACTIVE_SESSION_KEY);
    return null;
  }

  // Switching files is a normal workflow. Preserve the current queue even when
  // it is RUNNING/RECOVERING; only an in-flight browser download is unsafe to
  // detach from its session. This lets the user move from case A to case B and
  // later return to case A without losing verified progress.
  if ((await getPendingDownload()) || await documentStore.hasActiveDownload(sessionId)) {
    throw new Error("Bir evrak şu anda indiriliyor. Yeni dosyaya geçmeden önce birkaç saniye bekleyin; mevcut ilerleme korunuyor.");
  }

  await clearSessionRuntimeAlarms(sessionId);
  const now = new Date().toISOString();
  const switchPausesSession = [SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING, SESSION_STATUS.READY, SESSION_STATUS.PAUSED].includes(session.status);
  await sessionStore.update(sessionId, {
    status: switchPausesSession ? SESSION_STATUS.PAUSED : session.status,
    pauseReason: switchPausesSession ? "SWITCHED_TO_OTHER_JOB" : session.pauseReason || null,
    pausedAt: switchPausesSession ? now : session.pausedAt || null,
    resumeAfterRecovery: false,
    deactivatedAt: now,
    deactivatedReason: "NEW_JOB_SWITCH",
    sessionGuardStatus: "SWITCHED_OUT"
  });
  await recordDiagnosticEvent(sessionId, "SESSION_SWITCHED_OUT", {
    previousStatus: session.status || null,
    verified: await documentStore.countBySessionStatus(sessionId, STATUS.VERIFIED),
    total: Number(session.total || 0)
  });
  await chrome.storage.local.remove(ACTIVE_SESSION_KEY);
  return {
    sessionId,
    status: session.status,
    caseId: session.caseId,
    courtName: session.courtName,
    institutionName: session.institutionName || session.courtName || null,
    sourceSystem: session.sourceSystem || 'UYAP',
    total: session.total || 0,
    pauseReason: session.pauseReason || null
  };
}

export async function restoreActiveSessionAfterFailedNewJob(previous) {
  const sessionId = previous?.sessionId;
  if (!sessionId) return null;
  const session = await sessionStore.get(sessionId);
  if (!session) return null;
  await chrome.storage.local.set({[ACTIVE_SESSION_KEY]: sessionId});
  const originalStatus = previous?.status || session.status;
  const safeStatus = [SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(originalStatus)
    ? SESSION_STATUS.PAUSED
    : originalStatus;
  await sessionStore.update(sessionId, {
    status: safeStatus,
    pauseReason: safeStatus === SESSION_STATUS.PAUSED
      ? (originalStatus === SESSION_STATUS.PAUSED ? previous?.pauseReason || session.pauseReason || 'USER_PAUSED' : 'NEW_JOB_SWITCH_FAILED')
      : session.pauseReason || null,
    deactivatedAt: null,
    deactivatedReason: null,
    reactivatedAt: new Date().toISOString()
  });
  return getSessionSummary(sessionId);
}

function inventoryComparisonKey(document) {
  const sourceSystem = document?.sourceSystem || 'UYAP';
  const institution = document?.institutionName || document?.courtName || '';
  const caseId = document?.caseId || '';
  const sourceId = String(document?.sourceDocumentId || '').trim();
  if (sourceId) return `${sourceSystem}|${institution}|${caseId}|source:${sourceId}`;
  const sourceIdentity = String(document?.sourceIdentityHash || '').trim();
  if (sourceIdentity) return `${sourceSystem}|${institution}|${caseId}|identity:${sourceIdentity}`;
  return `${sourceSystem}|${institution}|${caseId}|document:${document?.documentId || ''}`;
}

function resumableSessionIdentityMatches(session, documents) {
  const first = Array.isArray(documents) ? documents[0] : null;
  if (!session || !first) return false;
  if ((session.sourceSystem || 'UYAP') !== (first.sourceSystem || 'UYAP')) return false;
  if (String(session.caseId || '') !== String(first.caseId || '')) return false;
  const expected = normalizedBindingValue(session.institutionName || session.courtName || '');
  const actual = normalizedBindingValue(first.institutionName || first.courtName || '');
  if (!expected || !actual) return false;
  return expected === actual;
}

function resumeInventoryKey(document) {
  // documentId is the persistence identity of a record and is intentionally
  // derived from stable UYAP metadata. Kurum's evrak_id is an encrypted,
  // render-scoped token; sourceIdentityHash therefore changes after reopening
  // the same case and MUST NOT be the primary cross-session resume key.
  const documentId = String(document?.documentId || '').trim();
  if (documentId) return `document:${documentId}`;
  const sourceId = String(document?.sourceDocumentId || '').trim();
  if (sourceId) return `source:${sourceId}`;
  const evidence = String(document?.identityEvidence || '').trim();
  if (evidence) return `evidence:${evidence}`;
  const sourceIdentity = String(document?.sourceIdentityHash || '').trim();
  return sourceIdentity ? `runtime-token:${sourceIdentity}` : 'unknown';
}

async function findResumableSessionForInventory(documents, excludeSessionIds = [], preferredSessionId = null) {
  const excluded = new Set((excludeSessionIds || []).filter(Boolean));
  const sessions = await sessionStore.all();
  const orderedSessions = preferredSessionId
    ? [...(sessions || [])].sort((a,b) => (a?.sessionId === preferredSessionId ? -1 : b?.sessionId === preferredSessionId ? 1 : 0))
    : (sessions || []);
  const candidates = [];
  for (const session of orderedSessions) {
    if (!session?.sessionId || excluded.has(session.sessionId)) continue;
    if (preferredSessionId && session.sessionId !== preferredSessionId) continue;
    if (session.identitySchemaVersion !== 3) continue;
    if (session.status === SESSION_STATUS.COMPLETED || session.archiveStatus === 'VERIFIED_COMPLETE') continue;
    if (!resumableSessionIdentityMatches(session, documents)) continue;
    const persisted = await documentStore.bySession(session.sessionId);
    if (!persisted.length) continue;
    const verified = persisted.filter((document) => document.status === STATUS.VERIFIED).length;
    const skipped = persisted.filter((document) => document.status === STATUS.SKIPPED).length;
    const unfinished = persisted.length - verified - skipped;
    // Completed-but-unpackaged/incomplete sessions are still useful to reopen;
    // otherwise require unfinished work so an ancient empty history item is not
    // selected over a meaningful queue.
    if (unfinished <= 0 && session.status !== SESSION_STATUS.INCOMPLETE) continue;
    candidates.push({
      session,
      persisted,
      verified,
      skipped,
      unfinished,
      progress: verified + skipped,
      updatedAtMs: Date.parse(session.updatedAt || session.createdAt || 0) || 0
    });
  }
  candidates.sort((a, b) => (b.progress - a.progress) || (b.updatedAtMs - a.updatedAtMs));
  return candidates[0] || null;
}

function freshRecordForExistingSession(raw, sessionId, tabId, now) {
  const documentId = String(raw.documentId);
  const record = {
    ...raw,
    sourceSystem: raw.sourceSystem || 'UYAP',
    institutionName: raw.institutionName || raw.courtName || null,
    sourceContext: raw.sourceContext || null,
    sourceDocumentIdOrigin: raw.sourceDocumentIdOrigin || (raw.sourceDocumentId ? 'scan' : null),
    key: `${sessionId}:${documentId}`,
    documentId,
    sessionId,
    tabId,
    targetPath: buildTargetPath({...raw, documentId}),
    downloadId: null,
    status: STATUS.QUEUED,
    attempts: 0,
    warnings: [],
    errorCode: null,
    discoveredAt: now,
    updatedAt: now
  };
  record.sourceFingerprint = sourceFingerprint(record);
  return record;
}

export async function resumeExistingSessionForInventory({documents, tabId, inventoryEvidence=[], preferredSessionId=null} = {}) {
  if (!Array.isArray(documents) || !documents.length || !Number.isInteger(tabId)) return null;
  const candidate = await findResumableSessionForInventory(documents, [], preferredSessionId);
  if (!candidate) return null;

  const {session, persisted} = candidate;
  if ((await getPendingDownload()) || await documentStore.hasActiveDownload(session.sessionId)) return null;

  const now = new Date().toISOString();
  const existingByKey = new Map(persisted.map((document) => [resumeInventoryKey(document), document]));
  const existingByDocumentId = new Map(persisted
    .filter((document) => document?.documentId)
    .map((document) => [String(document.documentId), document]));
  const existingBySourceId = new Map(persisted
    .filter((document) => String(document?.sourceDocumentId || '').trim())
    .map((document) => [String(document.sourceDocumentId).trim(), document]));
  const matchedPersistedStorageKeys = new Set();
  const updates = [];
  const additions = [];

  for (const raw of documents) {
    const resumeKey = resumeInventoryKey(raw);
    // First match the exact persisted document identity. This also repairs
    // v2.8.19/v2.8.28 sessions where a changing encrypted evrak token made
    // every reopened document look new even though documentId was identical.
    let existing = raw?.documentId ? existingByDocumentId.get(String(raw.documentId)) : null;
    if (!existing) existing = existingByKey.get(resumeKey);
    if (!existing && String(raw?.sourceDocumentId || '').trim()) {
      existing = existingBySourceId.get(String(raw.sourceDocumentId).trim()) || null;
    }
    if (!existing) {
      const addition = freshRecordForExistingSession(raw, session.sessionId, tabId, now);
      // Storage-key collision means this is not a new source document. Never
      // overwrite an existing VERIFIED/SKIPPED row with a fresh QUEUED row.
      const collision = existingByDocumentId.get(String(addition.documentId));
      if (collision) existing = collision;
      else {
        additions.push(addition);
        continue;
      }
    }

    matchedPersistedStorageKeys.add(existing.key);
    const transient = [STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING, STATUS.COMPLETED].includes(existing.status);
    updates.push({
      ...existing,
      tabId,
      frameId: raw.frameId,
      runtimeDocumentId: raw.documentId,
      runtimeBindingMethod: 'resume-inventory',
      runtimeBoundAt: now,
      sourceContext: raw.sourceContext || existing.sourceContext || null,
      sourceDocumentId: raw.sourceDocumentId || existing.sourceDocumentId || null,
      sourceDocumentIdOrigin: raw.sourceDocumentId ? 'scan' : existing.sourceDocumentIdOrigin || null,
      // The Kurum token is runtime-scoped. Keep the CURRENT token for binding,
      // but never use it as the cross-session persistence key.
      sourceIdentityHash: raw.sourceIdentityHash || existing.sourceIdentityHash || null,
      identityEvidence: raw.identityEvidence || existing.identityEvidence || null,
      folderNames: raw.folderNames || existing.folderNames || [],
      documentTitle: raw.documentTitle || existing.documentTitle || null,
      documentDate: raw.documentDate || existing.documentDate || null,
      documentType: raw.documentType || existing.documentType || null,
      unitDocumentNumber: raw.unitDocumentNumber || existing.unitDocumentNumber || null,
      sender: raw.sender || existing.sender || null,
      status: transient ? STATUS.QUEUED : existing.status,
      downloadId: transient ? null : existing.downloadId,
      attemptId: transient ? null : existing.attemptId,
      stagingPath: transient ? null : existing.stagingPath,
      startedAt: transient ? null : existing.startedAt,
      updatedAt: now
    });
  }

  const removed = persisted.filter((document) => !matchedPersistedStorageKeys.has(document.key));
  const removedUnfinished = removed.filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status));
  if (updates.length) await documentStore.putMany(updates);
  if (additions.length) await documentStore.putMany(additions);

  let persistedAfterMerge = await documentStore.bySession(session.sessionId);
  const resumeLargeCaseMode = persistedAfterMerge.length >= LARGE_CASE_THRESHOLD;
  if (resumeLargeCaseMode) {
    const needsBatchRepair = persistedAfterMerge.some((document) => !Number.isInteger(document.largeBatchNo) || Number(document.largeBatchNo) < 1);
    if (needsBatchRepair) {
      const repaired = persistedAfterMerge.map((document, index) => ({
        ...document,
        largeBatchNo: Math.floor(index / LARGE_CASE_BATCH_SIZE) + 1
      }));
      await documentStore.putManyChunked(repaired, LARGE_CASE_BATCH_SIZE);
      persistedAfterMerge = repaired;
    }
  }
  const verified = persistedAfterMerge.filter((document) => document.status === STATUS.VERIFIED).length;
  const skipped = persistedAfterMerge.filter((document) => document.status === STATUS.SKIPPED).length;
  const verifiedArchiveBytes = persistedAfterMerge
    .filter((document) => [STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status))
    .reduce((sum, document) => sum + Math.max(0, Number(document.actualSize || 0)), 0);
  // Never derive total from old-count + additions: an earlier bug could count a
  // storage-key collision as an addition and inflate 390 -> 780. IndexedDB is
  // the authority for how many records actually exist.
  const total = persistedAfterMerge.length;
  const resumePartitionedArchiveMode = archiveWillBePartitioned(total, verifiedArchiveBytes);

  await clearSessionRuntimeAlarms(session.sessionId);
  await chrome.storage.local.set({[ACTIVE_SESSION_KEY]: session.sessionId});
  const allTerminal = Number(verified || 0) + Number(skipped || 0) >= total;
  const previousPacingMode = String(session?.pacing?.mode || "normal");
  const resumedPacingMode = !resumePartitionedArchiveMode && !["cautious", "backoff"].includes(previousPacingMode)
    ? "fast"
    : previousPacingMode;
  const resumedStatus = removedUnfinished.length
    ? SESSION_STATUS.PAUSED
    : (allTerminal && session.status === SESSION_STATUS.INCOMPLETE ? SESSION_STATUS.INCOMPLETE : SESSION_STATUS.READY);
  await sessionStore.update(session.sessionId, {
    tabId,
    inventoryEvidence,
    total,
    largeCaseMode: resumeLargeCaseMode,
    largeCaseThreshold: LARGE_CASE_THRESHOLD,
    largeCaseBatchSize: resumeLargeCaseMode ? LARGE_CASE_BATCH_SIZE : null,
    largeCaseCurrentBatch: resumeLargeCaseMode ? Math.max(1, Number(session.largeCaseCurrentBatch || 1)) : 0,
    largeCaseTotalBatches: resumeLargeCaseMode ? Math.max(1, Math.ceil(total / LARGE_CASE_BATCH_SIZE)) : 0,
    largeCaseCompletedBatches: resumeLargeCaseMode ? Math.max(0, Number(session.largeCaseCompletedBatches || 0)) : 0,
    largeCaseBatchCooldownMs: 0,
    largeCaseBatchTransitionMode: resumeLargeCaseMode ? "adaptive" : null,
    largeCaseModeVersion: resumeLargeCaseMode ? 2 : 0,
    partitionedArchiveMode: resumePartitionedArchiveMode,
    verifiedArchiveBytes,
    downloadPacingProfile: resumePartitionedArchiveMode ? "partitioned-controlled" : "standard-fast",
    pacing: { ...(session.pacing || {}), mode: resumedPacingMode, updatedAt: now },
    status: resumedStatus,
    pauseReason: removedUnfinished.length ? 'SOURCE_INVENTORY_CHANGED' : null,
    pausedAt: removedUnfinished.length ? now : null,
    deactivatedAt: null,
    deactivatedReason: null,
    reactivatedAt: now,
    resumedExisting: true,
    resumeDetectedAt: now,
    resumeFromVerified: Number(verified || 0),
    resumeFromSkipped: Number(skipped || 0),
    resumeAddedDocuments: additions.length,
    resumeMatchedDocuments: updates.length,
    resumeTotalReconciledFrom: Number(session.total || 0),
    resumeTotalReconciledTo: total,
    resumeMissingDocuments: removed.length,
    resumeMissingUnfinished: removedUnfinished.length,
    recoveryStartedAt: null,
    resumeAfterRecovery: false,
    startPathRecoveryCount: 0,
    queueRuntimeRecoveryCount: 0,
    sessionGuardStatus: removedUnfinished.length ? 'INVENTORY_CHANGED' : 'RESUME_READY',
    queueError: removedUnfinished.length
      ? `${removedUnfinished.length} yarım evrak güncel UYAP ağacında görünmüyor. Doğrulanmış kayıtlar korunuyor; envanter değişikliği incelenmeli.`
      : null
  });

  let binding = {bound:0, unmatched:0, scanned:documents.length};
  if (!removedUnfinished.length) {
    let reboundSession = await sessionStore.get(session.sessionId);
    if (reboundSession?.largeCaseMode) {
      reboundSession = await alignLargeCaseBatchToPendingWork(session.sessionId, reboundSession);
    }
    binding = await refreshSessionRuntimeBindings(session.sessionId, tabId, {
      activeBatchOnly: Boolean(reboundSession?.largeCaseMode)
    });
    if (binding.contextMismatch) {
      await pauseForSourceContextChanged(session.sessionId, binding);
    } else if (binding.unmatched > 0) {
      await pauseForUnsafeBinding(session.sessionId, binding, 'SOURCE_REBIND_REQUIRED');
    } else {
      await sessionStore.update(session.sessionId, {
        runtimeBound: binding.bound,
        runtimeUnmatched: binding.unmatched,
        runtimeScanned: binding.scanned,
        observedCaseId: null
      });
    }
  }

  await recordDiagnosticEvent(session.sessionId, 'SESSION_REACTIVATED', {
    verified: Number(verified || 0),
    skipped: Number(skipped || 0),
    added: additions.length,
    missing: removed.length,
    missingUnfinished: removedUnfinished.length,
    bound: Number(binding.bound || 0),
    unmatched: Number(binding.unmatched || 0),
    scanned: Number(binding.scanned || documents.length),
    activeBatchOnly: Boolean(binding.activeBatchOnly),
    activeBatchNo: Number(binding.activeBatchNo || 0),
    unfinishedInScope: Number(binding.unfinishedInScope || 0),
    unfinishedTotal: Number(binding.unfinishedTotal || 0)
  });
  return getSessionSummary(session.sessionId);
}

export async function compareSessionInventories(previousSessionId, currentSessionId) {
  if (!previousSessionId || !currentSessionId) return null;
  const [previousSession, currentSession, previousDocuments, currentDocuments] = await Promise.all([
    sessionStore.get(previousSessionId),
    sessionStore.get(currentSessionId),
    documentStore.bySession(previousSessionId),
    documentStore.bySession(currentSessionId)
  ]);
  if (!previousSession || !currentSession) return null;
  const sameSource =
    (previousSession.sourceSystem || 'UYAP') === (currentSession.sourceSystem || 'UYAP') &&
    previousSession.caseId === currentSession.caseId &&
    (previousSession.institutionName || previousSession.courtName || '') ===
      (currentSession.institutionName || currentSession.courtName || '');
  if (!sameSource) return {
    sameSource:false,
    previousTotal: previousDocuments.length,
    currentTotal: currentDocuments.length,
    added:0,
    removed:0,
    unchanged:0
  };

  const previousMap = new Map(previousDocuments.map(document => [inventoryComparisonKey(document), document]));
  const currentMap = new Map(currentDocuments.map(document => [inventoryComparisonKey(document), document]));
  const added = [...currentMap.entries()].filter(([key]) => !previousMap.has(key)).map(([,document]) => document);
  const removed = [...previousMap.entries()].filter(([key]) => !currentMap.has(key)).map(([,document]) => document);
  const unchanged = [...currentMap.keys()].filter(key => previousMap.has(key)).length;
  const comparison = {
    sameSource:true,
    previousSessionId,
    previousTotal: previousDocuments.length,
    currentTotal: currentDocuments.length,
    added: added.length,
    removed: removed.length,
    unchanged,
    addedSamples: added.slice(0, 8).map(document => ({
      documentId: document.documentId,
      sourceDocumentId: document.sourceDocumentId || null,
      title: document.documentTitle || null
    })),
    removedSamples: removed.slice(0, 8).map(document => ({
      documentId: document.documentId,
      sourceDocumentId: document.sourceDocumentId || null,
      title: document.documentTitle || null
    })),
    comparedAt: new Date().toISOString()
  };
  await sessionStore.update(currentSessionId, {inventoryComparison: comparison});
  return comparison;
}

async function setPendingDownload(value) {
  await chrome.storage.local.set({ [PENDING_DOWNLOAD_KEY]: value });
}

async function clearPendingDownload() {
  try {
    const stored = await chrome.storage.local.get(PENDING_DOWNLOAD_KEY);
    const pending = stored?.[PENDING_DOWNLOAD_KEY] || null;
    if (pending?.attemptId) transientNetworkHints.delete(pending.attemptId);
  } catch { /* storage cleanup still continues */ }
  await chrome.storage.local.remove(PENDING_DOWNLOAD_KEY);
}

export async function getPendingDownload() {
  const result = await chrome.storage.local.get(PENDING_DOWNLOAD_KEY);
  return result[PENDING_DOWNLOAD_KEY] ?? null;
}

async function clearExpiredOrphanPendingDownload(sessionId, {reason = "liveness-guard"} = {}) {
  const pending = await getPendingDownload().catch(() => null);
  if (!pending || pending.sessionId !== sessionId || isLeaseFresh(pending)) return false;
  const document = pending.key ? await documentStore.get(pending.key).catch(() => null) : null;
  const activeStatus = document && [STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING].includes(document.status);
  const sameAttempt = !pending.attemptId || !document?.attemptId || pending.attemptId === document.attemptId;
  if (activeStatus && sameAttempt) return false;
  await clearPendingDownload();
  await recordDiagnosticEvent(sessionId, "STALE_PENDING_DOWNLOAD_CLEARED", {
    reason,
    pendingStatus: pending.status || null,
    hadDownloadId: Number.isInteger(pending.downloadId),
    leaseAgeMs: pending.createdAt ? Math.max(0, Date.now() - Number(pending.createdAt)) : null,
    documentStatus: document?.status || null
  }).catch(() => {});
  return true;
}


// RC15.9 NETWORK OBSERVER --------------------------------------------------
// Observe the browser's real UYAP request lifecycle without blocking or
// modifying any request. The observer is advisory evidence only: final file
// identity, byte validation and archive commit remain fail-closed.
function declaredUyapDocumentKind(value) {
  let url;
  try { url = value instanceof URL ? value : new URL(String(value || "")); } catch { return null; }
  const host = url.hostname.toLowerCase();
  if (!(host === "uyap.gov.tr" || host.endsWith(".uyap.gov.tr"))) return null;
  const path = url.pathname.toLowerCase();
  const mime = String(url.searchParams.get("mimeType") || "").trim().toLowerCase();
  const hasEvrakId = Boolean(String(url.searchParams.get("evrakId") || "").trim());
  const knownDocumentEndpoint = /(?:view_document_brd\.uyap|viewer\.jsp)$/.test(path);
  if (!hasEvrakId || !knownDocumentEndpoint) return null;
  if (mime === "html" || mime === "htm") return "uyap_html_document";
  return null;
}

// RC15.9.6 DOCUMENT-TYPE-TRUTH: the DownloadItem itself is the authoritative
// browser-side evidence for a UYAP document response. Network-observer hints are
// useful but advisory and can miss a request during service-worker timing gaps.
// Read finalUrl/url transiently and persist only the coarse document kind; raw
// UYAP URLs, evrakId/dosyaId values and tokens are never written to storage.
function declaredUyapDocumentKindFromDownload(downloadItem = {}) {
  return declaredUyapDocumentKind(downloadItem?.finalUrl) ||
    declaredUyapDocumentKind(downloadItem?.url) ||
    null;
}

function networkHintScore(details = {}) {
  let url;
  try { url = new URL(String(details.url || "")); } catch { return -1000; }
  const host = url.hostname.toLowerCase();
  if (!(host === "uyap.gov.tr" || host.endsWith(".uyap.gov.tr"))) return -1000;
  const method = String(details.method || "GET").toUpperCase();
  if (!["GET", "POST"].includes(method)) return -1000;
  const path = url.pathname.toLowerCase();
  const full = `${path}${url.search}`.toLowerCase();
  if (/\.(?:css|js|map|woff2?|ttf|ico|svg|webp)(?:$|\?)/i.test(full)) return -1000;
  if (path.endsWith('.ajx') || /(?:dosya|evrak).*_(?:brd|liste|turu)\.ajx$/i.test(path)) return -250;
  let score = 0;
  if (/(?:download|indir|kaydet|evrak|belge|dokuman|document|dosya|getfile|file)/i.test(full)) score += 70;
  if (/(?:goruntule|görüntüle|viewer|content|icerik|içerik)/i.test(full)) score += 35;
  if (/(?:evrak|belge|document|dosya)(?:id|no|key|guid|token)?=/i.test(url.search)) score += 25;
  if (["xmlhttprequest", "fetch", "other", "sub_frame"].includes(String(details.type || ""))) score += 10;
  if (method === "POST") score += 5;
  return score;
}

export async function observeUyapNetworkRequest(details = {}) {
  if (!Number.isInteger(details.tabId) || details.tabId < 0) return false;
  const score = networkHintScore(details);
  if (score < 35) return false;
  const pending = await getPendingDownload();
  if (!pending || pending.tabId !== details.tabId) return false;
  const now = Date.now();
  if (now - Number(pending.createdAt || 0) > Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0))) return false;
  const current = await getPendingDownload();
  if (!current || current.attemptId !== pending.attemptId || current.tabId !== details.tabId) return false;
  const previousScore = Number(current.networkHint?.score || -1000);
  if (score < previousScore) return false;
  const declaredDocumentKind = declaredUyapDocumentKind(details.url);
  const hint = {
    url: String(details.url || ""),
    method: String(details.method || "GET").toUpperCase(),
    type: String(details.type || ""),
    frameId: Number.isInteger(details.frameId) ? details.frameId : null,
    score,
    declaredDocumentKind,
    observedAtMs: now
  };
  transientNetworkHints.set(current.attemptId, hint);
  for (const [attemptId, candidate] of transientNetworkHints) {
    if (now - Number(candidate?.observedAtMs || 0) > NETWORK_HINT_TTL_MS) transientNetworkHints.delete(attemptId);
  }
  // Persist only token-free timing metadata so a service-worker restart can
  // still explain what happened without ever writing the raw URL/query.
  await setPendingDownload({...current, networkHint:{
    method:hint.method, type:hint.type, frameId:hint.frameId, score:hint.score,
    declaredDocumentKind:hint.declaredDocumentKind || null, observedAtMs:hint.observedAtMs
  }, networkObserverSeenAt:now});
  return true;
}

function transientNetworkHintForPending(pending) {
  if (!pending?.attemptId) return null;
  const hint = transientNetworkHints.get(pending.attemptId) || null;
  if (!hint) return null;
  if (Date.now() - Number(hint.observedAtMs || 0) > NETWORK_HINT_TTL_MS) {
    transientNetworkHints.delete(pending.attemptId);
    return null;
  }
  return hint;
}

function strongNetworkFallbackRequest(pending) {
  const hint = transientNetworkHintForPending(pending);
  if (!hint || Number(hint.score || 0) < 100 || String(hint.method || "GET").toUpperCase() !== "GET") return null;
  try { return validateDownloadRequest({url:hint.url, method:"GET"}); } catch { return null; }
}

export async function handleUyapNetworkRedirect(details = {}) {
  if (!Number.isInteger(details.tabId) || !details.redirectUrl) return false;
  const route = classifyAuthRoute(details.redirectUrl);
  if (!route?.loginRequired) return false;
  const sessionId = await getActiveSessionId();
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (!session || session.tabId !== details.tabId) return false;
  if (![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING, SESSION_STATUS.PAUSED].includes(session.status)) return false;
  if (session.status === SESSION_STATUS.PAUSED && session.pauseReason !== "LOGIN_REQUIRED") return false;
  await pauseForLoginRequired(session, {source:"network_redirect", detail:route.kind || "NETWORK_AUTH_REDIRECT"});
  await sessionStore.update(sessionId, {
    sessionGuardStatus:"LOGIN_REQUIRED",
    lastHeartbeatError:route.kind || "NETWORK_AUTH_REDIRECT",
    networkAuthRedirectAt:new Date().toISOString(),
    networkAuthRedirectCount:Number(session.networkAuthRedirectCount || 0) + 1
  });
  return true;
}

export async function handleUyapNetworkCompleted(details = {}) {
  if (!Number.isInteger(details.tabId)) return false;
  const statusCode = Number(details.statusCode || 0);
  if (![401, 403].includes(statusCode)) return false;
  const sessionId = await getActiveSessionId();
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (!session || session.tabId !== details.tabId || ![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) return false;
  // 401/403 on the source main frame is authoritative. For XHR/fetch, require
  // an active Save lease so unrelated portal assets cannot freeze the job.
  const authoritativeFrame = String(details.type || "") === "main_frame";
  const pending = authoritativeFrame ? null : await getPendingDownload();
  if (!authoritativeFrame && (!pending || pending.tabId !== details.tabId || networkHintScore(details) < 35)) return false;
  await pauseForLoginRequired(session, {source:"network_status", detail:`HTTP_${statusCode}`});
  await sessionStore.update(sessionId, {
    sessionGuardStatus:"LOGIN_REQUIRED",
    lastHeartbeatError:`HTTP_${statusCode}`,
    networkAuthStatusAt:new Date().toISOString()
  });
  return true;
}

async function clearSessionFallbackState(sessionId) {
  const stored = await chrome.storage.local.get(RECENT_FALLBACKS_KEY);
  const current = Array.isArray(stored?.[RECENT_FALLBACKS_KEY])
    ? stored[RECENT_FALLBACKS_KEY]
    : [];
  if (!current.length) return;
  const next = current.filter((entry) => entry?.sessionId !== sessionId);
  if (next.length) await chrome.storage.local.set({[RECENT_FALLBACKS_KEY]: next});
  else await chrome.storage.local.remove(RECENT_FALLBACKS_KEY);
}

function resettableDocument(document, now) {
  return {
    ...document,
    status: STATUS.QUEUED,
    attempts: 0,
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryStrategy: null,
    retryStrategyHistory: [],
    retryExhausted: false,
    attemptStrategy: null,
    startedAt: null,
    downloadStartedAtMs: null,
    clickStartedAtMs: null,
    actualFilename: null,
    actualSize: null,
    actualMime: null,
    sha256: null,
    completedAt: null,
    runtimeDocumentId: null,
    runtimeBindingMethod: null,
    runtimeBoundAt: null,
    errorCode: null,
    errorMessage: null,
    warnings: [],
    lastRuntimeResetAt: now,
    lastRuntimeResetPreviousStatus: document.status || null,
    lastRuntimeResetPreviousErrorCode: document.errorCode || null,
    updatedAt: now
  };
}

async function assertRuntimeResetSafe(sessionId, documents) {
  const pending = await getPendingDownload();
  if (pending && pending.sessionId !== sessionId) {
    throw new Error("Başka bir arşiv işine ait indirme tetiklemesi halen açık. Önce onun tamamlanmasını bekleyin.");
  }

  if (pending?.sessionId === sessionId) {
    const pendingAge = Date.now() - Number(pending.createdAt || 0);
    if (Number.isFinite(pendingAge) && pendingAge >= 0 && pendingAge < CLICK_DOWNLOAD_TIMEOUT_MS) {
      throw new Error("Aktif indirme tetiklemesi tamamlanmadan çalışma durumu sıfırlanamaz. Birkaç saniye bekleyip yeniden deneyin.");
    }
  }

  for (const document of documents) {
    if (![STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING].includes(document.status)) continue;
    if (Number.isInteger(document.downloadId) && completingDownloads.has(document.downloadId)) {
      throw new Error("Bir evrakın doğrulama/taşıma işlemi halen sürüyor. Tamamlandıktan sonra sıfırlayın.");
    }
    if (!Number.isInteger(document.downloadId)) continue;
    const [item] = await chrome.downloads.search({id: document.downloadId});
    if (item?.state === "in_progress") {
      throw new Error("Tarayıcıda halen devam eden bir UYAP indirmesi var. Tamamlandıktan sonra sıfırlayın.");
    }
    if (item?.state === "complete") {
      throw new Error("İndirilen bir evrak henüz doğrulama kuyruğundan geçmedi. Doğrulama tamamlandıktan sonra sıfırlayın.");
    }
  }
}

export async function resetSessionRuntime(sessionId = null) {
  const targetSessionId = sessionId || await getActiveSessionId();
  if (!targetSessionId) throw new Error("Sıfırlanacak aktif arşiv işi bulunamadı.");

  const session = await sessionStore.get(targetSessionId);
  if (!session) {
    if ((await getActiveSessionId()) === targetSessionId) {
      await chrome.storage.local.remove(ACTIVE_SESSION_KEY);
    }
    throw new Error("Sıfırlanacak arşiv işi veritabanında bulunamadı.");
  }
  if ([SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) {
    throw new Error("Çalışma durumu sıfırlanmadan önce mevcut işi Duraklat ile durdurun.");
  }

  const documents = await documentStore.bySession(targetSessionId);
  const unfinished = documents.filter((document) =>
    ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status)
  );
  const runtimeResettableStatuses = new Set([
    STATUS.DISCOVERED,
    STATUS.QUEUED,
    STATUS.AWAITING_BROWSER,
    STATUS.DOWNLOADING,
    STATUS.COMPLETED,
    STATUS.RETRY_WAIT
  ]);
  const resettable = unfinished.filter((document) =>
    runtimeResettableStatuses.has(document.status) && !isTerminalHtmlReviewDocument(document)
  );

  await assertRuntimeResetSafe(targetSessionId, resettable);
  if (heartbeatTask) {
    try { await heartbeatTask; } catch { /* reset proceeds after the in-flight health check settles */ }
  }

  if (!resettable.length) {
    await clearSessionRuntimeAlarms(targetSessionId);
    const pending = await getPendingDownload();
    if (pending?.sessionId === targetSessionId) await clearPendingDownload();
    await clearSessionFallbackState(targetSessionId);
    queueRequested = false;
    await restoreArchiveDownloadUi();
    await chrome.storage.local.set({[ACTIVE_SESSION_KEY]: targetSessionId});
    await sessionStore.update(targetSessionId, {
      lastRuntimeResetAt: new Date().toISOString(),
      lastRuntimeResetCount: 0
    });
    await recordDiagnosticEvent(targetSessionId, "RUNTIME_RESET", {resetCount: 0});
    return getSessionSummary(targetSessionId);
  }

  const now = new Date().toISOString();
  await clearSessionRuntimeAlarms(targetSessionId);
  const pending = await getPendingDownload();
  if (pending?.sessionId === targetSessionId) await clearPendingDownload();
  await clearSessionFallbackState(targetSessionId);
  await documentStore.putMany(resettable.map((document) => resettableDocument(document, now)));

  queueRequested = false;
  await restoreArchiveDownloadUi();
  await sessionStore.update(targetSessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: "RUNTIME_RESET",
    pausedAt: now,
    runtimeBound: 0,
    runtimeUnmatched: resettable.length,
    runtimeScanned: 0,
    recoveryStartedAt: null,
    resumeAfterRecovery: false,
    nextQueueWakeAt: null,
    nextHeartbeatAt: null,
    heartbeatFailures: 0,
    heartbeatWarning: null,
    lastHeartbeatError: null,
    consecutiveStartFailures: 0,
    finalRecoveryActive: false,
    finalRecoveryQueued: 0,
    finalRecoveryReason: null,
    finalRecoveryBlockedReason: null,
    archiveStatus: null,
    finalizationError: null,
    unresolved: unfinished.length,
    lastRuntimeResetAt: now,
    lastRuntimeResetCount: resettable.length
  });
  await chrome.storage.local.set({[ACTIVE_SESSION_KEY]: targetSessionId});
  await recordDiagnosticEvent(targetSessionId, "RUNTIME_RESET", {resetCount: resettable.length, preservedReviewCount: unfinished.length - resettable.length});
  return getSessionSummary(targetSessionId);
}

export async function createSession({ documents, tabId, inventoryEvidence=[] }) {
  const previousId = await getActiveSessionId();
  const previous = previousId && await sessionStore.get(previousId);
  if (previous && [SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(previous.status)) {
    throw new Error("Yeni tarama öncesi mevcut işi duraklatın.");
  }
  if (previous?.status === SESSION_STATUS.PAUSED && previous.pauseReason === "LOGIN_REQUIRED") {
    throw new Error("UYAP oturumu sonrası mevcut iş korunuyor. Aynı dava/Evrak ekranını açın; sistem kaldığı yerden devam edecektir.");
  }
  if ((await getPendingDownload()) || (previousId && await documentStore.hasActiveDownload(previousId))) {
    throw new Error("Devam eden indirme tamamlanmadan yeni iş oluşturulamaz.");
  }
  if (!documents?.length) throw new Error("Boş envanterle iş oluşturulamaz.");

  // UYAP Kurum toplu/list ekranları her zaman klasik 2026/123 biçiminde bir
  // dava/esas numarası taşımaz. Böyle bir ekranda evrakları sırf klasik caseId
  // yok diye reddetmek yerine, aynı kurum + temiz sayfa yolu için kararlı bir
  // arşiv kapsamı üret. Bu yalnız Kurum kayıtlarına uygulanır; klasik UYAP
  // fail-closed kimlik kuralı aynen korunur.
  const normalizedDocuments = [];
  for (const raw of documents) {
    const sourceSystem = raw?.sourceSystem || 'UYAP';
    if (sourceSystem === 'UYAP_KURUM' && (!raw.caseId || raw.caseId === 'Dosya_Bilinmiyor')) {
      const institution = raw.institutionName || raw.courtName || 'UYAP Kurum';
      const scopeSeed = `${raw.sourceContext?.pageUrl || ''}|${institution}`;
      const scopeToken = (await diagnosticReference('kurum-scope', scopeSeed)).slice(0, 12);
      const caseId = `Kurum_Arsiv_${scopeToken}`;
      normalizedDocuments.push({
        ...raw,
        caseId,
        courtName: raw.courtName || institution,
        institutionName: institution,
        sourceContext: {
          ...(raw.sourceContext || {}),
          caseId,
          caseIdentityMode: raw.sourceContext?.caseIdentityMode || 'background-page-scope-fallback'
        }
      });
    } else {
      normalizedDocuments.push(raw);
    }
  }
  documents = normalizedDocuments;

  const cases = new Set(documents.map(d => `${d.sourceSystem || 'UYAP'}|${d.courtName || d.institutionName}|${d.caseId}`));
  if (documents.some(d => !d.caseId || !(d.courtName || d.institutionName) || d.caseId === "Dosya_Bilinmiyor" || d.courtName === "Mahkeme_Bilinmiyor")) throw new Error("Dava/dosya kimliği belirlenemedi; indirme başlatılmadı.");
  if (cases.size !== 1) throw new Error("Tek taramada yalnızca bir dava açık olmalı.");
  const sessionId = crypto.randomUUID();
  const now = new Date().toISOString();
  const unique = new Map();
  const largeCaseMode = documents.length >= LARGE_CASE_THRESHOLD;
  const largeCaseBatchSize = largeCaseMode ? LARGE_CASE_BATCH_SIZE : null;

  for (const raw of documents) {
    if (!raw.documentId) throw new Error('Kaynak kimliği olmayan evrak bulundu; envanter oluşturulmadı.');
    const documentId = String(raw.documentId);
    const key = `${sessionId}:${documentId}`;
    const record = {
      ...raw,
      sourceSystem: raw.sourceSystem || 'UYAP',
      institutionName: raw.institutionName || raw.courtName || null,
      sourceContext: raw.sourceContext || null,
      sourceDocumentIdOrigin: raw.sourceDocumentIdOrigin || (raw.sourceDocumentId ? "scan" : null),
      key,
      documentId,
      sessionId,
      tabId,
      targetPath: buildTargetPath({ ...raw, documentId }),
      downloadId: null,
      status: STATUS.QUEUED,
      attempts: 0,
      warnings: [],
      errorCode: null,
      discoveredAt: now,
      updatedAt: now,
      largeBatchNo: largeCaseMode ? (Math.floor(unique.size / LARGE_CASE_BATCH_SIZE) + 1) : 0
    };
    record.sourceFingerprint = sourceFingerprint(record);
    const existing = unique.get(documentId);
    if (existing && existing.identityEvidence !== record.identityEvidence) throw new Error("Çakışan evrak kimliği; tarama tamamlanmadı.");
    unique.set(documentId, record);
  }

  const records = [...unique.values()];
  const partitionedArchiveMode = archiveWillBePartitioned(records.length, 0);
  await documentStore.putManyChunked(records, largeCaseMode ? LARGE_CASE_BATCH_SIZE : 1000);
  const largeCaseTotalBatches = largeCaseMode ? Math.max(1, Math.ceil(records.length / LARGE_CASE_BATCH_SIZE)) : 0;
  const archivePreference = await chrome.storage.local.get([LAST_DOWNLOADS_ROOT_KEY, PORTABLE_RESUME_ENABLED_KEY]);
  await sessionStore.put({
    sessionId,
    inventoryEvidence,
    identitySchemaVersion: 3,
    reliabilitySchemaVersion: RELIABILITY_SCHEMA_VERSION,
    transientNativeCleanupSchemaVersion: 1,
    sourceSystem: records[0]?.sourceSystem || 'UYAP',
    sourceContext: records[0]?.sourceContext || null,
    institutionName: records[0]?.institutionName || records[0]?.courtName || null,
    caseId: records[0]?.caseId || null,
    courtName: records[0]?.courtName || null,
    tabId,
    downloadsRoot: archivePreference[LAST_DOWNLOADS_ROOT_KEY] || null,
    portableResumeEnabled: archivePreference[PORTABLE_RESUME_ENABLED_KEY] === true,
    localArchiveReconciliationVersion: LOCAL_ARCHIVE_RECONCILIATION_VERSION,
    status: SESSION_STATUS.READY,
    pauseReason: null,
    lastHeartbeatAt: now,
    heartbeatFailures: 0,
    sessionGuardStatus: "READY",
    sessionLossCount: 0,
    htmlSessionInterruptions: 0,
    loginRequiredAt: null,
    resumeAfterLogin: false,
    loginNotificationShown: false,
    loginNotificationFallback: false,
    loginNotificationError: null,
    total: records.length,
    executionEngine: "auto-pipeline-v1",
    userSelectableMode: false,
    nativeLaneArchitecture: "dual-fast-bulk",
    pipelineValidationDepthLimit: MAX_PIPELINED_VALIDATIONS,
    largeCaseMode,
    largeCaseThreshold: LARGE_CASE_THRESHOLD,
    largeCaseBatchSize: largeCaseBatchSize,
    largeCaseCurrentBatch: largeCaseMode ? 1 : 0,
    largeCaseTotalBatches,
    largeCaseCompletedBatches: 0,
    largeCaseBatchCooldownMs: 0,
    largeCaseBatchTransitionMode: largeCaseMode ? "adaptive" : null,
    largeCaseModeVersion: largeCaseMode ? 2 : 0,
    partitionedArchiveMode,
    verifiedArchiveBytes: 0,
    downloadPacingProfile: partitionedArchiveMode ? "partitioned-controlled" : "standard-fast",
    pacing: { mode: partitionedArchiveMode ? "normal" : "fast", successStreak: 0, failureStreak: 0, delayMs: 0, updatedAt: now },
    performance: { recentVerifiedAt: [], docsPerMinute: 0, attempts: 0, verifiedAttempts: 0, failedAttempts: 0 },
    frameClientBaseline: getFrameClientStats(),
    recoveryCount: 0,
    healthyVerifiedSinceRecovery: 0,
    finalRecoveryActive: false,
    finalRecoveryPass: 0,
    finalRecoveryMaxPasses: FINAL_RECOVERY_MAX_PASSES,
    finalRecoveryQueued: 0,
    finalRecoveryHistory: [],
    createdAt: now,
    updatedAt: now
  });
  await chrome.storage.local.set({ [ACTIVE_SESSION_KEY]: sessionId });
  await recordDiagnosticEvent(sessionId, "SESSION_CREATED", {
    total: records.length,
    sourceSystem: records[0]?.sourceSystem || "UYAP",
    executionEngine:"auto-pipeline-v1",
    nativeLaneArchitecture:"dual-fast-bulk",
    userSelectableMode:false,
    largeCaseMode,
    largeCaseBatchSize,
    largeCaseTotalBatches,
    partitionedArchiveMode,
    downloadPacingProfile: partitionedArchiveMode ? "partitioned-controlled" : "standard-fast"
  });
  // Scan the physical local archive immediately after inventory creation so the
  // popup tells the truth before the first download starts. This is best-effort:
  // an unavailable agent keeps the queue intact and simply marks the scan state.
  try {
    await syncSessionWithLocalArchive(sessionId, {largeBatchNo:largeCaseMode ? 1 : null, nativePriority:0});
  } catch (error) {
    await sessionStore.update(sessionId, {
      localArchiveScanState: "unavailable",
      localArchiveScanError: sanitizeDiagnosticText(error, 240),
      localArchiveScanCompletedAt: new Date().toISOString()
    });
    await recordDiagnosticEvent(sessionId, "LOCAL_ARCHIVE_INITIAL_SCAN_SKIPPED", {
      error: sanitizeDiagnosticText(error, 240)
    }).catch(()=>{});
  }
  return getSessionSummary(sessionId);
}

async function sessionPageReady(session, tabId = session?.tabId) {
  if (!session || !Number.isInteger(tabId)) return false;
  const frames = await uyapFrames(tabId).catch(() => []);
  for (const frame of frames || []) {
    try {
      const response = await sendToFrame(tabId, frame.frameId, {type: MESSAGE.PROBE_PAGE});
      const data = response?.data;
      if (!response?.ok || !data?.hasDocuments) continue;
      if ((data.sourceSystem || 'UYAP') !== (session.sourceSystem || 'UYAP')) continue;
      if (!kurumContextMatches(session, data)) continue;
      return true;
    } catch { /* next frame */ }
  }
  return false;
}

async function setSessionGuardBadge(loginRequired, session = null, {force = false} = {}) {
  if (typeof chrome?.action?.setBadgeText !== "function") return;
  try {
    // RC15.8.3: badge/icon state is persistent session UI, not a best-effort
    // transient decoration. A stale recovery branch must never clear the red
    // warning while the active job is still paused for LOGIN_REQUIRED.
    if (!loginRequired && !force) {
      const activeSessionId = await getActiveSessionId().catch(() => null);
      const active = activeSessionId ? await sessionStore.get(activeSessionId).catch(() => null) : null;
      if (active?.status === SESSION_STATUS.PAUSED && active?.pauseReason === "LOGIN_REQUIRED") {
        return setSessionGuardBadge(true, active, {force:true});
      }
    }

    if (loginRequired) {
      const sourceLabel = String(session?.sourceSystem || '').toUpperCase() === 'UYAP_KURUM' ? 'UYAP Kurum' : 'UYAP';
      await chrome.action.setBadgeText({text: "!"});
      await chrome.action.setBadgeBackgroundColor?.({color: "#c62828"});
      await chrome.action.setBadgeTextColor?.({color: "#ffffff"});
      await chrome.action.setIcon?.({path: {
        "16":"icons/uyap-warning-16.png",
        "32":"icons/uyap-warning-32.png",
        "48":"icons/uyap-warning-48.png",
        "128":"icons/uyap-warning-128.png"
      }});
      await chrome.action.setTitle?.({title: `${sourceLabel} oturumu sona erdi - yeniden giriş gerekli`});
    } else {
      await chrome.action.setBadgeText({text: ""});
      await chrome.action.setIcon?.({path: {
        "16":"icons/uyap-archive-16.png",
        "32":"icons/uyap-archive-32.png",
        "48":"icons/uyap-archive-48.png",
        "128":"icons/uyap-archive-128.png"
      }});
      await chrome.action.setTitle?.({title: "UYAP Archive AI"});
    }
  } catch {
    // Toolbar state is advisory; queue safety must never depend on UI state.
  }
}

export async function syncSessionGuardUi() {
  const sessionId = await getActiveSessionId().catch(() => null);
  const session = sessionId ? await sessionStore.get(sessionId).catch(() => null) : null;
  const loginRequired = session?.status === SESSION_STATUS.PAUSED && session?.pauseReason === "LOGIN_REQUIRED";
  await setSessionGuardBadge(loginRequired, session, {force:true});
  return {loginRequired, sessionId: session?.sessionId || null};
}

async function clearQueueControlAlarms(sessionId) {
  await Promise.all([
    clearOperationalAlarm(queueWakeAlarmName(sessionId)),
    clearOperationalAlarm(recoveryAlarmName(sessionId)),
    clearOperationalAlarm(queueGuardAlarmName(sessionId)),
    clearOperationalAlarm(`${HEARTBEAT_ALARM_PREFIX}${sessionId}`)
  ]);
  await sessionStore.update(sessionId, {nextQueueWakeAt: null});
}

async function pauseForLoginRequired(session, {source = "heartbeat", detail = null} = {}) {
  if (!session?.sessionId) return false;
  const latest = await sessionStore.get(session.sessionId) || session;
  if ([SESSION_STATUS.COMPLETED, SESSION_STATUS.INCOMPLETE].includes(latest.status)) return false;
  const alreadyPaused = latest.status === SESSION_STATUS.PAUSED && latest.pauseReason === "LOGIN_REQUIRED";
  const now = new Date().toISOString();
  await clearQueueControlAlarms(latest.sessionId);
  await sessionStore.update(latest.sessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: "LOGIN_REQUIRED",
    pausedAt: latest.pausedAt || now,
    loginRequiredAt: latest.loginRequiredAt || now,
    resumeAfterLogin: true,
    heartbeatWarning: null,
    lastSessionLossAt: now,
    lastSessionLossSource: source,
    lastSessionLossDetail: detail,
    sessionLossCount: Number(latest.sessionLossCount || 0) + (alreadyPaused ? 0 : 1)
  });
  await setSessionGuardBadge(true, latest);
  // Always ensure the cross-tab alert exists. If the first Windows/Chrome
  // notification attempt failed, a repeated DOM/heartbeat signal gets one
  // safe retry without creating duplicates when an alert is already visible.
  const notification = await showLoginRequiredNotification(latest, {ensureOnly:alreadyPaused});
  await sessionStore.update(latest.sessionId, {
    loginNotificationShown: notification?.shown === true || notification?.existing === true,
    loginNotificationFallback: notification?.fallback === true,
    loginNotificationError: notification?.shown === true || notification?.existing === true ? null : String(notification?.reason || 'NOTIFICATION_NOT_SHOWN').slice(0, 500),
    loginNotificationLastAttemptAt: new Date().toISOString()
  });
  if (!alreadyPaused) await recordDiagnosticEvent(latest.sessionId, "LOGIN_REQUIRED", {
    source,
    detail: sanitizeDiagnosticText(detail || "LOGIN_REQUIRED", 240),
    notificationShown: notification?.shown === true || notification?.existing === true,
    notificationFallback: notification?.fallback === true,
    notificationError: notification?.shown === true || notification?.existing === true ? null : sanitizeDiagnosticText(notification?.reason || 'NOTIFICATION_NOT_SHOWN', 240)
  });
  await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${latest.sessionId}`, {
    when: Date.now() + LOGIN_WATCH_INTERVAL_MS
  });
  const awaiting = await documentStore.firstBySessionStatus(latest.sessionId, STATUS.AWAITING_BROWSER);
  if (awaiting) {
    // Give a Save request that was already issued a short chance to surface as
    // a real Chrome download. No new document starts while LOGIN_REQUIRED, so
    // this drain window cannot race with a second attempt for the same record.
    await scheduleOperationalAlarm(sessionDrainAlarmName(latest.sessionId), {
      when: Date.now() + SESSION_LOSS_DRAIN_MS
    });
  }
  // Network work must stop when authentication is gone, but local work does not
  // have to. Keep already-downloaded validation and at most the current/next
  // local reconciliation batch moving on the independent native lanes.
  void continueLocalWorkDuringLoginPause(latest.sessionId);
  return false;
}

export async function handleSessionExpiredSignal(tabId, evidence = null) {
  const sessionId = await getActiveSessionId();
  if (!sessionId || !Number.isInteger(tabId)) return false;
  const session = await sessionStore.get(sessionId);
  if (!session) return false;
  // A login page in another UYAP tab must not freeze an unrelated healthy job.
  // The source tab itself is authoritative; tab-loss recovery handles closed tabs.
  if (session.tabId !== tabId) return false;
  if (![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING, SESSION_STATUS.PAUSED].includes(session.status)) return false;
  if (session.status === SESSION_STATUS.PAUSED && session.pauseReason !== "LOGIN_REQUIRED") return false;
  const detail = String(evidence?.text || evidence?.title || "UYAP_SESSION_EXPIRED_DOM_SIGNAL").slice(0, 500);
  await pauseForLoginRequired(session, {source: "dom_warning", detail});
  await sessionStore.update(sessionId, {
    sessionGuardStatus: "LOGIN_REQUIRED",
    lastHeartbeatError: "LOGIN_REQUIRED_DOM_WARNING",
    lastSessionWarningText: detail
  });
  return true;
}

export async function handleSessionAuthNavigation(tabId, url) {
  const route = classifyAuthRoute(url);
  if (!route?.loginRequired || !Number.isInteger(tabId)) return false;
  const sessionId = await getActiveSessionId();
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (!session) return false;

  const sourceSystem = String(session.sourceSystem || '').toUpperCase();
  if (route.sourceSystem === 'UYAP_KURUM' && sourceSystem !== 'UYAP_KURUM') return false;
  if (route.sourceSystem === 'UYAP' && sourceSystem === 'UYAP_KURUM') return false;

  // Never freeze a healthy job because the user opened a login page in some
  // unrelated tab. Only the preserved source/recovery tab is authoritative.
  if (session.tabId !== tabId) return false;
  if (![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING, SESSION_STATUS.PAUSED].includes(session.status)) return false;
  if (session.status === SESSION_STATUS.PAUSED && session.pauseReason !== 'LOGIN_REQUIRED') return false;

  await pauseForLoginRequired(session, {
    source: route.kind === 'KURUM_EDEVLET_ENTRY' ? 'navigation_edevlet_kurum_entry' : 'navigation_auth_route',
    detail: route.kind
  });
  await sessionStore.update(sessionId, {
    sessionGuardStatus: 'LOGIN_REQUIRED',
    lastHeartbeatError: route.kind,
    preferredLoginRoute: route.kind === 'KURUM_EDEVLET_ENTRY' ? 'edevlet' : (route.sourceSystem === 'UYAP_KURUM' ? 'direct' : null)
  });
  return true;
}

async function requeueDocumentAfterSessionLoss(document, reason = "LOGIN_REQUIRED") {
  const latest = await documentStore.get(document.key) || document;
  const now = new Date().toISOString();
  await documentStore.update(latest.key, {
    status: STATUS.QUEUED,
    attempts: Math.max(0, Number(latest.attempts || 1) - 1),
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryStrategy: null,
    retryStrategyHistory: [],
    retryExhausted: false,
    attemptStrategy: null,
    startedAt: null,
    completedAt: null,
    errorCode: null,
    errorMessage: null,
    sessionLossCount: Number(latest.sessionLossCount || 0) + 1,
    lastSessionLossAt: now,
    lastSessionLossReason: reason,
    updatedAt: now
  });
}

async function findReadySessionTab(session, preferredTabId = null) {
  const candidates = [];
  const push = (value) => {
    if (Number.isInteger(value) && !candidates.includes(value)) candidates.push(value);
  };
  push(preferredTabId);
  push(session?.tabId);
  try {
    if (typeof chrome?.tabs?.query === "function") {
      const tabs = await chrome.tabs.query({url: ["https://*.uyap.gov.tr/*", "https://uyap.gov.tr/*"]});
      for (const tab of tabs || []) push(tab?.id);
    }
  } catch {
    // Host-permission or browser differences: preferred/current tab still work.
  }
  for (const tabId of candidates) {
    try {
      if (await sessionPageReady(session, tabId)) return tabId;
    } catch {
      // Try the next UYAP tab.
    }
  }
  return null;
}

export async function refreshSessionContext(sessionId, tabId) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error("Aktif arşiv işi bulunamadı.");
  if (!Number.isInteger(tabId)) throw new Error("Açık UYAP sekmesi bulunamadı.");
  if (session.status === SESSION_STATUS.RUNNING) {
    throw new Error("İşlem çalışırken bağlantı kontrolü yapılamaz. Önce Duraklat'a basın.");
  }
  const binding = await refreshSessionRuntimeBindings(sessionId, tabId, { activeBatchOnly: Boolean(session.largeCaseMode) });
  if (binding.contextMismatch) {
    await pauseForSourceContextChanged(sessionId, binding);
    throw new Error(`Açık UYAP Kurum dosyası değişti. Aktif iş ${session.caseId || ''}, açık dosya ${binding.observedCaseId || 'bilinmiyor'}. Aktif iş güvenli biçimde duraklatıldı.`);
  }
  if (!binding.scanned) {
    const shouldResume = session.status === SESSION_STATUS.RECOVERING && session.resumeAfterRecovery === true;
    await sessionStore.update(sessionId, {
      tabId,
      runtimeBound:0,
      runtimeUnmatched:null,
      runtimeScanned:0,
      status: SESSION_STATUS.RECOVERING,
      pauseReason: "SOURCE_PAGE_NOT_READY",
      recoveryStartedAt: session.recoveryStartedAt || new Date().toISOString(),
      resumeAfterRecovery: shouldResume,
      lastContextCheckAt: new Date().toISOString()
    });
    await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 800});
    throw new Error(`UYAP'ta ${session.courtName || "ilgili mahkeme"} ${session.caseId || ""} dosyasını açıp Evrak sekmesine geçin. Arşiv, doğrulanan kayıtlar ve kuyruk korunuyor; ekran görünür olduğunda bağlantı yeniden kontrol edilecek.`);
  }
  await clearOperationalAlarm(recoveryAlarmName(sessionId));
  await sessionStore.update(sessionId, {
    tabId,
    runtimeBound: binding.bound,
    runtimeUnmatched: binding.unmatched,
    runtimeScanned: binding.scanned,
    status: SESSION_STATUS.PAUSED,
    pauseReason: binding.unmatched === 0 ? "CONNECTION_VERIFIED" : "SOURCE_REBIND_REQUIRED",
    recoveryStartedAt: null,
    resumeAfterRecovery: false,
    lastContextCheckAt: new Date().toISOString()
  });
  return getSessionSummary(sessionId);
}

async function maybeRefreshPreservedSourceAfterAuth(session, observedTabId = null) {
  if (!session || !Number.isInteger(observedTabId) || observedTabId !== session.authTabId) return false;
  if (!Number.isInteger(session.tabId) || session.tabId === observedTabId) return false;
  let authTab = null;
  try { authTab = await chrome.tabs.get(observedTabId); } catch { return false; }
  if (!isUyapHostUrl(authTab?.url) || classifyAuthRoute(authTab?.url)?.loginRequired) return false;
  const authProbe = await probeSessionHealth(session, observedTabId).catch(() => null);
  if (!authProbe?.healthy || authProbe?.loginRequired) return false;

  // Shared browser auth state is back. Prefer the preserved case tab if it is
  // already healthy; otherwise reload only that tab once so its original case
  // route can rebuild under the new authenticated session.
  if (await sessionPageReady(session, session.tabId).catch(()=>false)) return false;
  const lastReload = Date.parse(session.authSourceReloadAt || "");
  if (Number.isFinite(lastReload) && Date.now() - lastReload < 5_000) return false;
  try {
    await chrome.tabs.reload(session.tabId);
    await sessionStore.update(session.sessionId, {
      authRecoveredAt:new Date().toISOString(),
      authSourceReloadAt:new Date().toISOString(),
      sessionGuardStatus:"LOGIN_SOURCE_RELOADING"
    });
    await recordDiagnosticEvent(session.sessionId, "AUTH_TAB_RECOVERED", {
      authTabId:observedTabId,
      sourceTabId:session.tabId,
      action:"reload-preserved-source"
    }).catch(()=>{});
    return true;
  } catch {
    return false;
  }
}

export async function attemptResumeLoginSession(tabId = null) {
  const sessionId = await getActiveSessionId();
  if (!sessionId) return false;
  const session = await sessionStore.get(sessionId);
  if (session?.status !== SESSION_STATUS.PAUSED || session.pauseReason !== "LOGIN_REQUIRED") return false;
  await setSessionGuardBadge(true, session);
  if (session.loginNotificationShown !== true || session.loginNotificationError) {
    const notification = await showLoginRequiredNotification(session, {ensureOnly:true});
    await sessionStore.update(sessionId, {
      loginNotificationShown: notification?.shown === true || notification?.existing === true,
      loginNotificationFallback: notification?.fallback === true,
      loginNotificationError: notification?.shown === true || notification?.existing === true ? null : String(notification?.reason || 'NOTIFICATION_NOT_SHOWN').slice(0, 500),
      loginNotificationLastAttemptAt: new Date().toISOString()
    });
  }
  const sourceReloaded = await maybeRefreshPreservedSourceAfterAuth(session, tabId);
  if (sourceReloaded) {
    await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`, {when: Date.now() + 1_200});
    return false;
  }
  const candidateTabId = await findReadySessionTab(session, tabId);
  if (!Number.isInteger(candidateTabId)) {
    await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`, {when: Date.now() + LOGIN_WATCH_INTERVAL_MS});
    return false;
  }
  const probe = await probeSessionHealth(session, candidateTabId);
  if (!probe.healthy || probe.loginRequired) {
    await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`, {when: Date.now() + LOGIN_WATCH_INTERVAL_MS});
    return false;
  }
  try {
    await startSession(sessionId, candidateTabId);
    await clearOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`);
    await clearLoginRequiredNotification(sessionId);
    await setSessionGuardBadge(false);
    const latest = await sessionStore.get(sessionId);
    const authTabId = Number.isInteger(latest?.authTabId) ? latest.authTabId : (Number.isInteger(session.authTabId) ? session.authTabId : null);
    if (Number.isInteger(authTabId) && authTabId !== candidateTabId) {
      try { await chrome.tabs.remove(authTabId); } catch {}
    }
    await sessionStore.update(sessionId, {authTabId:null, sourceTabIdAtLogin:null, authSourceReloadAt:null});
    await recordDiagnosticEvent(sessionId, "AUTO_RESUME_AFTER_LOGIN", {result: "success", candidateTabId});
    return true;
  } catch (error) {
    await sessionStore.update(sessionId, {
      queueError: `AUTO_RESUME_FAILED: ${String(error).slice(0, 1000)}`,
      sessionGuardStatus: "LOGIN_RETRY"
    });
    await recordDiagnosticEvent(sessionId, "AUTO_RESUME_AFTER_LOGIN", {
      result: "retry",
      error: sanitizeDiagnosticText(error, 240)
    });
    await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`, {when: Date.now() + LOGIN_WATCH_INTERVAL_MS});
    return false;
  }
}

async function rebindSessionTab(sessionId, tabId, {refreshRuntime = false, activeBatchOnly = false} = {}) {
  if (!Number.isInteger(tabId)) return {bound:0, unmatched:0, scanned:0};
  const documents = await documentStore.bySession(sessionId);
  const now = new Date().toISOString();
  const unfinished = documents
    .filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status))
    .map((document) => ({ ...document, tabId, updatedAt: now }));
  if (unfinished.length) await documentStore.patchRuntimeMany(unfinished.map(({key,tabId}) => ({key,tabId})));
  await sessionStore.update(sessionId, { tabId });
  return refreshRuntime
    ? refreshSessionRuntimeBindings(sessionId, tabId, {activeBatchOnly})
    : {bound:0, unmatched:unfinished.length, scanned:0};
}

async function preflightDocumentAcrossFrames(document, strategy = {}) {
  const frames = await uyapFrames(document.tabId);
  const frameIds = [...new Set([
    document.frameId,
    ...(frames || []).map((frame) => frame.frameId)
  ].filter(Number.isInteger))];
  const errors = [];

  for (const frameId of frameIds) {
    try {
      const response = await sendToFrame(document.tabId, frameId, {
        type: MESSAGE.PREFLIGHT_DOCUMENT,
        documentId: document.runtimeDocumentId || document.documentId,
        kurumLocator: document?.sourceContext?.kurumLocator || null,
        saveMenuTimeoutMs: Math.max(5_000, Number(strategy.saveMenuTimeoutMs || 0))
      });
      if (response?.ok) {
        if (frameId !== document.frameId) await documentStore.update(document.key, {frameId});
        return {ok:true, frameId, method:response.method || null};
      }
      errors.push(response?.error || `Çerçeve ${frameId}: başlangıç testi başarısız`);
    } catch (error) {
      errors.push(String(error));
    }
  }

  throw new Error(errors.at(-1) || 'PREFLIGHT_FRAME_NOT_FOUND: UYAP evrak çerçevesi bulunamadı.');
}

async function alignLargeCaseBatchToPendingWork(sessionId, session = null) {
  const current = session || await sessionStore.get(sessionId);
  if (!current?.largeCaseMode) return current;
  const totalBatches = Math.max(1, Number(current.largeCaseTotalBatches || 1));
  const [queued, retry] = await Promise.all([
    documentStore.firstBySessionStatusBatch(sessionId, STATUS.QUEUED, totalBatches),
    documentStore.firstBySessionStatusBatch(sessionId, STATUS.RETRY_WAIT, totalBatches)
  ]);
  const batchCandidates = [queued, retry]
    .map((document) => Number(document?.largeBatchNo || 0))
    .filter((value) => Number.isInteger(value) && value >= 1);
  const nextBatch = batchCandidates.length ? Math.min(...batchCandidates) : totalBatches;
  const previousBatch = Math.max(1, Number(current.largeCaseCurrentBatch || 1));
  if (nextBatch !== previousBatch) {
    await sessionStore.update(sessionId, {
      largeCaseCurrentBatch: nextBatch,
      largeCaseCompletedBatches: Math.max(Number(current.largeCaseCompletedBatches || 0), nextBatch - 1),
      largeCaseBatchAlignedAt: new Date().toISOString(),
      largeCaseBatchAlignedFrom: previousBatch,
      largeCaseBatchAlignedTo: nextBatch
    });
    await recordDiagnosticEvent(sessionId, "LARGE_CASE_BATCH_ALIGNED", {
      from: previousBatch,
      to: nextBatch,
      totalBatches
    });
    return sessionStore.get(sessionId);
  }
  return current;
}

async function selectStartPreflightDocument(sessionId) {
  const nowIso = new Date().toISOString();
  const session = await sessionStore.get(sessionId);
  const batchNo = session?.largeCaseMode ? Math.max(1, Number(session.largeCaseCurrentBatch || 1)) : null;
  const queued = await documentStore.nextQueued(sessionId, batchNo);
  if (queued) return queued;
  return documentStore.nextReadyRetry(sessionId, nowIso, batchNo);
}

async function runStartPreflight(sessionId, tabId, {autoRecover = false} = {}) {
  let document = await selectStartPreflightDocument(sessionId);
  if (!document) {
    await sessionStore.update(sessionId, {
      startPreflightState: 'not-needed',
      startPreflightError: null,
      startPreflightAt: new Date().toISOString()
    });
    return {ok:true, skipped:true};
  }

  const startedAt = Date.now();
  const strategy = attemptStrategyForDocument(document);
  let firstError = null;
  try {
    const result = await preflightDocumentAcrossFrames(document, strategy);
    await sessionStore.update(sessionId, {
      startPreflightState: 'passed',
      startPreflightDocumentKey: document.key,
      startPreflightRecovered: false,
      startPreflightError: null,
      startPreflightMs: Date.now() - startedAt,
      startPreflightAt: new Date().toISOString(),
      consecutiveStartFailures: 0,
      startPathRecoveryCount: 0,
      startPathNextRetryAt: null
    });
    return result;
  } catch (error) {
    firstError = String(error);
  }

  // One bounded repair is allowed before the queue opens. This is deliberately
  // done before any document attempt is consumed, so a rotated/changed UYAP DOM
  // cannot turn thousands of queued records into start failures.
  const repairSession = await sessionStore.get(sessionId);
  const binding = await refreshSessionRuntimeBindings(sessionId, tabId, { activeBatchOnly: Boolean(repairSession?.largeCaseMode) });
  if (!bindingIsComplete(binding)) {
    const message = `Başlangıç testi sırasında UYAP evrak bağı güvenli yenilenemedi. ${binding.bound}/${binding.bound + binding.unmatched} kayıt eşleşti; ${binding.unmatched} kayıt eşleşmedi.`;
    await sessionStore.update(sessionId, {
      startPreflightState: 'binding-failed',
      startPreflightDocumentKey: document.key,
      startPreflightRecovered: false,
      startPreflightError: firstError,
      startPreflightMs: Date.now() - startedAt,
      startPreflightAt: new Date().toISOString()
    });
    if (autoRecover) {
      await enterStartPathRecovery(sessionId, message, binding);
      return {ok:false, recovering:true, error:message};
    }
    await pauseForUnsafeBinding(sessionId, binding, 'SOURCE_REBIND_REQUIRED');
    throw new Error(`${message} Kuyruk ve doğrulanmış evraklar korundu.`);
  }

  document = await documentStore.get(document.key) || document;
  try {
    const result = await preflightDocumentAcrossFrames(document, {
      ...strategy,
      saveMenuTimeoutMs: Math.max(7_500, Number(strategy.saveMenuTimeoutMs || 0))
    });
    await sessionStore.update(sessionId, {
      startPreflightState: 'passed-after-rebind',
      startPreflightDocumentKey: document.key,
      startPreflightRecovered: true,
      startPreflightFirstError: firstError,
      startPreflightError: null,
      startPreflightMs: Date.now() - startedAt,
      startPreflightAt: new Date().toISOString(),
      consecutiveStartFailures: 0,
      startPathRecoveryCount: 0,
      startPathNextRetryAt: null
    });
    return result;
  } catch (error) {
    const secondError = String(error);
    await clearPendingDownload();
    await sessionStore.update(sessionId, {
      queueError: secondError,
      consecutiveStartFailures: 0,
      startPreflightState: 'failed',
      startPreflightDocumentKey: document.key,
      startPreflightRecovered: true,
      startPreflightFirstError: firstError,
      startPreflightError: secondError,
      startPreflightMs: Date.now() - startedAt,
      startPreflightAt: new Date().toISOString()
    });
    if (autoRecover) {
      await enterStartPathRecovery(sessionId, secondError);
      return {ok:false, recovering:true, error:secondError};
    }
    await clearSessionRuntimeAlarms(sessionId);
    await sessionStore.update(sessionId, {
      status: SESSION_STATUS.PAUSED,
      pauseReason: 'START_PREFLIGHT_FAILED',
      pausedAt: new Date().toISOString()
    });
    throw new Error(`İndirme kuyruğu açılmadı. Tek evraklık başlangıç testi ve otomatik DOM yenileme başarısız oldu. Hiçbir evrak hata sayılmadı; kuyruk korundu. Ayrıntı: ${secondError}`);
  }
}


function downloadHistoryRootFromFilename(filename) {
  const value = String(filename || "").trim();
  if (!value) return null;
  const lower = value.toLocaleLowerCase("tr-TR");
  const markerBackslash = lower.indexOf("\\uyap_arsiv\\");
  const markerSlash = lower.indexOf("/uyap_arsiv/");
  const marker = markerBackslash >= 0 ? markerBackslash : markerSlash;
  if (marker > 1) return value.slice(0, marker);
  const index = Math.max(value.lastIndexOf("\\"), value.lastIndexOf("/"));
  return index > 1 ? value.slice(0, index) : null;
}

async function inferDownloadsRootFromHistory() {
  if (typeof chrome?.downloads?.search !== "function") return null;
  try {
    const rows = await chrome.downloads.search({orderBy:["-startTime"], limit:250});
    const candidates = (rows || []).filter((item) => {
      const url = String(item?.finalUrl || item?.url || "");
      const filename = String(item?.filename || "");
      const basename = filename.split(/[\\/]/).pop() || "";
      return /(^|\.)uyap\.gov\.tr(?=\/|$)/i.test((() => { try { return new URL(url).hostname; } catch { return ""; } })()) ||
        /^evrak[_ -]?\d+/i.test(basename) || /[\\/]UYAP_Arsiv[\\/]/i.test(filename);
    });
    for (const item of candidates) {
      const root = downloadHistoryRootFromFilename(item?.filename);
      if (root) return root;
    }
  } catch { /* history is a best-effort root hint */ }
  return null;
}

function normalizeArchiveRootKey(value) {
  return String(value || "").trim().replaceAll("\\", "/").replace(/\/+$/, "").toLocaleLowerCase("tr-TR");
}

function localArchiveReconciledBatchSet(session, root = null) {
  if (Number(session?.localArchiveReconciliationVersion || 0) !== LOCAL_ARCHIVE_RECONCILIATION_VERSION) return new Set();
  const expectedRoot = normalizeArchiveRootKey(root || session?.downloadsRoot || session?.localArchiveBatchRoot);
  const recordedRoot = normalizeArchiveRootKey(session?.localArchiveBatchRoot || session?.localArchiveScanRoot);
  if (expectedRoot && recordedRoot && expectedRoot !== recordedRoot) return new Set();
  return new Set((Array.isArray(session?.localArchiveReconciledBatches) ? session.localArchiveReconciledBatches : [])
    .map(Number).filter((value) => Number.isInteger(value) && value > 0));
}

function localArchiveBatchIsReconciled(session, batchNo, root = null) {
  if (!session?.largeCaseMode || !Number.isInteger(Number(batchNo)) || Number(batchNo) <= 0) return false;
  return localArchiveReconciledBatchSet(session, root).has(Number(batchNo));
}

function sessionValidationCount(sessionId, batchNo = null) {
  let count = 0;
  for (const entry of validatingDocuments.values()) {
    if (entry?.sessionId !== sessionId) continue;
    if (batchNo !== null && Number(entry?.largeBatchNo || 0) !== Number(batchNo)) continue;
    count += 1;
  }
  return count;
}

function pipelineCanAdvance(session) {
  if (!session || session.status !== SESSION_STATUS.RUNNING) return false;
  if (!["fast", "normal"].includes(String(session?.pacing?.mode || "normal"))) return false;
  if (["LOGIN_REQUIRED", "NATIVE_RECOVERY", "NATIVE_OPERATION_RETRY", "SOURCE_PAGE_NOT_READY"].includes(String(session.sessionGuardStatus || ""))) return false;
  if (Number(session?.pacing?.consecutiveFailures || 0) >= 2) return false;
  if (Number(session?.pacing?.consecutiveHtml || 0) >= HTML_CIRCUIT_CONSECUTIVE_THRESHOLD) return false;
  return true;
}

function reusableLocalArchiveScan(session, root, includeTerminal = false) {
  if (!session || includeTerminal || session.localArchiveScanState !== "complete") return false;
  if (Number(session.localArchiveReconciliationVersion || 0) !== LOCAL_ARCHIVE_RECONCILIATION_VERSION) return false;
  if (Number(session.localArchiveScanTotal || 0) !== Number(session.total || 0)) return false;
  const scanRoot = normalizeArchiveRootKey(session.localArchiveScanRoot);
  const currentRoot = normalizeArchiveRootKey(root || session.downloadsRoot);
  if (!scanRoot || !currentRoot || scanRoot !== currentRoot) return false;
  return Boolean(session.localArchiveScanCompletedAt);
}

const localArchiveSessionTasks = new Map();
function syncSessionWithLocalArchive(sessionId, options = {}) {
  const previous = localArchiveSessionTasks.get(sessionId) || Promise.resolve();
  const task = previous.catch(() => {}).then(() => performLocalArchiveSync(sessionId, options));
  localArchiveSessionTasks.set(sessionId, task);
  const cleanup = () => { if (localArchiveSessionTasks.get(sessionId) === task) localArchiveSessionTasks.delete(sessionId); };
  task.then(cleanup, cleanup);
  return task;
}

async function performLocalArchiveSync(sessionId, {downloadsRoot = null, includeTerminal = false, force = false, largeBatchNo = null, nativePriority = 10} = {}) {
  if (!sessionId) return {matched:0, ambiguous:0, unmatched:0, scanned:0, downloadsRoot:null};
  const session = await sessionStore.get(sessionId);
  if (!session) return {matched:0, ambiguous:0, unmatched:0, scanned:0, downloadsRoot:null};

  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  let root = downloadsRoot || session.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  if (!root) root = await inferDownloadsRootFromHistory();
  const batchNo = Number(largeBatchNo || 0);
  const batchScoped = Boolean(session.largeCaseMode && Number.isInteger(batchNo) && batchNo > 0);
  if (!force && batchScoped && localArchiveBatchIsReconciled(session, batchNo, root)) {
    return {matched:0, recognized:Number(session.localArchiveRecognizedCount || 0), ambiguous:Number(session.localArchiveAmbiguousCount || 0), unmatched:Number(session.localArchiveUnmatchedCount || 0), scanned:0, downloadsRoot:root, reused:true, batchNo};
  }
  if (!force && !batchScoped && reusableLocalArchiveScan(session, root, includeTerminal)) {
    return {
      matched:Number(session.localArchiveRecognizedCount || 0),
      recognized:Number(session.localArchiveRecognizedCount || 0),
      ambiguous:Number(session.localArchiveAmbiguousCount || 0),
      unmatched:Number(session.localArchiveUnmatchedCount || 0),
      scanned:0,
      downloadsRoot:root,
      reused:true
    };
  }

  const documents = await documentStore.bySession(sessionId);
  if (!documents.length) return {matched:0, ambiguous:0, unmatched:0, scanned:0, downloadsRoot:session.downloadsRoot || null};
  const candidates = documents.filter((document) =>
    (!batchScoped || Number(document.largeBatchNo || 0) === batchNo) &&
    (includeTerminal || ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status))
  );
  if (!candidates.length) {
    let rootOrphanCleanup = {scanned:0, removed:0, retained:0, errors:0, removedBytes:0};
    if (!batchScoped) {
      const cleanup = await askNativeAgent({
        action:"sync_inventory",
        __nativePriority:nativePriority,
        sessionId,
        downloadsRoot:root,
        reconcileRootOrphans:true,
        rootOrphanMinimumAgeMs:120_000,
        documents:[]
      });
      if (cleanup?.ok) rootOrphanCleanup = {...rootOrphanCleanup, ...(cleanup.rootOrphanCleanup || {})};
      await sessionStore.update(sessionId, {
        rootOrphanCleanup,
        rootOrphanCleanupAt:rootOrphanCleanup.scanned > 0 ? new Date().toISOString() : session.rootOrphanCleanupAt || null
      });
      if (rootOrphanCleanup.removed > 0 || rootOrphanCleanup.errors > 0) {
        await recordDiagnosticEvent(sessionId, "ROOT_DOWNLOAD_ORPHANS_RECONCILED", rootOrphanCleanup);
      }
    }
    const recognized = documents.filter((document) => document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE").length;
    if (batchScoped) {
      const batches = localArchiveReconciledBatchSet(session, root);
      batches.add(batchNo);
      const totalBatches = Math.max(1, Number(session.largeCaseTotalBatches || 1));
      const complete = batches.size >= totalBatches;
      await sessionStore.update(sessionId, {
        localArchiveReconciliationVersion:LOCAL_ARCHIVE_RECONCILIATION_VERSION,
        localArchiveReconciledBatches:[...batches].sort((a,b)=>a-b),
        localArchiveBatchRoot:root || session.localArchiveBatchRoot || null,
        localArchiveScanState:complete ? "complete" : "partial",
        localArchiveScanCompletedAt:complete ? new Date().toISOString() : session.localArchiveScanCompletedAt || null
      });
    }
    return {matched:0, ambiguous:0, unmatched:0, scanned:0, recognized, downloadsRoot:root, batchNo:batchScoped?batchNo:null, rootOrphanCleanup};
  }

  const byId = new Map(documents.map((document) => [String(document.documentId), document]));
  let matched = 0;
  let ambiguous = 0;
  let unmatched = 0;
  let discoveredRoot = root;
  let scanState = "complete";
  let archiveRootCount = 0;
  let scopeCandidateRows = 0;
  let legacyLooseImported = 0;
  let legacyLooseReused = 0;
  const discoveredRoots = new Set();
  const methodCounts = {};
  const rootOrphanCleanup = {scanned:0, removed:0, retained:0, errors:0, removedBytes:0};
  // RC15.8: fewer native round-trips keep one physical-directory cache alive
  // for thousands of records at a time. Browser->native messages remain well
  // below Chromium's practical payload ceiling for the supported inventory.
  const chunkSize = 3_000;
  for (let offset = 0; offset < candidates.length; offset += chunkSize) {
    const chunk = candidates.slice(offset, offset + chunkSize);
    const synchronization = await askNativeAgent({
      action: "sync_inventory",
      __nativePriority:nativePriority,
      sessionId,
      downloadsRoot: discoveredRoot,
      reconcileRootOrphans: !batchScoped && offset === 0,
      rootOrphanMinimumAgeMs: 120_000,
      documents: chunk.map((document) => ({
        sourceSystem: document.sourceSystem || session.sourceSystem || "UYAP",
        institutionName: document.institutionName || session.institutionName || document.courtName || session.courtName || null,
        courtName: document.courtName || session.courtName || null,
        documentId: document.documentId,
        sourceDocumentId: document.sourceDocumentId || null,
        sourceIdentityHash: document.sourceIdentityHash || null,
        caseId: document.caseId,
        documentTitle: document.documentTitle,
        documentDate: document.documentDate || null,
        documentType: document.documentType || null,
        unitDocumentNumber: document.unitDocumentNumber || null,
        sender: document.sender || null,
        folderNames: document.folderNames,
        sourceVersion: document.sourceVersion || null,
        sourceFingerprint: document.sourceFingerprint || sourceFingerprint(document),
        targetPath: document.targetPath || buildTargetPath(document)
      }))
    });
    if (!synchronization?.ok) { scanState = "failed"; break; }
    invalidateArchiveRegistryCache();
    if (synchronization.downloadsRoot) discoveredRoot = synchronization.downloadsRoot;
    archiveRootCount = Math.max(archiveRootCount, Number(synchronization.archiveRootCount || 0));
    scopeCandidateRows = Math.max(scopeCandidateRows, Number(synchronization.scopeCandidateRows || 0));
    legacyLooseImported += Math.max(0, Number(synchronization.legacyLooseImported || 0));
    legacyLooseReused += Math.max(0, Number(synchronization.legacyLooseReused || 0));
    for (const key of Object.keys(rootOrphanCleanup)) {
      rootOrphanCleanup[key] += Math.max(0, Number(synchronization.rootOrphanCleanup?.[key] || 0));
    }
    for (const candidateRoot of (synchronization.discoveredRoots || [])) discoveredRoots.add(String(candidateRoot));
    const rawUnchangedIds = new Set((synchronization.unchanged || []).map(String));
    const physicalProofs = synchronization.physicalProofs || {};
    const unchangedIds = new Set([...rawUnchangedIds].filter((documentId) => {
      const proof = physicalProofs?.[documentId];
      return proof?.level === "sha256" && proof?.pathExists === true && Boolean(proof?.sha256);
    }));
    const ambiguousIds = new Set((synchronization.ambiguous || []).map(String));
    const checkedAt = new Date().toISOString();
    const chunkUpdates = [];
    for (const document of chunk) {
      const documentId = String(document.documentId);
      if (unchangedIds.has(documentId)) {
        matched += 1;
        const method = synchronization.matchMethods?.[documentId] || "verified-local-archive";
        methodCounts[method] = Number(methodCounts[method] || 0) + 1;
        chunkUpdates.push({
          ...document,
          status: STATUS.SKIPPED,
          skipReason: "UNCHANGED_LOCAL_ARCHIVE",
          sha256: synchronization.hashes?.[documentId] || document.sha256 || null,
          localArchiveRecoveredAt: checkedAt,
          localArchiveCheckedAt: checkedAt,
          localArchiveCheckedRoot: discoveredRoot || root || null,
          localArchiveMatchState: "found",
          localArchiveMatchMethod: method,
          localArchivePhysicalProofLevel: "sha256",
          localArchivePhysicalProofAt: checkedAt,
          localArchivePhysicalProofSize: Number(physicalProofs?.[documentId]?.size || 0),
          localArchivePhysicalProofSha256: physicalProofs?.[documentId]?.sha256 || synchronization.hashes?.[documentId] || null,
          errorCode: null,
          errorMessage: null,
          warnings: withoutRecoveredNativeWarnings(document.warnings),
          retryAt: null,
          retryClass: null,
          manualRetryPriority: false,
          updatedAt: checkedAt
        });
      } else if (ambiguousIds.has(documentId)) {
        ambiguous += 1;
        chunkUpdates.push({
          ...document,
          status: ((document.status === STATUS.REVIEW_REQUIRED && document.errorCode === "LOCAL_ARCHIVE_AMBIGUOUS") ||
                   (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE")) ? STATUS.QUEUED : document.status,
          skipReason: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? null : document.skipReason,
          localArchiveMatchState: "ambiguous_hash_pending",
          localArchiveMatchMethod: "content-sha256-pending",
          localArchivePhysicalProofLevel: null,
          localArchivePhysicalProofAt: null,
          localArchivePhysicalProofSize: null,
          localArchivePhysicalProofSha256: null,
          localHashReconcilePending: true,
          errorCode: document.errorCode === "LOCAL_ARCHIVE_AMBIGUOUS" ? null : document.errorCode,
          errorMessage: document.errorCode === "LOCAL_ARCHIVE_AMBIGUOUS" ? null : document.errorMessage,
          retryClass: document.errorCode === "LOCAL_ARCHIVE_AMBIGUOUS" ? null : document.retryClass,
          localArchiveCheckedAt: checkedAt,
          localArchiveCheckedRoot: discoveredRoot || root || null,
          updatedAt: checkedAt
        });
      } else {
        unmatched += 1;
        chunkUpdates.push({
          ...document,
          status: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? STATUS.QUEUED : document.status,
          skipReason: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? null : document.skipReason,
          localArchiveMatchState: rawUnchangedIds.has(documentId) ? "unproven_metadata" : "not_found",
          localArchiveMatchMethod: null,
          localArchivePhysicalProofLevel: null,
          localArchivePhysicalProofAt: null,
          localArchivePhysicalProofSize: null,
          localArchivePhysicalProofSha256: null,
          localArchiveCheckedAt: checkedAt,
          localArchiveCheckedRoot: discoveredRoot || root || null,
          updatedAt: checkedAt
        });
      }
    }
    if (chunkUpdates.length) await documentStore.putMany(chunkUpdates);
  }
  if (discoveredRoot) {
    await chrome.storage.local.set({[LAST_DOWNLOADS_ROOT_KEY]: discoveredRoot});
    if (session.downloadsRoot !== discoveredRoot) {
      await sessionStore.update(sessionId, {downloadsRoot: discoveredRoot});
    }
  }

  const refreshed = await documentStore.bySession(sessionId);
  const recognized = refreshed.filter((document) => document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE").length;
  const currentAmbiguous = refreshed.filter((document) => ["ambiguous", "ambiguous_hash_pending"].includes(document.localArchiveMatchState) && ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status)).length;
  const currentUnmatched = refreshed.filter((document) => document.localArchiveMatchState === "not_found" && ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status)).length;
  const effectiveRoot = discoveredRoot || root || null;
  const latestSession = await sessionStore.get(sessionId) || session;
  let reconciledBatches = localArchiveReconciledBatchSet(latestSession, effectiveRoot);
  if (batchScoped && scanState !== "failed") reconciledBatches.add(batchNo);
  const totalBatches = Math.max(1, Number(session.largeCaseTotalBatches || 1));
  const allBatchesReconciled = batchScoped && reconciledBatches.size >= totalBatches;
  const effectiveScanState = scanState === "failed" ? "failed" : (batchScoped ? (allBatchesReconciled ? "complete" : "partial") : "complete");
  const checkedCount = batchScoped
    ? refreshed.filter((document) => normalizeArchiveRootKey(document.localArchiveCheckedRoot) === normalizeArchiveRootKey(effectiveRoot)).length
    : documents.length;
  await sessionStore.update(sessionId, {
    localArchiveReconciliationVersion: LOCAL_ARCHIVE_RECONCILIATION_VERSION,
    localArchiveScanState: effectiveScanState,
    localArchiveScanCompletedAt: effectiveScanState === "complete" ? new Date().toISOString() : session.localArchiveScanCompletedAt || null,
    localArchiveBatchScanAt: batchScoped ? new Date().toISOString() : session.localArchiveBatchScanAt || null,
    localArchiveScanTotal: checkedCount,
    localArchiveScanRoot: effectiveRoot,
    localArchiveBatchRoot: batchScoped ? effectiveRoot : (session.localArchiveBatchRoot || effectiveRoot),
    localArchiveReconciledBatches: batchScoped ? [...reconciledBatches].sort((a,b)=>a-b) : (session.localArchiveReconciledBatches || []),
    localArchiveRecognizedCount: recognized,
    localArchiveAmbiguousCount: currentAmbiguous,
    localArchiveUnmatchedCount: currentUnmatched,
    localArchiveMatchMethods: methodCounts,
    localArchiveArchiveRootCount: archiveRootCount,
    localArchiveScopeCandidateRows: scopeCandidateRows,
    localArchiveLegacyLooseImported: legacyLooseImported,
    localArchiveLegacyLooseReused: legacyLooseReused,
    localArchiveDiscoveredRoots: [...discoveredRoots],
    rootOrphanCleanup,
    rootOrphanCleanupAt: rootOrphanCleanup.scanned > 0 ? new Date().toISOString() : session.rootOrphanCleanupAt || null,
    localArchiveReconciledAt: matched > 0 ? new Date().toISOString() : session.localArchiveReconciledAt || null,
    localArchiveRecoveredCount: recognized
  });
  await recordDiagnosticEvent(sessionId, matched > 0 ? "LOCAL_ARCHIVE_RECONCILED" : "LOCAL_ARCHIVE_SCANNED", {
    matched, recognized, ambiguous:currentAmbiguous, unmatched:currentUnmatched, scanned:candidates.length, methods:methodCounts,
    archiveRootCount, scopeCandidateRows, discoveredRoots:[...discoveredRoots], batchNo:batchScoped?batchNo:null,
    scanState:effectiveScanState, rootOrphanCleanup, legacyLooseImported, legacyLooseReused
  });
  if (rootOrphanCleanup.removed > 0 || rootOrphanCleanup.errors > 0) {
    await recordDiagnosticEvent(sessionId, "ROOT_DOWNLOAD_ORPHANS_RECONCILED", rootOrphanCleanup);
  }
  return {matched, recognized, ambiguous:currentAmbiguous, unmatched:currentUnmatched, scanned:candidates.length, downloadsRoot:effectiveRoot, methods:methodCounts, archiveRootCount, scopeCandidateRows, legacyLooseImported, legacyLooseReused, discoveredRoots:[...discoveredRoots], batchNo:batchScoped?batchNo:null, scanState:effectiveScanState, rootOrphanCleanup};
}


async function ensureLocalArchiveBatchReconciled(sessionId, batchNo, {downloadsRoot = null, force = false, nativePriority = 0} = {}) {
  const safeBatch = Number(batchNo || 0);
  if (!sessionId || !Number.isInteger(safeBatch) || safeBatch <= 0) return null;
  const session = await sessionStore.get(sessionId);
  if (!session?.largeCaseMode) return null;
  const root = downloadsRoot || session.downloadsRoot || null;
  if (!force && localArchiveBatchIsReconciled(session, safeBatch, root)) return {ok:true,reused:true,batchNo:safeBatch};
  const key = `${sessionId}:${safeBatch}:${normalizeArchiveRootKey(root) || "auto"}`;
  if (localArchiveBatchTasks.has(key)) return localArchiveBatchTasks.get(key);
  const task = syncSessionWithLocalArchive(sessionId, {downloadsRoot:root, largeBatchNo:safeBatch, force, nativePriority})
    .finally(() => localArchiveBatchTasks.delete(key));
  localArchiveBatchTasks.set(key, task);
  return task;
}

async function prefetchNextLocalArchiveBatch(sessionId, activeBatch) {
  const session = await sessionStore.get(sessionId);
  if (!session?.largeCaseMode || session.status !== SESSION_STATUS.RUNNING) return null;
  const nextBatch = Number(activeBatch || 0) + 1;
  if (nextBatch > Number(session.largeCaseTotalBatches || 0)) return null;
  if (localArchiveBatchIsReconciled(session, nextBatch, session.downloadsRoot)) return null;
  const key = `${sessionId}:${nextBatch}:${normalizeArchiveRootKey(session.downloadsRoot) || "auto"}`;
  if (localArchivePrefetchTasks.has(key)) return localArchivePrefetchTasks.get(key);
  const task = (async () => {
    try {
      const result = await ensureLocalArchiveBatchReconciled(sessionId, nextBatch, {downloadsRoot:session.downloadsRoot, nativePriority:50});
      await recordDiagnosticEvent(sessionId, "AUTO_PIPELINE_BATCH_PREFETCH", {
        batchNo:nextBatch,
        scanned:Number(result?.scanned || 0),
        matched:Number(result?.matched || 0),
        reused:result?.reused === true
      }).catch(()=>{});
      return result;
    } catch (error) {
      await recordDiagnosticEvent(sessionId, "AUTO_PIPELINE_BATCH_PREFETCH_FAILED", {
        batchNo:nextBatch,
        error:sanitizeDiagnosticText(error, 240)
      }).catch(()=>{});
      return null;
    }
  })().finally(() => localArchivePrefetchTasks.delete(key));
  localArchivePrefetchTasks.set(key, task);
  return task;
}


async function continueLocalWorkDuringLoginPause(sessionId) {
  try {
    const session = await sessionStore.get(sessionId);
    if (!session || session.status !== SESSION_STATUS.PAUSED || session.pauseReason !== "LOGIN_REQUIRED") return null;
    const batches = [];
    if (session.largeCaseMode) {
      const current = Math.max(1, Number(session.largeCaseCurrentBatch || 1));
      const total = Math.max(1, Number(session.largeCaseTotalBatches || 1));
      batches.push(current);
      if (current + 1 <= total) batches.push(current + 1);
    }
    let reconciled = 0;
    for (const batchNo of batches) {
      const fresh = await sessionStore.get(sessionId);
      if (!fresh || fresh.status !== SESSION_STATUS.PAUSED || fresh.pauseReason !== "LOGIN_REQUIRED") break;
      if (localArchiveBatchIsReconciled(fresh, batchNo, fresh.downloadsRoot)) continue;
      await ensureLocalArchiveBatchReconciled(sessionId, batchNo, {downloadsRoot:fresh.downloadsRoot, nativePriority:80});
      reconciled += 1;
    }
    await sessionStore.update(sessionId, {
      localWorkDuringLoginAt:new Date().toISOString(),
      localWorkDuringLoginBatches:reconciled
    });
    await recordDiagnosticEvent(sessionId, "LOGIN_PAUSE_LOCAL_WORK", {reconciledBatches:reconciled}).catch(()=>{});
    return {reconciledBatches:reconciled};
  } catch (error) {
    await recordDiagnosticEvent(sessionId, "LOGIN_PAUSE_LOCAL_WORK_FAILED", {error:sanitizeDiagnosticText(error,240)}).catch(()=>{});
    return null;
  }
}

async function reconcileAfterDownloadsRootDiscovery(sessionId, downloadsRoot) {
  if (!sessionId || !downloadsRoot) return null;
  const session = await sessionStore.get(sessionId);
  if (!session) return null;
  if (String(session.postRootReconcileRoot || "") === String(downloadsRoot)) return null;
  const shouldReconcile = !session.downloadsRoot ||
    String(session.downloadsRoot) !== String(downloadsRoot) ||
    Number(session.localArchiveRecognizedCount || 0) === 0;
  if (!shouldReconcile) return null;
  const startedAt = Date.now();
  await sessionStore.update(sessionId, {
    downloadsRoot,
    postRootReconcileRoot: downloadsRoot,
    postRootReconcileStartedAt: new Date().toISOString()
  });
  await chrome.storage.local.set({[LAST_DOWNLOADS_ROOT_KEY]: downloadsRoot});
  try {
    const activeBatch = session.largeCaseMode ? Math.max(1, Number(session.largeCaseCurrentBatch || 1)) : null;
    const result = await syncSessionWithLocalArchive(sessionId, {downloadsRoot, force:true, largeBatchNo:activeBatch, nativePriority:0});
    await sessionStore.update(sessionId, {
      postRootReconcileCompletedAt: new Date().toISOString(),
      postRootReconcileMatched: Number(result?.matched || 0),
      postRootReconcileMs: Date.now() - startedAt
    });
    await recordDiagnosticEvent(sessionId, "LOCAL_ARCHIVE_POST_ROOT_RECONCILED", {
      matched:Number(result?.matched || 0),
      recognized:Number(result?.recognized || 0),
      ambiguous:Number(result?.ambiguous || 0),
      unmatched:Number(result?.unmatched || 0),
      scanned:Number(result?.scanned || 0),
      elapsedMs:Date.now() - startedAt
    });
    return result;
  } catch (error) {
    await sessionStore.update(sessionId, {
      postRootReconcileRoot: null,
      postRootReconcileError: sanitizeDiagnosticText(error, 240),
      postRootReconcileFailedAt: new Date().toISOString()
    });
    await recordDiagnosticEvent(sessionId, "LOCAL_ARCHIVE_POST_ROOT_RECONCILE_FAILED", {error:sanitizeDiagnosticText(error,240)}).catch(()=>{});
    return null;
  }
}

async function guardDocumentAgainstLocalArchive(document, session) {
  if (!document?.documentId || !session?.sessionId) return {decision:"download", reason:"missing-identity"};

  // RC15.9.20: finalization may discover that a document which was previously
  // satisfied by a weak local-archive alias must be materialized at its own
  // canonical target path. In that one bounded recovery case the normal local
  // duplicate gate would otherwise rediscover the same alias and create an
  // infinite finalize -> retry -> local-skip loop. Bypass the local gate only
  // for this explicitly tagged final-recovery document; normal duplicate
  // protection remains unchanged for every other queue item.
  if (document.forceCanonicalRecovery === true) {
    await recordDiagnosticEvent(session.sessionId, "FINAL_RECOVERY_LOCAL_GATE_BYPASSED", {
      documentId: String(document.documentId),
      previousMatchMethod: document.forceCanonicalRecoveryPreviousMatchMethod || document.localArchiveMatchMethod || null,
      targetPath: document.targetPath || null
    });
    return {decision:"download", reason:"final-recovery-canonical-materialization"};
  }

  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  const downloadsRoot = session.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;

  // RC15.8 hot path: the full session reconciliation has already proved this
  // exact document absent from this exact archive root. Re-running sync_inventory
  // before every click re-lists the same large case directory and can cost many
  // seconds per document. Reuse that negative proof inside the same session.
  // Final native validation/finalization remains cryptographic and fail-closed.
  const scanRootMatches = normalizeArchiveRootKey(session.localArchiveScanRoot) &&
    normalizeArchiveRootKey(session.localArchiveScanRoot) === normalizeArchiveRootKey(downloadsRoot);
  const documentRootMatches = normalizeArchiveRootKey(document.localArchiveCheckedRoot) &&
    normalizeArchiveRootKey(document.localArchiveCheckedRoot) === normalizeArchiveRootKey(downloadsRoot);
  const completeSessionScan = session.localArchiveScanState === "complete" &&
    Number(session.localArchiveScanTotal || 0) === Number(session.total || 0);
  const batchProof = session.largeCaseMode && localArchiveBatchIsReconciled(session, Number(document.largeBatchNo || 0), downloadsRoot);
  if ((completeSessionScan || batchProof) && scanRootMatches && documentRootMatches &&
      document.localArchiveMatchState === "not_found" && document.localArchiveCheckedAt) {
    return {decision:"download", reason:batchProof?"batch-local-scan-cache":"session-local-scan-cache"};
  }
  if ((completeSessionScan || batchProof) && scanRootMatches && documentRootMatches &&
      document.localArchiveMatchState === "ambiguous_hash_pending" && document.localArchiveCheckedAt) {
    return {decision:"download", reason:"content-hash-reconcile-pending"};
  }

  const result = await askNativeAgent({
    action: "sync_inventory",
    sessionId: session.sessionId,
    downloadsRoot,
    documents: [{
      sourceSystem: document.sourceSystem || session.sourceSystem || "UYAP",
      institutionName: document.institutionName || session.institutionName || document.courtName || session.courtName || null,
      courtName: document.courtName || session.courtName || null,
      documentId: document.documentId,
      sourceDocumentId: document.sourceDocumentId || null,
      sourceIdentityHash: document.sourceIdentityHash || null,
      caseId: document.caseId,
      documentTitle: document.documentTitle,
      documentDate: document.documentDate || null,
      documentType: document.documentType || null,
      unitDocumentNumber: document.unitDocumentNumber || null,
      sender: document.sender || null,
      folderNames: document.folderNames,
      sourceVersion: document.sourceVersion || null,
      sourceFingerprint: document.sourceFingerprint || sourceFingerprint(document),
      targetPath: document.targetPath || buildTargetPath(document)
    }]
  });
  if (!result?.ok) throw new Error(`LOCAL_ARCHIVE_PREFLIGHT_FAILED: ${result?.error || "Yerel arşiv kontrolü başarısız."}`);

  const discoveredRoot = result.downloadsRoot || downloadsRoot || null;
  if (discoveredRoot) {
    await chrome.storage.local.set({[LAST_DOWNLOADS_ROOT_KEY]: discoveredRoot});
    if (session.downloadsRoot !== discoveredRoot) await sessionStore.update(session.sessionId, {downloadsRoot: discoveredRoot});
  }

  const id = String(document.documentId);
  const metadataFound = new Set((result.unchanged || []).map(String)).has(id);
  const physicalProof = result.physicalProofs?.[id] || null;
  const found = metadataFound && physicalProof?.level === "sha256" && physicalProof?.pathExists === true && Boolean(physicalProof?.sha256);
  const ambiguous = new Set((result.ambiguous || []).map(String)).has(id);
  const checkedAt = new Date().toISOString();
  if (found) {
    const method = result.matchMethods?.[id] || "verified-local-archive";
    await documentStore.update(document.key, {
      status: STATUS.SKIPPED,
      skipReason: "UNCHANGED_LOCAL_ARCHIVE",
      sha256: result.hashes?.[id] || document.sha256 || null,
      localArchiveRecoveredAt: checkedAt,
      localArchiveCheckedAt: checkedAt,
      localArchiveCheckedRoot: discoveredRoot || downloadsRoot || null,
      localArchiveMatchState: "found",
      localArchiveMatchMethod: method,
      localArchivePhysicalProofLevel: "sha256",
      localArchivePhysicalProofAt: checkedAt,
      localArchivePhysicalProofSize: Number(physicalProof?.size || 0),
      localArchivePhysicalProofSha256: physicalProof?.sha256 || result.hashes?.[id] || null,
      errorCode: null,
      errorMessage: null,
      warnings: withoutRecoveredNativeWarnings(document.warnings),
      manualRetryPriority: false,
      retryAt: null,
      retryClass: null
    });
    const wasState = String(document.localArchiveMatchState || "");
    await sessionStore.update(session.sessionId, {
      localArchiveRecognizedCount: Math.max(0, Number(session.localArchiveRecognizedCount || 0) + (wasState === "found" ? 0 : 1)),
      localArchiveAmbiguousCount: Math.max(0, Number(session.localArchiveAmbiguousCount || 0) - (["ambiguous", "ambiguous_hash_pending"].includes(wasState) ? 1 : 0)),
      localArchiveUnmatchedCount: Math.max(0, Number(session.localArchiveUnmatchedCount || 0) - (wasState === "not_found" ? 1 : 0)),
      localArchiveReconciledAt: checkedAt
    });
    await recordDiagnosticEvent(session.sessionId, "DOWNLOAD_BLOCKED_LOCAL_DUPLICATE", {
      documentId: id, method, targetPath: document.targetPath || null
    });
    const latestAfterLocalProof = await sessionStore.get(session.sessionId);
    if (latestAfterLocalProof?.nativeTransientFailureActive === true || latestAfterLocalProof?.pacing?.recentOutcomes?.some?.((outcome) => outcome?.errorCode === TRANSIENT_NATIVE_ERROR_CODE)) {
      await cleanupResolvedNativeTransientState(session.sessionId, {reason:"native-local-inventory-success", forceDocuments:true});
    }
    return {decision:"skip", reason:"already-local", method};
  }
  if (ambiguous) {
    // RC15.9.10: metadata ambiguity is no longer a terminal/manual-review state.
    // Download exactly once, validate, then let SHA-256 decide whether the bytes
    // already exist locally. Distinct UYAP identities remain distinct paths.
    await documentStore.update(document.key, {
      status: STATUS.QUEUED,
      errorCode: null,
      errorMessage: null,
      localArchiveCheckedAt: checkedAt,
      localArchiveCheckedRoot: discoveredRoot || downloadsRoot || null,
      localArchiveMatchState: "ambiguous_hash_pending",
      localArchiveMatchMethod: "content-sha256-pending",
      localArchivePhysicalProofLevel: null,
      localArchivePhysicalProofAt: null,
      localArchivePhysicalProofSize: null,
      localArchivePhysicalProofSha256: null,
      localHashReconcilePending: true,
      manualRetryPriority: Boolean(document.manualRetryPriority),
      retryAt: null,
      retryClass: null
    });
    const wasState = String(document.localArchiveMatchState || "");
    await sessionStore.update(session.sessionId, {
      localArchiveRecognizedCount: Math.max(0, Number(session.localArchiveRecognizedCount || 0) - (wasState === "found" ? 1 : 0)),
      localArchiveAmbiguousCount: Math.max(0, Number(session.localArchiveAmbiguousCount || 0) + (["ambiguous", "ambiguous_hash_pending"].includes(wasState) ? 0 : 1)),
      localArchiveUnmatchedCount: Math.max(0, Number(session.localArchiveUnmatchedCount || 0) - (wasState === "not_found" ? 1 : 0)),
      localArchiveReconciledAt: checkedAt
    });
    await recordDiagnosticEvent(session.sessionId, "LOCAL_ARCHIVE_AMBIGUOUS_HASH_PENDING", {
      documentId: id, targetPath: document.targetPath || null
    });
    return {decision:"download", reason:"ambiguous-content-hash-pending"};
  }

  await documentStore.update(document.key, {
    status: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? STATUS.QUEUED : document.status,
    skipReason: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? null : document.skipReason,
    localArchiveCheckedAt: checkedAt,
    localArchiveCheckedRoot: discoveredRoot || downloadsRoot || null,
    localArchiveMatchState: metadataFound ? "unproven_metadata" : "not_found",
    localArchiveMatchMethod: null,
    localArchivePhysicalProofLevel: null,
    localArchivePhysicalProofAt: null,
    localArchivePhysicalProofSize: null,
    localArchivePhysicalProofSha256: null
  });
  if (metadataFound && !found) {
    await recordDiagnosticEvent(session.sessionId, "LOCAL_ARCHIVE_UNPROVEN_METADATA_REJECTED", {
      documentId:id, targetPath:document.targetPath || null, reason:"sha256-physical-proof-missing"
    });
  }
  return {decision:"download", reason:metadataFound?"unproven-local-metadata":"not-found"};
}

export async function startSession(sessionId, tabId = null) {
  const oldSession = await sessionStore.get(sessionId);
  if (!oldSession) throw new Error('İş bulunamadı. Önce sayfayı tarayın.');
  if (oldSession.identitySchemaVersion !== 3) throw new Error('Bu kuyruk eski kimlik şemasıyla oluşturulmuş. Raporu saklayıp Sayfayı tara ile yeni envanter oluşturun; eski arşivler korunur.');
  if ([SESSION_STATUS.RUNNING,SESSION_STATUS.RECOVERING].includes(oldSession.status)) return getSessionSummary(sessionId);
  await migrateReliabilityState(sessionId);
  await quarantineLegacyDeterministicRetries(sessionId);
  await repairUnassignedBatches(sessionId);
  const agent = await pingNativeAgent();
  if (!agent.ok) {
    throw new Error(`İndirme başlamadı: yerel ajan bağlantısı kurulamadı. Paket içindeki YEREL_AJANI_KUR.bat dosyasını çalıştırın. Ayrıntı: ${agent.error}`);
  }
  if (agent.downloadReady !== true) {
    throw new Error(`İndirme başlamadı: bağlı Native Agent ${agent.version || "bilinmiyor"} bu sürümün güvenli arşiv onarımını desteklemiyor. Agent 2.7.64 veya üzerini yükleyin; mevcut arşiv ve kuyruk korunuyor.`);
  }
  // RC15.3 can reclaim a canonical evrak_<id> file that an older build left
  // in the browser download root after a correlation race. This runs before
  // new clicks, so a valid existing byte stream wins over re-downloading.
  await recoverStrandedCanonicalDownloadsAtStart(sessionId);
  const now = new Date().toISOString();
  const wasLoginResume = oldSession.status === SESSION_STATUS.PAUSED && oldSession.pauseReason === "LOGIN_REQUIRED";
  const wasEndpointRefreshResume = oldSession.status === SESSION_STATUS.PAUSED && oldSession.pauseReason === "CONTENT_ENDPOINT_REFRESH_REQUIRED";
  if (!wasEndpointRefreshResume && ["open","half_open"].includes(htmlCircuitState(oldSession)) && Number(oldSession.htmlCircuitProbeAttempt || 0) >= HTML_CIRCUIT_MAX_SOFT_PROBES) {
    await clearSessionRuntimeAlarms(sessionId);
    await sessionStore.update(sessionId,{status:SESSION_STATUS.PAUSED,pauseReason:"CONTENT_ENDPOINT_REFRESH_REQUIRED",sessionGuardStatus:"CONTENT_ENDPOINT_REFRESH_REQUIRED"});
    return getSessionSummary(sessionId);
  }
  const needsRuntimeRefresh = oldSession.status === SESSION_STATUS.PAUSED;
  const effectiveTabId = Number.isInteger(tabId) ? tabId : oldSession.tabId;
  if (!Number.isInteger(effectiveTabId)) {
    throw new Error('İndirme başlamadı: açık UYAP sekmesi bulunamadı. Dava dosyasını ve Evrak sekmesini açık tutup yeniden deneyin.');
  }

  // A fresh inventory scan has already proved that the case tree exists. Do not
  // gate a new start on a second lightweight PROBE_PAGE result: on live Kurum
  // builds that probe can transiently report not-ready while the exact same DOM
  // is fully scannable, which used to send a valid 78/78 queue into an endless
  // START_PATH_DEGRADED loop before preflight even ran. Re-scan and bind the
  // persisted queue synchronously instead. This is stronger evidence because it
  // validates every unfinished record against the active case branch.
  const freshLargeCaseStart = Boolean(
    oldSession.largeCaseMode &&
    oldSession.status === SESSION_STATUS.READY &&
    Number(oldSession.tabId) === Number(effectiveTabId) &&
    (Date.now() - (Date.parse(oldSession.createdAt || 0) || 0)) <= 10 * 60_000
  );
  let binding;
  if (freshLargeCaseStart) {
    // createSession was produced from the currently open UYAP tree only moments ago.
    // Re-scanning and rewriting all 10k+ runtime bindings here doubles startup
    // cost without adding evidence. Preflight below still validates the first
    // active batch item fail-closed. Resumes/reloads keep the full safe rebind.
    const currentBatch = Math.max(1, Number(oldSession.largeCaseCurrentBatch || 1));
    const batchQueued = await documentStore.countBySessionBatchStatus(sessionId, currentBatch, STATUS.QUEUED);
    binding = {bound: batchQueued, unmatched:0, scanned:Number(oldSession.total || 0), contextMismatch:false, largeCaseFastStart:true};
    await sessionStore.update(sessionId, {tabId:effectiveTabId});
  } else {
    binding = await rebindSessionTab(sessionId, effectiveTabId, {refreshRuntime: true, activeBatchOnly: Boolean(oldSession.largeCaseMode)});
  }
  if (binding.contextMismatch) {
    await pauseForSourceContextChanged(sessionId, binding);
    return getSessionSummary(sessionId);
  }

  // One short in-process retry absorbs an AJAX/tree repaint without delegating
  // a sub-second retry to chrome.alarms (MV3 persistent alarms are >=30 s).
  if (!bindingIsComplete(binding)) {
    await new Promise((resolve) => setTimeout(resolve, 180));
    binding = await refreshSessionRuntimeBindings(sessionId, effectiveTabId, {activeBatchOnly: Boolean(oldSession.largeCaseMode)});
    if (binding.contextMismatch) {
      await pauseForSourceContextChanged(sessionId, binding);
      return getSessionSummary(sessionId);
    }
  }

  if (!bindingIsComplete(binding)) {
    let message = `Başlangıçta aktif dava ağacıyla ${binding.bound}/${binding.bound + binding.unmatched} kayıt güvenli eşleşti; ${binding.unmatched} kayıt eşleşmedi (tarandı: ${binding.scanned}).`;
    let localDocuments = await documentStore.bySession(sessionId);
    let metadataAlreadyArchived = localDocuments.length > 0 && localDocuments.every((document) =>
      document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE"
    );
    let allAlreadyArchived = metadataAlreadyArchived && localDocuments.every((document) =>
      document.localArchivePhysicalProofLevel === "sha256" &&
      Boolean(document.localArchivePhysicalProofAt) &&
      Boolean(document.localArchivePhysicalProofSha256)
    );

    // RC15.9.22: an ALREADY_ARCHIVED decision must be based on bytes that exist
    // on disk at decision time, not on a proof cached seconds/minutes earlier.
    // This also upgrades older metadata-only UNCHANGED_LOCAL_ARCHIVE states.
    // A missing/replaced file is converted back to QUEUED and rebound to UYAP.
    if (metadataAlreadyArchived) {
      const proofRefresh = await syncSessionWithLocalArchive(sessionId, {
        downloadsRoot: oldSession.downloadsRoot || null, includeTerminal: true, force: true, nativePriority: 0
      });
      localDocuments = await documentStore.bySession(sessionId);
      metadataAlreadyArchived = localDocuments.length > 0 && localDocuments.every((document) =>
        document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE"
      );
      allAlreadyArchived = metadataAlreadyArchived && localDocuments.every((document) =>
        document.localArchivePhysicalProofLevel === "sha256" &&
        Boolean(document.localArchivePhysicalProofAt) &&
        Boolean(document.localArchivePhysicalProofSha256)
      );
      if (!allAlreadyArchived) {
        await recordDiagnosticEvent(sessionId, 'ALREADY_ARCHIVED_PHYSICAL_PROOF_REJECTED', {
          total: localDocuments.length,
          unproven: localDocuments.filter((document) => document.localArchivePhysicalProofLevel !== "sha256" || !document.localArchivePhysicalProofSha256).length,
          matchedAfterReproof: Number(proofRefresh?.matched || 0),
          reason: 'Metadata-only local archive state cannot suppress UYAP downloads.'
        });
        binding = await refreshSessionRuntimeBindings(sessionId, effectiveTabId, {activeBatchOnly: Boolean(oldSession.largeCaseMode)});
        if (binding.contextMismatch) {
          await pauseForSourceContextChanged(sessionId, binding);
          return getSessionSummary(sessionId);
        }
        message = `Fiziksel arşiv kanıtı yenilendi; aktif dava ağacıyla ${binding.bound}/${binding.bound + binding.unmatched} kayıt güvenli eşleşti; ${binding.unmatched} kayıt eşleşmedi (tarandı: ${binding.scanned}).`;
      }
    }

    if (!bindingIsComplete(binding)) {
      if (allAlreadyArchived) {
        await clearSessionRuntimeAlarms(sessionId);
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.PAUSED,
          pauseReason: 'ALREADY_ARCHIVED',
          pausedAt: new Date().toISOString(),
          queueError: null,
          sessionGuardStatus: 'ALREADY_ARCHIVED',
          observedCaseId: binding.observedCaseId || null,
          runtimeBound: 0,
          runtimeUnmatched: 0,
          runtimeScanned: 0
        });
        await recordDiagnosticEvent(sessionId, 'ALREADY_ARCHIVED', {
          total: localDocuments.length,
          proofLevel: 'sha256',
          reason: 'All documents have current on-disk SHA-256 physical proofs; runtime binding was intentionally skipped.'
        });
        return getSessionSummary(sessionId);
      }
      if (String(oldSession.sourceSystem || '').toUpperCase() === 'UYAP_KURUM' && oldSession.status === SESSION_STATUS.READY) {
        await pauseForUnsafeBinding(sessionId, binding, 'START_BINDING_FAILED');
        await sessionStore.update(sessionId, {
          queueError: message,
          sessionGuardStatus: 'START_BINDING_FAILED',
          observedCaseId: binding.observedCaseId || null
        });
        await recordDiagnosticEvent(sessionId, 'START_BINDING_FAILED', {
          bound: binding.bound,
          unmatched: binding.unmatched,
          scanned: binding.scanned,
          error: sanitizeDiagnosticText(message, 240)
        });
        return getSessionSummary(sessionId);
      }
      await enterStartPathRecovery(sessionId, message, binding);
      return getSessionSummary(sessionId);
    }
  }

  await sessionStore.update(sessionId, {
    tabId: effectiveTabId,
    runtimeBound: binding.bound,
    runtimeUnmatched: binding.unmatched,
    runtimeScanned: binding.scanned,
    observedCaseId: null,
    observedCourtName: null,
    queueError: null,
    sessionGuardStatus: oldSession.status === SESSION_STATUS.PAUSED ? 'RECOVERED_SOURCE' : 'READY'
  });
  if (needsRuntimeRefresh) await repairMassDownloadStartFailures(sessionId);
  // Reconcile the current queue against the physical local archive before any
  // new download starts. Kurum UYAP does not expose sourceVersion, so every
  // non-terminal document with a stable documentId/sourceFingerprint must be
  // offered to the native archive registry.
  const preSyncSession = await sessionStore.get(sessionId);
  const syncBatchNo = preSyncSession?.largeCaseMode ? Math.max(1, Number(preSyncSession.largeCaseCurrentBatch || 1)) : null;
  const synchronization = await syncSessionWithLocalArchive(sessionId, {largeBatchNo:syncBatchNo, nativePriority:0});
  const syncUnchangedCount = Number(synchronization.matched || 0);
  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);

  await compactOperationalAlarms();
  let preflightSession = await sessionStore.get(sessionId);
  if (preflightSession?.largeCaseMode) {
    preflightSession = await alignLargeCaseBatchToPendingWork(sessionId, preflightSession);
  }
  const isKurumStart = String(preflightSession?.sourceSystem || oldSession.sourceSystem || '').toUpperCase() === 'UYAP_KURUM';
  // Kurum classic-tree preflight is deterministic (runtime node + evrak_id +
  // active branch). If its bounded repair also fails, surface the exact error
  // instead of entering a vague alarm-driven recovery loop. Normal UYAP keeps
  // its existing automatic-recovery policy.
  const preflight = await runStartPreflight(
    sessionId,
    preflightSession?.tabId ?? effectiveTabId,
    {autoRecover: !isKurumStart}
  );
  if (preflight?.ok === false && preflight?.recovering) return getSessionSummary(sessionId);
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.RUNNING,
    pauseReason: null,
    pausedAt: null,
    lastHeartbeatAt: now,
    heartbeatFailures: 0,
    heartbeatWarning: null,
    lastHeartbeatError: null,
    loginRequiredAt: null,
    resumeAfterLogin: false,
    loginNotificationShown: false,
    loginNotificationFallback: false,
    loginNotificationError: null,
    sessionGuardStatus: "HEALTHY",
    recoveryCount: 0,
    healthyVerifiedSinceRecovery: 0,
    consecutiveStartFailures: 0,
    pacing: oldSession.pacing || { mode: "normal", successStreak: 0, failureStreak: 0, delayMs: 0, updatedAt: now },
    performance: oldSession.performance || { recentVerifiedAt: [], docsPerMinute: 0, attempts: 0, verifiedAttempts: 0, failedAttempts: 0 },
    agentVersion: agent.version,
    agentAvailable: true,
    agentCompletionGuardAvailable: agent.completionGuardAvailable === true,
    agentCompatibilityMode: agent.compatibilityMode || null,
    downloadsRoot: stored[LAST_DOWNLOADS_ROOT_KEY] || null,
    syncUnchanged: syncUnchangedCount,
    ...(wasEndpointRefreshResume ? {
      htmlCircuitState:"half_open",
      htmlCircuitHalfOpen:true,
      htmlCircuitProbeAttempt:HTML_CIRCUIT_MAX_SOFT_PROBES,
      htmlCircuitRecoverySuccesses:0,
      htmlCircuitHalfOpenedAt:now,
      htmlCircuitNextProbeAt:null,
      contentCooldownUntil:null,
      consecutiveHtmlFailures:0,
      lastHtmlFailureDocumentKey:null
    } : {}),
    // Starting an INCOMPLETE job is an explicit new completion attempt. Do not
    // let stale final-recovery/finalization state from the previous pass block
    // the manually requeued documents or make the UI look finished while the
    // queue is running again.
    ...(oldSession.status === SESSION_STATUS.INCOMPLETE ? {
      archiveStatus: "RETRYING",
      finalizationError: null,
      finalizationInProgress: false,
      finalIntegrity: null,
      partialArchiveAvailable: false,
      finalRecoveryActive: false,
      finalRecoveryPass: 0,
      finalRecoveryQueued: 0,
      finalRecoveryLastQueued: 0,
      finalRecoveryReason: null,
      finalRecoveryDocumentIds: [],
      finalRecoveryBlockedReason: null,
      finalRecoveryExhausted: false,
      finalRecoveryCompletedAt: null
    } : {})
  });
  await recordDiagnosticEvent(sessionId, "SESSION_STARTED", {
    preflightState: preflight?.state || "passed",
    preflightRecovered: Boolean(preflight?.recovered),
    syncUnchanged: syncUnchangedCount,
    agentVersion: agent.version || null,
    largeCaseMode: Boolean(preflightSession?.largeCaseMode),
    largeCaseCurrentBatch: Number(preflightSession?.largeCaseCurrentBatch || 0),
    largeCaseTotalBatches: Number(preflightSession?.largeCaseTotalBatches || 0)
  });
  await chrome.storage.local.set({ [ACTIVE_SESSION_KEY]: sessionId });
  await clearOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`);
  await clearLoginRequiredNotification(sessionId);
  await setSessionGuardBadge(false);
  await scheduleHeartbeat(sessionId);
  await scheduleQueueGuard(sessionId);
  await runNextQueueItem();
  return getSessionSummary(sessionId);
}

export async function pauseSession(sessionId) {
  await clearSessionRuntimeAlarms(sessionId);
  await restoreArchiveDownloadUi();
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: "USER_PAUSED",
    pausedAt: new Date().toISOString()
  });
  await clearLoginRequiredNotification(sessionId);
  await recordDiagnosticEvent(sessionId, "USER_PAUSED");
  return getSessionSummary(sessionId);
}

async function queueFinalRecovery(session, documents, {
  reason,
  candidateIds = null,
  integrity = null,
  mode = 'unresolved'
} = {}) {
  // A document that already produced HTML on its bounded recovery attempt is
  // terminal from the automatic queue's perspective. Keep it in user review;
  // final recovery must not erase that semantic failure and download it again.
  const recoverableDocuments = documents.filter((document) => !isTerminalHtmlReviewDocument(document));
  const terminalReviewIds = new Set(documents.filter((document) => isTerminalHtmlReviewDocument(document)).map((document) => String(document.documentId || "")));
  const filteredCandidateIds = candidateIds
    ? candidateIds.filter((id) => !terminalReviewIds.has(String(id)))
    : null;
  const plan = planFinalRecovery({
    documents: recoverableDocuments,
    total: session.total || documents.length,
    pass: session.finalRecoveryPass || 0,
    candidateIds: filteredCandidateIds,
    integrity,
    mode
  });
  if (!plan.allowed) {
    await sessionStore.update(session.sessionId, {
      finalRecoveryActive: false,
      finalRecoveryExhausted: plan.reason === 'PASS_LIMIT',
      finalRecoveryBlockedReason: plan.reason,
      finalRecoveryLimit: plan.limit,
      finalRecoveryMaxPasses: plan.maxPasses
    });
    return false;
  }

  const now = new Date().toISOString();
  const selectedIds = plan.documents.map(document => document.documentId);
  const canonicalRecoveryIds = new Set();
  await documentStore.putMany(plan.documents.map(document => {
    // RC15.9.20: if final integrity rejects a document that had only been
    // satisfied by local-archive reconciliation, the recovery pass must not
    // immediately re-adopt the same physical alias. Force one canonical
    // materialization attempt so the document gets its own deterministic path.
    // This is deliberately limited to final-integrity recovery candidates.
    const forceCanonicalRecovery = Boolean(
      mode === 'final_integrity' &&
      document.status === STATUS.SKIPPED &&
      document.skipReason === "UNCHANGED_LOCAL_ARCHIVE" &&
      document.localArchiveMatchState === "found"
    );
    if (forceCanonicalRecovery) canonicalRecoveryIds.add(String(document.documentId));
    return ({
    ...document,
    status: STATUS.QUEUED,
    largeBatchNo: session.largeCaseMode ? Math.max(1, Number(session.largeCaseCurrentBatch || session.largeCaseTotalBatches || 1)) : Number(document.largeBatchNo || 0),
    attempts: 0,
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryExhausted: false,
    retryStrategy: 'fresh_rebind',
    retryStrategyHistory: ['fresh_rebind'],
    attemptStrategy: null,
    startedAt: null,
    skipReason: forceCanonicalRecovery ? null : document.skipReason,
    forceCanonicalRecovery,
    forceCanonicalRecoveryReason: forceCanonicalRecovery ? (reason || mode || 'final_integrity') : null,
    forceCanonicalRecoveryPreviousMatchMethod: forceCanonicalRecovery ? (document.localArchiveMatchMethod || null) : null,
    localArchiveMatchState: forceCanonicalRecovery ? "final_recovery_canonical_required" : document.localArchiveMatchState,
    localArchiveCheckedAt: forceCanonicalRecovery ? null : document.localArchiveCheckedAt,
    localArchiveCheckedRoot: forceCanonicalRecovery ? null : document.localArchiveCheckedRoot,
    completionRecoveryCount: Number(document.completionRecoveryCount || 0) + 1,
    completionRecoveryPreviousErrorCode: document.errorCode || null,
    completionRecoveryReason: reason || mode,
    completionRecoveryQueuedAt: now,
    errorCode: null,
    errorMessage: null,
    warnings: [],
    updatedAt: now
  });
  }));

  if (canonicalRecoveryIds.size) {
    await recordDiagnosticEvent(session.sessionId, "FINAL_CANONICAL_RECOVERY_QUEUED", {
      documents: canonicalRecoveryIds.size,
      documentIds: [...canonicalRecoveryIds].slice(0, 20)
    });
  }

  const history = Array.isArray(session.finalRecoveryHistory) ? session.finalRecoveryHistory : [];
  await sessionStore.update(session.sessionId, {
    status: SESSION_STATUS.RUNNING,
    archiveStatus: 'RECOVERING',
    finalizationError: null,
    unresolved: selectedIds.length,
    finalRecoveryActive: true,
    finalRecoveryPass: plan.nextPass,
    finalRecoveryMaxPasses: plan.maxPasses,
    finalRecoveryLimit: plan.limit,
    finalRecoveryQueued: selectedIds.length,
    finalRecoveryLastQueued: selectedIds.length,
    finalRecoveryReason: reason || mode,
    finalRecoveryDocumentIds: selectedIds,
    finalRecoveryStartedAt: now,
    finalRecoveryBlockedReason: null,
    finalRecoveryExhausted: false,
    finalRecoveryHistory: [...history, {
      pass: plan.nextPass,
      reason: reason || mode,
      count: selectedIds.length,
      documentIds: selectedIds,
      startedAt: now
    }].slice(-8)
  });
  queueRequested = true;
  return true;
}

export async function runNextQueueItem() {
  if (queueLock) { queueRequested = true; return; }
  queueLock = true;
  const keepAlive = setInterval(() => void chrome.storage.local.get(ACTIVE_SESSION_KEY), 20_000);

  try {
    const sessionId = await getActiveSessionId();
    if (!sessionId) return;

    let session = await sessionStore.get(sessionId);
    if (!session || session.status !== SESSION_STATUS.RUNNING) return;
    // Enforce at the request boundary; reactivation cannot bypass exhaustion.
    if (htmlCircuitState(session) === "open" && Number(session.htmlCircuitProbeAttempt || 0) >= HTML_CIRCUIT_MAX_SOFT_PROBES) {
      await clearSessionRuntimeAlarms(sessionId);
      await sessionStore.update(sessionId, {
        status:SESSION_STATUS.PAUSED, pauseReason:"CONTENT_ENDPOINT_REFRESH_REQUIRED",
        sessionGuardStatus:"CONTENT_ENDPOINT_REFRESH_REQUIRED", resumeAfterRecovery:false,
        htmlCircuitNextProbeAt:null, contentCooldownUntil:null, nextDownloadAt:0
      });
      return;
    }
    await clearExpiredOrphanPendingDownload(sessionId, {reason:"queue-entry"});

    // RC15.9.10: circuit time is authoritative and independent from normal
    // pacing. Validation/performance updates are not allowed to shorten it.
    if (htmlCircuitState(session) === "open") {
      const deadlineMs = htmlCircuitDeadlineMs(session);
      if (deadlineMs > Date.now()) {
        await scheduleQueueWake(sessionId, deadlineMs);
        return;
      }
      const nowIso = new Date().toISOString();
      const probeAttempt = Math.max(1, Number(session.htmlCircuitProbeAttempt || 0) + 1);
      await sessionStore.update(sessionId, {
        htmlCircuitState: "half_open",
        htmlCircuitHalfOpen: true,
        htmlCircuitProbeAttempt: probeAttempt,
        htmlCircuitRecoverySuccesses: 0,
        htmlCircuitHalfOpenedAt: nowIso,
        htmlCircuitNextProbeAt: null,
        contentCooldownUntil: null,
        nextDownloadAt: Date.now(),
        sessionGuardStatus: "CONTENT_CIRCUIT_HALF_OPEN"
      });
      await recordDiagnosticEvent(sessionId, "HTML_CIRCUIT_HALF_OPEN", {
        episode: Number(session.htmlCircuitEpisode || 1),
        probeAttempt,
        requiredStableVerified: HTML_CIRCUIT_STABLE_SUCCESSES
      });
      session = await sessionStore.get(sessionId);
    }
    if (session.nextDownloadAt > Date.now()) {
      await scheduleQueueWake(sessionId, session.nextDownloadAt);
      return;
    }
    if (await documentStore.hasActiveDownload(sessionId)) return;

    const pipelineBatch = session.largeCaseMode
      ? Math.max(1, Math.min(Number(session.largeCaseTotalBatches || 1), Number(session.largeCaseCurrentBatch || 1)))
      : null;
    const circuitHalfOpenProbe = htmlCircuitState(session) === "half_open";
    if (sessionValidationCount(sessionId) >= (circuitHalfOpenProbe ? 1 : MAX_PIPELINED_VALIDATIONS)) {
      await scheduleQueueWake(sessionId, Date.now() + 80);
      return;
    }
    if (pipelineBatch) {
      await ensureLocalArchiveBatchReconciled(sessionId, pipelineBatch, {downloadsRoot:session.downloadsRoot, nativePriority:0}).catch(async (error) => {
        await recordDiagnosticEvent(sessionId, "AUTO_PIPELINE_BATCH_RECONCILE_FAILED", {
          batchNo:pipelineBatch,
          error:sanitizeDiagnosticText(error, 240)
        }).catch(()=>{});
      });
      session = await sessionStore.get(sessionId);
      if (!session || session.status !== SESSION_STATUS.RUNNING) return;
      // Bulk-lane prefetch intentionally runs without blocking the browser lane.
      void prefetchNextLocalArchiveBatch(sessionId, pipelineBatch);
    }

    if (session.sessionGuardStatus === "DOWNLOAD_IN_PROGRESS_SOURCE_DEFERRED" || heartbeatIsDue(session)) {
      const healthy = await performSessionHeartbeat(session);
      if (!healthy) return;
    }

    const queueLookupStartedAt = Date.now();
    const nowIso = new Date().toISOString();
    const activeLargeBatch = session.largeCaseMode
      ? Math.max(1, Math.min(Number(session.largeCaseTotalBatches || 1), Number(session.largeCaseCurrentBatch || 1)))
      : null;
    let [queued, manualRetry, readyRetry] = await Promise.all([
      documentStore.nextQueued(sessionId, activeLargeBatch),
      // Explicit user retries may cross batch boundaries; user intent wins.
      documentStore.nextReadyManualRetry(sessionId, nowIso),
      documentStore.nextReadyRetry(sessionId, nowIso, activeLargeBatch)
    ]);
    if (manualRetry?.errorCode === "HTML_RESPONSE" && manualRetry.retryClass !== "deferred_html") {
      manualRetry = await migrateLegacyHtmlRetry(manualRetry);
    }
    if (readyRetry?.errorCode === "HTML_RESPONSE" && readyRetry.retryClass !== "deferred_html") {
      readyRetry = await migrateLegacyHtmlRetry(readyRetry);
    }
    const primarySuccessesSinceRetry = Math.max(0, Number(session.primarySuccessesSinceRetry || 0));
    const hasFutureLargeBatches = Boolean(
      session.largeCaseMode &&
      activeLargeBatch &&
      activeLargeBatch < Number(session.largeCaseTotalBatches || 1)
    );

    // RC15.2: an automatic HTML retry must never pin the end of a 500-document
    // batch. If fresh primary batches remain, quarantine that HTML document and
    // advance to the next batch. Explicit user retries still win immediately.
    // This keeps the first pass sequential and fast while preserving a bounded
    // deferred-recovery phase after the untouched inventory drains.
    const deferAutomaticHtmlUntilPrimaryDrain = Boolean(
      !manualRetry &&
      !queued &&
      hasFutureLargeBatches &&
      readyRetry &&
      readyRetry.retryClass === "deferred_html"
    );
    if (deferAutomaticHtmlUntilPrimaryDrain) readyRetry = null;

    const retryIsDueForFairness = shouldRunDeferredRetry({
      queued,
      readyRetry,
      primarySuccessesSinceRetry,
      nowMs: Date.now()
    });
    // A user-requested "Şimdi dene" retry is the only deferred item allowed
    // to jump ahead of untouched primary inventory. hasActiveDownload() above
    // guarantees that the current document still finishes first.
    const next = manualRetry || (retryIsDueForFairness ? readyRetry : (queued || readyRetry));
    if (!manualRetry && next === readyRetry && readyRetry) {
      await sessionStore.update(sessionId, {
        primarySuccessesSinceRetry: 0,
        lastDeferredRetryAt: new Date().toISOString()
      });
    }
    const queueLookupMs = Date.now() - queueLookupStartedAt;
    if (!next) {
      const futureRetry = await documentStore.nextFutureRetry(sessionId, nowIso, activeLargeBatch);
      const deferFutureHtmlUntilPrimaryDrain = Boolean(
        futureRetry &&
        futureRetry.retryClass === "deferred_html" &&
        hasFutureLargeBatches
      );
      if (futureRetry?.retryAt && !deferFutureHtmlUntilPrimaryDrain) {
        const retryAt = Date.parse(futureRetry.retryAt);
        await scheduleQueueWake(sessionId, retryAt);
        return;
      }

      if (session.largeCaseMode && activeLargeBatch && activeLargeBatch < Number(session.largeCaseTotalBatches || 1)) {
        if (sessionValidationCount(sessionId, activeLargeBatch) > 0) {
          await scheduleQueueWake(sessionId, Date.now() + 40);
          return;
        }
        const nextBatch = activeLargeBatch + 1;
        const transitionDelayMs = largeCaseBatchTransitionDelayMs(session);
        const wakeAt = Date.now() + transitionDelayMs;
        await sessionStore.update(sessionId, {
          largeCaseCompletedBatches: activeLargeBatch,
          largeCaseCurrentBatch: nextBatch,
          largeCaseLastBatchCompletedAt: new Date().toISOString(),
          largeCaseNextBatchAt: new Date(wakeAt).toISOString(),
          largeCaseLastTransitionDelayMs: transitionDelayMs,
          nextDownloadAt: wakeAt
        });
        await recordDiagnosticEvent(sessionId, "LARGE_CASE_BATCH_COMPLETED", {
          completedBatch: activeLargeBatch,
          nextBatch,
          totalBatches: Number(session.largeCaseTotalBatches || 1),
          batchSize: Number(session.largeCaseBatchSize || LARGE_CASE_BATCH_SIZE),
          transitionMode: "adaptive",
          transitionDelayMs,
          pacingMode: session?.pacing?.mode || "normal",
          windowHtmlRate: Number(session?.pacing?.windowHtmlRate || 0),
          windowErrorRate: Number(session?.pacing?.windowErrorRate || 0)
        });
        await scheduleQueueWake(sessionId, wakeAt);
        return;
      }

      if (sessionValidationCount(sessionId) > 0) {
        await scheduleQueueWake(sessionId, Date.now() + 40);
        return;
      }

      // Full-session materialization is intentionally delayed until the queue
      // is empty and final integrity checks are about to run. It must never be
      // part of the per-document hot path.
      const documents = await documentStore.bySession(sessionId);
      await clearOperationalAlarm(`${HEARTBEAT_ALARM_PREFIX}${sessionId}`);
      const unresolved = documents.filter((doc) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(doc.status));
      if (!session.downloadsRoot) {
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.INCOMPLETE,
          archiveStatus: "INCOMPLETE",
          unresolved: unresolved.length,
          finalizationError: "DOWNLOAD_ROOT_UNKNOWN",
          pauseReason: null,
          pausedAt: null,
          finalRecoveryActive: false
        });
        return;
      }
      if (unresolved.length && await queueFinalRecovery(session, documents, {
        reason: 'UNRESOLVED_AFTER_RETRIES',
        mode: 'unresolved'
      })) return;
      // Final recovery is bounded. Once it is exhausted, package the verified
      // subset into independently verified ZIP parts instead of blocking a
      // 12k-document job forever because a handful of documents are bad.
      // The session remains INCOMPLETE and the manifest clearly marks the
      // export PARTIAL_WITH_ERRORS; no completeness claim is made.

      const currentIds = new Set();
      const currentDocuments = new Map();
      const frames = await uyapFrames(session.tabId).catch(() => []);
      // Final source verification has two materially different failure modes:
      // 1) the live page cannot be scanned at all (service worker/frame/navigation race),
      // 2) the live page was scanned and proves that the source inventory changed.
      // The old code collapsed both into SOURCE_INVENTORY_CHANGED, which left a
      // fully verified local archive without its final ZIP/manifest. Keep the
      // fail-closed behavior for a proven inventory conflict, but allow a local
      // final-package fallback when the only problem is that the live source
      // could not be re-read after every document was already verified.
      let sourceScanUnavailable = !frames.length;
      let sourceScanConflict = false;
      let successfulFrameScans = 0;
      for (const frame of frames || []) {
        try {
          const scan = await sendToFrame(session.tabId, frame.frameId, {type: MESSAGE.SCAN_PAGE});
          if (!scan?.ok) throw new Error('FINAL_SCAN_FAILED');
          successfulFrameScans += 1;
          for (const d of scan?.data?.documents || []) {
            if ((d.sourceSystem || 'UYAP') !== (documents[0].sourceSystem || 'UYAP') || d.caseId !== documents[0].caseId || d.courtName !== documents[0].courtName) sourceScanConflict = true;
            const seen=currentDocuments.get(d.documentId);
            if (seen && seen.identityEvidence !== d.identityEvidence) sourceScanConflict = true;
            currentIds.add(d.documentId);
            currentDocuments.set(d.documentId,{...d,frameId:frame.frameId});
          }
        } catch { sourceScanUnavailable = true; }
      }
      if (successfulFrameScans <= 0) sourceScanUnavailable = true;
      const missing = documents.filter(d => !currentIds.has(d.documentId));
      const knownIds = new Set(documents.map(d => d.documentId));
      const added = [...currentDocuments.values()].filter(d => !knownIds.has(d.documentId));
      if (!sourceScanUnavailable && !sourceScanConflict && !missing.length && added.length && (session.inventoryPasses || 0) < 2) {
        const now = new Date().toISOString();
        await documentStore.putMany(added.map((raw, index) => ({...raw,key:`${sessionId}:${raw.documentId}`,sessionId,tabId:session.tabId,
          largeBatchNo:session.largeCaseMode ? Math.floor((documents.length + index) / LARGE_CASE_BATCH_SIZE) + 1 : null,
          targetPath:buildTargetPath(raw),sourceFingerprint:sourceFingerprint(raw),downloadId:null,status:STATUS.QUEUED,
          attempts:0,warnings:[],errorCode:null,discoveredAt:now,updatedAt:now})));
        await sessionStore.update(sessionId,{total:documents.length+added.length,inventoryPasses:(session.inventoryPasses||0)+1,
          largeCaseTotalBatches:session.largeCaseMode ? Math.ceil((documents.length+added.length)/LARGE_CASE_BATCH_SIZE) : 0,
          finalIntegrity:null, unresolved:documents.filter(d => ![STATUS.VERIFIED,STATUS.SKIPPED].includes(d.status)).length + added.length});
        queueRequested=true;
        return;
      }
      const provenInventoryChanged = sourceScanConflict || added.length > 0 || (!sourceScanUnavailable && (currentIds.size !== documents.length || missing.length > 0));
      if (provenInventoryChanged) {
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.INCOMPLETE,
          archiveStatus: "INCOMPLETE",
          finalizationError: "SOURCE_INVENTORY_CHANGED",
          unresolved: missing.length,
          finalSourceScanStatus: "changed",
          finalSourceScanAt: new Date().toISOString()
        });
        await recordDiagnosticEvent(sessionId, "FINAL_SOURCE_INVENTORY_CHANGED", {
          known: documents.length, observed: currentIds.size, missing: missing.length, added: added.length, conflict: sourceScanConflict
        });
        return;
      }
      if (sourceScanUnavailable) {
        await recordDiagnosticEvent(sessionId, "FINAL_SOURCE_RESCAN_UNAVAILABLE", {
          frames: (frames || []).length, successfulFrameScans, unresolved: unresolved.length
        });
        if (!unresolved.length) {
          // All captured source records are already cryptographically verified on
          // disk. Reuse the same strict native finalization gate that powers the
          // manual "Final paketi oluştur" action. This does not re-download or
          // weaken SHA-256/content/path validation; it merely avoids requiring a
          // live UYAP frame for the packaging-only step.
          await sessionStore.update(sessionId, {
            status: SESSION_STATUS.INCOMPLETE,
            archiveStatus: "INCOMPLETE",
            finalizationError: "SOURCE_RESCAN_UNAVAILABLE_LOCAL_FINALIZATION",
            unresolved: 0,
            finalSourceScanStatus: "unavailable-local-fallback",
            finalSourceScanAt: new Date().toISOString(),
            finalizationInProgress: false
          });
          try {
            await repackageVerifiedArchive(sessionId);
          } catch (error) {
            await sessionStore.update(sessionId, {
              finalizationError: `AUTO_FINAL_PACKAGE_FAILED: ${String(error).slice(0, 900)}`,
              finalizationInProgress: false
            });
            await recordDiagnosticEvent(sessionId, "AUTO_FINAL_PACKAGE_FAILED", {error:sanitizeDiagnosticText(error, 240)});
          }
          return;
        }
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.INCOMPLETE,
          archiveStatus: "INCOMPLETE",
          finalizationError: "SOURCE_RESCAN_UNAVAILABLE",
          unresolved: unresolved.length,
          finalSourceScanStatus: "unavailable",
          finalSourceScanAt: new Date().toISOString()
        });
        return;
      }
      await recordDiagnosticEvent(sessionId, "FINALIZATION_STARTED", {
        expected: session.total,
        unresolved: unresolved.length,
        reviewRequired: documents.filter(d => d.status === STATUS.REVIEW_REQUIRED).length
      });
      await sessionStore.update(sessionId, {
        finalizationInProgress: true,
        finalizationStartedAt: new Date().toISOString(),
        finalizationError: null
      });
      const packageDocuments = finalizationDocuments(documents);
      const excludedDocuments = userExcludedDocuments(documents);
      const exclusionOnly = !unresolved.length ? allUserExcludedFinalization(documents, excludedDocuments) : null;
      const finalization = exclusionOnly || await askNativeAgent({
        action: "finalize_archive",
        sessionId,
        downloadsRoot: session.downloadsRoot,
        expected: packageDocuments.length,
        sourceInventoryTotal: documents.length,
        userExcludedDocuments: excludedDocuments.map((d) => ({documentId:d.documentId, reason:d.skipReason, excludedAt:d.userExcludedAt || null})),
        documentIds: packageDocuments.map(d => d.documentId),
        reviewRequired: documents.filter(d => d.status === STATUS.REVIEW_REQUIRED).length,
        allowPartial: unresolved.length > 0,
        partSizeBytes: ARCHIVE_PART_TARGET_BYTES,
        maxDocumentsPerPart: ARCHIVE_PART_MAX_DOCUMENTS,
        documents: packageDocuments.map(d => nativeFinalizationDocument(d, session))
      });
      if (finalization.integrity) {
        finalization.integrity.sourceInventoryTotal = documents.length;
        finalization.integrity.userExcludedCount = excludedDocuments.length;
      }
      const finalizationRepairedIds = [...new Set(finalization.repairedIds || [])];
      if (finalizationRepairedIds.length) {
        const repairedAt = new Date().toISOString();
        for (const id of finalizationRepairedIds) {
          const doc = documents.find(d => d.documentId === id);
          if (!doc) continue;
          const cleanedWarnings = (Array.isArray(doc.warnings) ? doc.warnings : []).filter((warning) => ![
            "NATIVE_OPERATION_FAILED", "ARCHIVE_INTEGRITY_FAILED", "TRANSFER_INTEGRITY_FAILED"
          ].includes(String(warning || "")));
          const repairedChanges = {
            status: STATUS.VERIFIED,
            errorCode: null,
            errorMessage: null,
            retryAt: null,
            retryClass: null,
            retryExhausted: false,
            invalidRetryHint: null,
            observedInvalidSize: null,
            warnings: cleanedWarnings,
            finalizationStatusRecoveredAt: repairedAt
          };
          await documentStore.update(doc.key, repairedChanges);
          Object.assign(doc, repairedChanges);
        }
        await recordDiagnosticEvent(sessionId, "FINALIZATION_STATUS_RECONCILED", {
          documents: finalizationRepairedIds.length
        });
      }
      const finalRecoveryIds = [...new Set([...(finalization.invalidIds || []), ...(finalization.missingIds || [])])];
      const hasNonRecoverableIntegrityConflict = Boolean(
        Number(finalization.unexpectedCount ?? (finalization.unexpectedIds || []).length) ||
        Number(finalization.integrity?.duplicateDocumentCount ?? (finalization.integrity?.duplicateDocumentIds || []).length) ||
        Number(finalization.integrity?.blockingDuplicateSourceDocumentCount ?? finalization.integrity?.duplicateSourceDocumentCount ?? (finalization.integrity?.blockingDuplicateSourceDocumentIds || finalization.integrity?.duplicateSourceDocumentIds || []).length) ||
        Number(finalization.integrity?.duplicatePathCount ?? (finalization.integrity?.duplicatePaths || []).length) ||
        Number(finalization.integrity?.fingerprintMismatchCount ?? (finalization.integrity?.fingerprintMismatchIds || []).length)
      );
      if (!finalization.integrity?.gatePassed && finalRecoveryIds.length && !hasNonRecoverableIntegrityConflict) {
        if (await queueFinalRecovery(session, documents, {
          reason: 'FINAL_INTEGRITY_MISSING_OR_INVALID',
          candidateIds: finalRecoveryIds,
          integrity: finalization.integrity,
          mode: 'final_integrity'
        })) return;
      }
      for (const id of finalRecoveryIds) {
        const doc = documents.find(d => d.documentId === id);
        if (!doc) continue;
        if (isTerminalHtmlReviewDocument(doc)) {
          await normalizeTerminalHtmlReviewDocument(doc);
        } else {
          const preserveLocalRevalidationError = Boolean(
            doc.quarantinePath && LOCAL_REVALIDATION_CODES.has(String(doc.errorCode || ""))
          );
          await documentStore.update(doc.key, {
            status: preserveLocalRevalidationError ? STATUS.REVIEW_REQUIRED : STATUS.FAILED,
            errorCode: preserveLocalRevalidationError ? doc.errorCode : "ARCHIVE_INTEGRITY_FAILED",
            errorMessage: preserveLocalRevalidationError ? doc.errorMessage : doc.errorMessage
          });
        }
      }
      await sessionStore.update(sessionId, {
        status: finalization.ok && finalization.archiveStatus === "VERIFIED_COMPLETE" && finalization.integrity?.gatePassed
          ? SESSION_STATUS.COMPLETED
          : SESSION_STATUS.INCOMPLETE,
        archiveStatus: finalization.archiveStatus || "INCOMPLETE",
        partialArchiveAvailable: finalization.archiveStatus === "PARTIAL_WITH_ERRORS",
        unresolved: finalization.unresolved ?? unresolved.length,
        finalPackagePath: finalization.finalPackagePath || null,
        manifestPath: finalization.manifestPath || null,
        zipPath: finalization.zipPath || null,
        manifestSha256: finalization.manifestSha256 || null,
        zipSha256: finalization.zipSha256 || null,
        archiveFormat: finalization.archiveFormat || null,
        archiveParts: finalization.archiveParts || [],
        partsManifestPath: finalization.partsManifestPath || null,
        partsManifestSha256: finalization.partsManifestSha256 || null,
        checksumsPath: finalization.checksumsPath || null,
        checksumsSha256: finalization.checksumsSha256 || null,
        finalIntegrity: finalization.integrity || null,
        userExcludedCount: excludedDocuments.length,
        finalizationError: finalization.ok ? null : finalization.error,
        finalizationInProgress: false,
        finalRecoveryActive: false,
        finalRecoveryQueued: 0,
        finalRecoveryCompletedAt: finalization.ok && finalization.archiveStatus === "VERIFIED_COMPLETE" && finalization.integrity?.gatePassed
          ? new Date().toISOString()
          : null,
        pauseReason: null,
        pausedAt: null
      });
      await recordDiagnosticEvent(sessionId, "FINALIZATION_FINISHED", {
        ok: Boolean(finalization.ok),
        archiveStatus: finalization.archiveStatus || "INCOMPLETE",
        gatePassed: Boolean(finalization.integrity?.gatePassed),
        partCount: Number(finalization.integrity?.partCount || 0),
        unresolved: Number(finalization.unresolved ?? unresolved.length)
      });
      const finalizedSession = await sessionStore.get(sessionId);
      if (finalizedSession?.status === SESSION_STATUS.COMPLETED) {
        await showArchiveCompletionNotification(finalizedSession);
      } else if (finalizedSession?.status === SESSION_STATUS.INCOMPLETE && Number(finalizedSession.unresolved || 0) > 0) {
        await showArchiveProblemNotification(finalizedSession);
      }
      await restoreArchiveDownloadUi();
      await clearOperationalAlarm(queueGuardAlarmName(sessionId));
      return;
    }

    // RC15.3: before re-downloading a structural start failure, try to adopt the
    // exact canonical browser file left behind by RC15.1/older correlation races.
    // Adoption is fail-closed: exactly one completed evrak_<id> candidate must
    // exist inside the failed attempt's time window, and native validation still
    // runs before the document can become VERIFIED.
    if (await recoverStrandedCanonicalDownload(next)) {
      queueRequested = true;
      return;
    }

    // Last-moment duplicate gate: the local archive is authoritative. Even if a
    // queue was created long ago, manually retried, or another session archived
    // the same document in the meantime, never click UYAP before checking disk.
    const localGate = await guardDocumentAgainstLocalArchive(next, session);
    if (localGate.decision !== "download") {
      queueRequested = true;
      return;
    }

    await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: Date.now() + 30_000});
    await startDocumentDownload(next, { queueLookupMs });
  } catch (error) {
    const id = await getActiveSessionId();
    if (id) {
      const current = await sessionStore.get(id);
      const count = Math.max(0, Number(current?.queueRuntimeRecoveryCount || 0)) + 1;
      const delayMs = [500, 1_500, 3_000, 5_000][Math.min(Math.max(0, count - 1), 3)];
      await sessionStore.update(id, {
        status: SESSION_STATUS.RECOVERING,
        pauseReason: "QUEUE_RUNTIME_RECOVERY",
        queueError: String(error).slice(0, 1200),
        queueRuntimeRecoveryCount: count,
        recoveryStartedAt: current?.recoveryStartedAt || new Date().toISOString(),
        resumeAfterRecovery: true,
        sessionGuardStatus: "AUTO_RECOVERY",
        finalizationInProgress: false
      });
      await recordDiagnosticEvent(id, "QUEUE_RUNTIME_RECOVERY", {
        recoveryCount: count,
        delayMs,
        error: sanitizeDiagnosticText(error, 240)
      });
      await scheduleOperationalAlarm(recoveryAlarmName(id), {when: Date.now() + delayMs});
    }
    await restoreArchiveDownloadUi();
  } finally {
    clearInterval(keepAlive);
    queueLock = false;
    if (queueRequested) {
      queueRequested = false;
      setTimeout(() => void runNextQueueItem(), 0);
    }
  }
}

async function waitForNativeDownloadBinding(documentKey, attemptId, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const latest = await documentStore.get(documentKey);
    if (!latest || latest.attemptId !== attemptId) return latest;
    if (latest.status !== STATUS.AWAITING_BROWSER) return latest;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return documentStore.get(documentKey);
}

async function recoverRecentNativeDownload(document, pending) {
  // Event delivery can lag behind the Save handler. Exact source-id evidence is
  // always preferred. If UYAP emitted a generic image/download filename, the
  // expected-download lease may adopt it only when it is the sole new UYAP-origin
  // candidate created after this click attempt was armed.
  let items = [];
  try {
    items = await chrome.downloads.search({
      startedAfter: new Date(Number(pending.leaseArmedAt || pending.createdAt || Date.now()) - 500).toISOString(),
      limit: EXPECTED_DOWNLOAD_BASELINE_LIMIT
    });
  } catch {
    try { items = await chrome.downloads.search({}); } catch { return null; }
  }
  const candidates = (items || [])
    .filter((item) => {
      const started = downloadStartAtMs(item);
      return started === null || (started >= Number(pending.leaseArmedAt || pending.createdAt || 0) - 500 && started <= Date.now() + 1_000);
    })
    .sort((a, b) => (downloadStartAtMs(b) || 0) - (downloadStartAtMs(a) || 0));

  // Pass 1: deterministic exact/canonical evidence.
  for (const item of candidates) {
    const latest = await documentStore.get(document.key);
    const correlation = correlateDirectUyapDownload(item, latest, pending);
    if (!correlation) continue;
    const detected = detectDownloadExtension(item);
    await attachBrowserDownload(item, pending, {
      stagingPath: latest.stagingPath || pending.stagingPath,
      sourceDocumentId: correlation.sourceDocumentId,
      bindingMethod: `${correlation.bindingMethod}-history-recovery`,
      correlationMode: 'exact_history_recovery',
      correlationCandidateCount: 1,
      originalFilename: downloadBasename(item),
      extension: detected.extension,
      extensionSource: detected.source
    });
    if (["complete", "interrupted"].includes(item.state)) {
      await handleDownloadChanged({id:item.id, state:{current:item.state}});
    }
    return item;
  }

  // Pass 2: fail-closed single-candidate lease fallback for generic UYAP names.
  for (const item of candidates) {
    const latest = await documentStore.get(document.key);
    const correlation = await correlateLeaseSingleCandidate(item, latest, pending);
    if (!correlation || correlation.bindingMethod !== 'native-uyap-single-candidate-lease') continue;
    const detected = detectDownloadExtension(item);
    await attachBrowserDownload(item, pending, {
      stagingPath: latest.stagingPath || pending.stagingPath,
      sourceDocumentId: correlation.sourceDocumentId,
      bindingMethod: `${correlation.bindingMethod}-history-recovery`,
      correlationMode: 'single_candidate_lease_history_recovery',
      correlationCandidateCount: correlation.candidateCount,
      originalFilename: downloadBasename(item),
      extension: detected.extension,
      extensionSource: detected.source
    });
    if (["complete", "interrupted"].includes(item.state)) {
      await handleDownloadChanged({id:item.id, state:{current:item.state}});
    }
    return item;
  }
  return null;
}

async function startDocumentDownload(document, metrics = {}) {
  await suppressArchiveDownloadUi();
  let workingDocument = document;
  const strategy = attemptStrategyForDocument(document);
  const attemptRecoveryClass = document.retryClass || null;
  const htmlRecoveryAttempts = Math.max(0, Number(document.htmlRecoveryAttempts || 0)) +
    (attemptRecoveryClass === "deferred_html" ? 1 : 0);

  // Retry attempts may deliberately refresh runtime DOM bindings before they
  // click. This changes the failure path without weakening identity checks.
  if (strategy.freshRebind) {
    const binding = await rebindSingleDocument(document);
    if (!binding.ok) {
      if (binding.contextMismatch) {
        await pauseForSourceContextChanged(document.sessionId, binding);
        return;
      }
      await handleFailure(document, "DOWNLOAD_START_FAILED", `TARGETED_REBIND_FAILED: ${binding.error || 'Evrak bulunamadı.'}`);
      void runNextQueueItem();
      return;
    }
    workingDocument = binding.document || await documentStore.get(document.key) || document;
  }

  const attempts = (workingDocument.attempts ?? 0) + 1;
  const startedAt = new Date().toISOString();
  const attemptId = crypto.randomUUID();
  const stagingPath = buildStagingPath(workingDocument.sessionId, attemptId);
  const attemptStartedAtMs = Date.now();
  const clickTimeoutMs = clickTimeoutForDocument(workingDocument);
  const baselineDownloadIds = await expectedDownloadBaselineIds();
  const pending = {
    key: workingDocument.key,
    sessionId: workingDocument.sessionId,
    documentId: workingDocument.runtimeDocumentId || workingDocument.documentId,
    logicalDocumentId: workingDocument.documentId,
    attemptId,
    tabId: workingDocument.tabId,
    stagingPath,
    createdAt: attemptStartedAtMs,
    leaseVersion: 1,
    leaseArmedAt: attemptStartedAtMs,
    leaseExpiresAt: attemptStartedAtMs + clickTimeoutMs,
    baselineDownloadIds,
    strategy: strategy.name,
    captureWindowMs: strategy.captureWindowMs,
    clickTimeoutMs
  };
  await setPendingDownload(pending);
  await documentStore.update(workingDocument.key, {
    attempts,
    attemptId,
    attemptStrategy: strategy.name,
    stagingPath,
    downloadId: null,
    status: STATUS.AWAITING_BROWSER,
    startedAt,
    errorCode: null,
    errorMessage: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    manualRetryPriority: false,
    retryPriority: 1,
    manualRetryStartedAt: workingDocument.manualRetryPriority === true ? startedAt : workingDocument.manualRetryStartedAt || null,
    attemptRecoveryClass,
    htmlRecoveryAttempts,
    attemptStartedAtMs,
    perfQueueLookupMs: Number.isFinite(Number(metrics.queueLookupMs)) ? Number(metrics.queueLookupMs) : null,
    perfClickToDownloadMs: null,
    downloadStartedAtMs: null,
    perfDownloadMs: null,
    perfValidationMs: null,
    perfTotalMs: null,
    watchdogLastBytesReceived: 0,
    watchdogLastProgressAt: null,
    watchdogLastObservedAt: null,
    watchdogResumeAttemptAt: null
  });
  await scheduleOperationalAlarm(clickTimeoutAlarmName(workingDocument.sessionId), {
    when: Date.now() + pending.clickTimeoutMs
  });

  let clickPhaseMs = null;
  let nativeWaitMs = null;
  let historyRecoveryMs = null;
  try {
    const clickStartedAtMs = Date.now();
    // Request data is transient; auth query/form values never enter storage/report.
    const clickResult = workingDocument.sourceUrl
      ? {request: validateDownloadRequest({url:workingDocument.sourceUrl}), captureStatus:"source-url"}
      : await clickDocumentAcrossFrames(workingDocument, attemptId, strategy);
    clickPhaseMs = metricDuration(clickStartedAtMs);
    const pendingAfterClick = await getPendingDownload();
    const networkFallback = pendingAfterClick?.attemptId === attemptId
      ? strongNetworkFallbackRequest(pendingAfterClick)
      : null;
    const capturedRequest = clickResult?.request
      ? validateDownloadRequest(clickResult.request)
      : networkFallback;
    const strongNetworkObserved = Boolean(
      pendingAfterClick?.attemptId === attemptId &&
      Number(pendingAfterClick?.networkHint?.score || 0) >= 80
    );

    // A direct source URL is itself authoritative. RC15.9 also treats a strong
    // read-only webRequest observation as timing evidence: the real UYAP request
    // has already left the page, so we need only a short native-download grace
    // window before using a safely reproducible GET fallback. POST bodies are
    // never reconstructed from the observer and still depend on page capture.
    const sourceUrlRequest = Boolean(workingDocument.sourceUrl && capturedRequest);
    const allowFallback = sourceUrlRequest || strategy.allowFallback;
    const fallbackRequest = allowFallback ? capturedRequest : null;
    const fallbackExpectedDocumentKind = fallbackRequest ? declaredUyapDocumentKind(fallbackRequest.url) : null;
    const nativeWaitBudgetMs = sourceUrlRequest
      ? 0
      : (capturedRequest && allowFallback
          ? (strongNetworkObserved ? Math.min(250, strategy.nativePreferenceMs) : strategy.nativePreferenceMs)
          : (strongNetworkObserved ? Math.min(1_500, strategy.nativeOnlyWaitMs) : strategy.nativeOnlyWaitMs));
    const nativeWaitStartedAtMs = Date.now();
    if (nativeWaitBudgetMs > 0) {
      await waitForNativeDownloadBinding(workingDocument.key, attemptId, nativeWaitBudgetMs);
    }
    nativeWaitMs = metricDuration(nativeWaitStartedAtMs);

    const latest = await documentStore.get(workingDocument.key);
    const session = await sessionStore.get(workingDocument.sessionId);
    if (latest?.attemptId !== attemptId || latest.status !== STATUS.AWAITING_BROWSER) return;
    if (session?.status !== SESSION_STATUS.RUNNING) {
      if (session?.status === SESSION_STATUS.PAUSED && session.pauseReason === "LOGIN_REQUIRED") {
        // Do not immediately retire an in-flight Save when logout is detected.
        // A download already emitted by UYAP may arrive a few seconds later and
        // can still be validated safely under this attemptId. The session-drain
        // alarm will neutral-requeue it if nothing arrives.
        await scheduleOperationalAlarm(sessionDrainAlarmName(workingDocument.sessionId), {
          when: Date.now() + SESSION_LOSS_DRAIN_MS
        });
        return;
      }
      await documentStore.update(workingDocument.key, {
        status: STATUS.QUEUED,
        attempts: Math.max(0, Number(latest.attempts || 1) - 1),
        attemptId: null,
        stagingPath: null,
        startedAt: null,
        attemptRecoveryClass: null
      });
      await clearPendingDownload();
      return;
    }

    {
      const historyStartedAtMs = Date.now();
      const recovered = await recoverRecentNativeDownload(workingDocument, pending);
      historyRecoveryMs = metricDuration(historyStartedAtMs);
      if (recovered) return;
    }

    if (!fallbackRequest) {
      await clearPendingDownload();
      await clearOperationalAlarm(clickTimeoutAlarmName(workingDocument.sessionId));
      const correlationError = clickResult?.captureStatus || "UYAP Kaydet sonrası ilişkilendirilebilir tarayıcı indirmesi görülmedi.";
      const failureLatencyMs = metricDuration(attemptStartedAtMs);
      await applyAttemptPerformance(workingDocument.sessionId, {
        documentKey: workingDocument.key,
        queueLookupMs: metrics.queueLookupMs,
        failureLatencyMs,
        failureClickPhaseMs: clickPhaseMs,
        failureNativeWaitMs: nativeWaitMs,
        failureHistoryRecoveryMs: historyRecoveryMs,
        failureStage: "correlation",
        failureStrategy: strategy.name,
        totalMs: failureLatencyMs
      }, { ok: false, errorCode: "NATIVE_DOWNLOAD_NOT_CORRELATED", schedule: false });
      if (await recordStructuralStartFailure(workingDocument, "NATIVE_DOWNLOAD_NOT_CORRELATED", correlationError)) return;
      await handleFailure(workingDocument, "NATIVE_DOWNLOAD_NOT_CORRELATED", correlationError);
      void runNextQueueItem();
      return;
    }

    const downloadId = await chrome.downloads.download({...fallbackRequest, filename:stagingPath, conflictAction:'uniquify', saveAs:false});
    const extensionDownloadStartedAtMs = Date.now();
    await rememberRecentFallback(workingDocument, attemptId, downloadId);
    await documentStore.update(workingDocument.key, {
      downloadId, status:STATUS.DOWNLOADING, bindingMethod:'extension-download-id',
      requestMethod:fallbackRequest.method, requestOrigin:new URL(fallbackRequest.url).origin,
      downloadStartedAtMs: extensionDownloadStartedAtMs,
      perfClickToDownloadMs: metricDuration(attemptStartedAtMs, extensionDownloadStartedAtMs),
      perfFailureClickPhaseMs: clickPhaseMs,
      perfNativeWaitMs: nativeWaitMs,
      networkObserverUsed: strongNetworkObserved,
      networkObserverScore: strongNetworkObserved ? Number(pendingAfterClick?.networkHint?.score || 0) : null,
      networkObserverRequestType: strongNetworkObserved ? pendingAfterClick?.networkHint?.type || null : null,
      expectedDocumentKind: fallbackExpectedDocumentKind || pendingAfterClick?.networkHint?.declaredDocumentKind || workingDocument.expectedDocumentKind || null
    });
    await sessionStore.update(workingDocument.sessionId, {consecutiveStartFailures: 0});
    await clearPendingDownload();
    await clearOperationalAlarm(clickTimeoutAlarmName(workingDocument.sessionId));
    // Completion can precede the downloads.download() promise resolution.
    const [item] = await chrome.downloads.search({id:downloadId});
    if (item && ['complete','interrupted'].includes(item.state)) await handleDownloadChanged({id:downloadId,state:{current:item.state}});
  } catch (error) {
    const latestAfterError = await documentStore.get(workingDocument.key);
    if (latestAfterError?.attemptId !== attemptId) return;
    if (latestAfterError?.status !== STATUS.AWAITING_BROWSER) return;
    await clearPendingDownload();
    await clearOperationalAlarm(clickTimeoutAlarmName(workingDocument.sessionId));
    if (isRuntimeRebindError(error)) {
      const requeued = await documentStore.update(workingDocument.key, {
        status: STATUS.QUEUED,
        attempts: Math.max(0, (latestAfterError.attempts || 1) - 1),
        attemptId: null,
        stagingPath: null,
        runtimeDocumentId: null,
        errorCode: null,
        errorMessage: null
      });
      const binding = await rebindSingleDocument(requeued || {...workingDocument, runtimeDocumentId:null});
      if (binding.ok) {
        queueRequested = true;
        return;
      }
      if (binding.contextMismatch) {
        await pauseForSourceContextChanged(workingDocument.sessionId, binding);
        return;
      }
      await handleFailure(requeued || workingDocument, "DOWNLOAD_START_FAILED", `TARGETED_REBIND_FAILED: ${binding.error || String(error)}`);
      void runNextQueueItem();
      return;
    }
    const failureLatencyMs = metricDuration(attemptStartedAtMs);
    await applyAttemptPerformance(workingDocument.sessionId, {
      documentKey: workingDocument.key,
      queueLookupMs: metrics.queueLookupMs,
      failureLatencyMs,
      failureClickPhaseMs: clickPhaseMs,
      failureNativeWaitMs: nativeWaitMs,
      failureHistoryRecoveryMs: historyRecoveryMs,
      failureStage: "start",
      failureStrategy: strategy.name,
      totalMs: failureLatencyMs
    }, { ok: false, errorCode: "DOWNLOAD_START_FAILED", schedule: false });
    if (await recordStructuralStartFailure(workingDocument, "DOWNLOAD_START_FAILED", String(error))) return;
    await handleFailure(workingDocument, "DOWNLOAD_START_FAILED", String(error));
    void runNextQueueItem();
  }

}

async function clickDocumentAcrossFrames(document, attemptId, strategy = {}) {
  const frames = await uyapFrames(document.tabId);
  const frameIds = [...new Set([
    document.frameId,
    ...(frames || []).map((frame) => frame.frameId)
  ].filter(Number.isInteger))];
  const errors = [];

  for (const frameId of frameIds) {
    try {
      const response = await sendToFrame(document.tabId,frameId,
        {type:MESSAGE.CLICK_DOCUMENT,documentId:document.runtimeDocumentId || document.documentId,attemptId,kurumLocator:document?.sourceContext?.kurumLocator || null,saveMenuTimeoutMs:strategy.saveMenuTimeoutMs,captureWindowMs:strategy.captureWindowMs});
      if (response?.ok) {
        if (frameId !== document.frameId) await documentStore.update(document.key, { frameId });
        return {
          request: response.request || null,
          captureStatus: response.captureStatus || (response.request ? "fallback-request-captured" : "native-only")
        };
      }
      if (/SAVE_|AMBIGUOUS_|NOT_ALLOWED|NOT_SUPPORTED/.test(response?.error || '')) throw new Error(response.error);
      errors.push(response?.error || `Çerçeve ${frameId}: evrak bulunamadı`);
    } catch (error) {
      if (/SAVE_|AMBIGUOUS_|NOT_ALLOWED|NOT_SUPPORTED/.test(String(error))) throw error;
      errors.push(String(error));
    }
  }

  throw new Error(errors.at(-1) || "UYAP evrak çerçevesi bulunamadı. Sayfayı yenileyip tekrar deneyin.");
}

export async function attachBrowserDownload(downloadItem, pending, options = {}) {
  const current = await documentStore.get(pending.key);
  const resolvedSourceId = options.sourceDocumentId || current?.sourceDocumentId || null;
  const resolvedExtension = options.extension || current?.extension || null;
  const browserDownloadStartedAtMs = downloadStartAtMs(downloadItem) || Date.now();
  const attemptStartedAtMs = Number(current?.attemptStartedAtMs || pending.createdAt || 0);
  const updates = {
    downloadId: downloadItem.id,
    status: STATUS.DOWNLOADING,
    browserFilename: downloadItem.filename,
    downloadStartedAtMs: browserDownloadStartedAtMs,
    perfClickToDownloadMs: metricDuration(attemptStartedAtMs, browserDownloadStartedAtMs)
  };

  // Rebuild the target after the browser reveals the real UYAP source id and
  // payload extension. This prevents d-<hash>.bin names from surviving into the
  // final archive when a numeric source id is learned during download.
  if (current) {
    updates.targetPath = options.targetPath || buildTargetPath({
      ...current,
      sourceDocumentId: resolvedSourceId,
      extension: resolvedExtension
    });
  } else if (options.targetPath) {
    updates.targetPath = options.targetPath;
  }
  if (options.stagingPath) updates.stagingPath = options.stagingPath;
  if (options.extension) updates.extension = options.extension;
  if (options.extensionSource) updates.extensionSource = options.extensionSource;
  if (options.originalFilename) updates.originalFilename = options.originalFilename;
  if (options.warnings) updates.warnings = options.warnings;
  const expectedDocumentKind = options.expectedDocumentKind ||
    declaredUyapDocumentKindFromDownload(downloadItem) ||
    pending?.networkHint?.declaredDocumentKind ||
    current?.expectedDocumentKind || null;
  if (expectedDocumentKind) {
    updates.expectedDocumentKind = expectedDocumentKind;
    updates.documentKindEvidence = options.expectedDocumentKind
      ? "explicit"
      : (declaredUyapDocumentKindFromDownload(downloadItem) ? "download-item-url" : (pending?.networkHint?.declaredDocumentKind ? "network-observer" : "existing"));
  }
  if (options.sourceDocumentId) {
    updates.sourceDocumentId = options.sourceDocumentId;
    if (!current?.sourceDocumentIdOrigin) {
      const learned = ["native-uyap-discovered-source-id", "native-canonical-fresh-pending"].includes(options.bindingMethod);
      updates.sourceDocumentIdOrigin = options.sourceDocumentIdOrigin || (learned ? "download-filename" : (current?.sourceDocumentId ? current.sourceDocumentIdOrigin : "download-correlation"));
    }
  }
  if (options.bindingMethod) updates.bindingMethod = options.bindingMethod;
  if (options.correlationMode) updates.correlationMode = options.correlationMode;
  if (Number.isFinite(Number(options.correlationCandidateCount))) updates.correlationCandidateCount = Number(options.correlationCandidateCount);
  const initialBytes = Math.max(0, Number(downloadItem?.bytesReceived || downloadItem?.fileSize || 0));
  updates.watchdogLastBytesReceived = initialBytes;
  updates.watchdogLastProgressAt = new Date().toISOString();
  updates.watchdogLastObservedAt = new Date().toISOString();

  await documentStore.update(pending.key, updates);
  await sessionStore.update(pending.sessionId, {consecutiveStartFailures: 0});

  if (options.finalizePending) {
    await clearPendingDownload();
    await clearOperationalAlarm(clickTimeoutAlarmName(pending.sessionId));
  } else {
    await setPendingDownload({ ...pending, downloadId: downloadItem.id });
  }
}

export async function determineBrowserFilename(downloadItem, pending) {
  if (!pending) return null;
  const document = await documentStore.get(pending.key);
  const correlation = await correlateLeaseSingleCandidate(downloadItem, document, pending, {graceMs: DIRECT_DOWNLOAD_GENERIC_GRACE_MS});
  if (!correlation) return null;

  const detected = detectDownloadExtension(downloadItem);
  const stagingPath = document.stagingPath || pending.stagingPath;
  if (document.status === STATUS.AWAITING_BROWSER) {
    await attachBrowserDownload(downloadItem, pending, {
      stagingPath,
      sourceDocumentId: correlation.sourceDocumentId,
      bindingMethod: correlation.bindingMethod,
      correlationMode: correlation.correlationMode || 'exact',
      correlationCandidateCount: correlation.candidateCount || 1,
      originalFilename: downloadBasename(downloadItem),
      extension: detected.extension,
      extensionSource: detected.source
    });
  } else if (!document.sourceDocumentId && correlation.sourceDocumentId) {
    await documentStore.update(document.key, {
      sourceDocumentId: correlation.sourceDocumentId,
      sourceDocumentIdOrigin: ["native-uyap-discovered-source-id", "native-canonical-fresh-pending"].includes(correlation.bindingMethod) ? "download-filename" : "download-correlation",
      bindingMethod: correlation.bindingMethod,
      originalFilename: downloadBasename(downloadItem),
      extension: detected.extension,
      extensionSource: detected.source
    });
  }
  return stagingPath || null;
}

export async function handleDownloadCreated(downloadItem) {
  const pending = await getPendingDownload();
  if (!pending) {
    await suppressLateDuplicateUyapDownload(downloadItem);
    return;
  }
  const document = await documentStore.get(pending.key);
  if (document?.provisionalUnmatched && Number(document.downloadId) === Number(downloadItem?.id)) return;
  const correlation = correlateDirectUyapDownload(downloadItem, document, pending);
  if (!correlation || document.status !== STATUS.AWAITING_BROWSER) {
    if (document.status === STATUS.AWAITING_BROWSER) {
      const provisional = await registerProvisionalUnmatchedDownload(downloadItem, pending);
      if (provisional) return;
    }
    await suppressLateDuplicateUyapDownload(downloadItem);
    return;
  }

  const detected = detectDownloadExtension(downloadItem);
  await attachBrowserDownload(downloadItem, pending, {
    stagingPath: document.stagingPath || pending.stagingPath,
    sourceDocumentId: correlation.sourceDocumentId,
    bindingMethod: correlation.bindingMethod,
    originalFilename: downloadBasename(downloadItem),
    extension: detected.extension,
    extensionSource: detected.source
  });
}

export async function handleDownloadChanged(delta) {
  let document = await documentStore.byDownloadId(delta.id);
  if (!document) return;

  if (delta.state?.current === "complete") {
    if (document.status !== STATUS.DOWNLOADING || completingDownloads.has(delta.id)) return;
    completingDownloads.add(delta.id);
    try {
    await clearOperationalAlarm(watchdogAlarmName(document.sessionId));
    const [item] = await chrome.downloads.search({ id: delta.id });
    const pending = await getPendingDownload();

    if (document.provisionalUnmatched) {
      const finalCorrelation = await finalizeProvisionalCorrelation(document, item, pending);
      if (!finalCorrelation.ok) {
        if (pending?.downloadId === delta.id) await clearPendingDownload();
        await clearOperationalAlarm(clickTimeoutAlarmName(document.sessionId));
        await documentStore.update(document.key, {
          actualFilename: item?.filename || document.actualFilename || null,
          actualSize: Number.isFinite(Number(item?.fileSize)) ? Number(item.fileSize) : document.actualSize || null,
          completedAt: new Date().toISOString(),
          provisionalUnmatched: true,
          errorMessage: "UYAP indirmesi güvenli eşleştirilemedi; dosya _unmatched altında korundu."
        });
        await recordDiagnosticEvent(document.sessionId, "UNMATCHED_DOWNLOAD_PRESERVED", {
          documentId: document.documentId || null,
          downloadId: delta.id,
          provisionalPath: document.provisionalUnmatchedPath || null
        });
        await handleFailure(document, "NATIVE_DOWNLOAD_NOT_CORRELATED", "UYAP indirmesi güvenli eşleştirilemedi; dosya _unmatched altında korundu.");
        void runNextQueueItem();
        return;
      }
      document = finalCorrelation.document;
    }

    if (pending?.downloadId === delta.id) await clearPendingDownload();

    // Final browser metadata can be richer than the earlier onCreated event.
    // Learn the numeric UYAP id/real extension once more before native validation.
    const completedSourceId = nativeUyapFilenameSourceId({
      ...item,
      filename: document.originalFilename || item?.filename
    }) || document.sourceDocumentId || null;
    const completedDetection = detectDownloadExtension({
      ...item,
      filename: document.originalFilename || item?.filename
    });
    const completedExtension = completedDetection.extension === ".part"
      ? (document.extension || ".bin")
      : (completedDetection.extension || document.extension || ".bin");
    const completionUpdates = {};
    const completedDeclaredKind = declaredUyapDocumentKindFromDownload(item);
    if (completedDeclaredKind && completedDeclaredKind !== document.expectedDocumentKind) {
      completionUpdates.expectedDocumentKind = completedDeclaredKind;
      completionUpdates.documentKindEvidence = "download-item-url";
      document.expectedDocumentKind = completedDeclaredKind;
      document.documentKindEvidence = "download-item-url";
    }
    if (completedSourceId && completedSourceId !== document.sourceDocumentId) {
      completionUpdates.sourceDocumentId = completedSourceId;
      completionUpdates.sourceDocumentIdOrigin = document.sourceDocumentIdOrigin || "download-filename";
      document.sourceDocumentId = completedSourceId;
      document.sourceDocumentIdOrigin = completionUpdates.sourceDocumentIdOrigin;
    }
    if (completedExtension && completedExtension !== document.extension) {
      completionUpdates.extension = completedExtension;
      completionUpdates.extensionSource = completedDetection.source;
      document.extension = completedExtension;
    }
    const refreshedTargetPath = buildTargetPath({
      ...document,
      sourceDocumentId: completedSourceId || document.sourceDocumentId,
      extension: completedExtension
    });
    if (refreshedTargetPath !== document.targetPath) {
      completionUpdates.targetPath = refreshedTargetPath;
      document.targetPath = refreshedTargetPath;
    }
    if (Object.keys(completionUpdates).length) {
      await documentStore.update(document.key, completionUpdates);
    }
    const sessionForGuard = await sessionStore.get(document.sessionId);
    const browserGuard = browserCompletionPreflight(item);
    if (sessionForGuard?.agentCompletionGuardAvailable !== true && !browserGuard.ok) {
      const failureNowMs = Date.now();
      const downloadMs = metricDuration(document.downloadStartedAtMs || downloadStartAtMs(item), failureNowMs);
      const totalMs = metricDuration(document.attemptStartedAtMs || Date.parse(document.startedAt || ""), failureNowMs);
      try { if (item?.exists !== false) await chrome.downloads.removeFile(delta.id); } catch {}
      await documentStore.update(document.key, {
        actualFilename: item?.filename || null,
        actualSize: browserGuard.fileSize,
        browserFileSize: browserGuard.fileSize,
        browserTotalBytes: browserGuard.totalBytes,
        invalidRetryHint: "fresh_redownload",
        completedAt: new Date().toISOString()
      });
      await handleFailure(document, browserGuard.errorCode, browserGuard.detail);
      await applyAttemptPerformance(document.sessionId, {
        documentKey: document.key,
        queueLookupMs: document.perfQueueLookupMs,
        clickToDownloadMs: document.perfClickToDownloadMs,
        downloadMs,
        validationMs: 0,
        failureLatencyMs: totalMs,
        failureClickPhaseMs: document.perfFailureClickPhaseMs,
        failureNativeWaitMs: 0,
        failureStage: "browser_completion_guard",
        failureStrategy: document.attemptStrategy || document.retryStrategy || "primary",
        totalMs
      }, { ok:false, errorCode:browserGuard.errorCode });
      void runNextQueueItem();
      return;
    }
    const validationStartedAtMs = Date.now();
    const pipelineSession = await sessionStore.get(document.sessionId);
    const circuitProbeValidation = htmlCircuitState(pipelineSession) === "half_open";
    const allowPipelineAdvance = !circuitProbeValidation && pipelineCanAdvance(pipelineSession) && sessionValidationCount(document.sessionId) < MAX_PIPELINED_VALIDATIONS;
    await documentStore.update(document.key, {
      status: STATUS.COMPLETED,
      validationStartedAt: new Date(validationStartedAtMs).toISOString(),
      validationPipeline: allowPipelineAdvance ? "overlapped" : "serial"
    });
    validatingDocuments.set(document.key, {
      sessionId:document.sessionId,
      largeBatchNo:Number(document.largeBatchNo || 0),
      downloadId:delta.id,
      startedAtMs:validationStartedAtMs
    });
    if (allowPipelineAdvance) {
      const pipelineDelayMs = pacingDelayMs(pipelineSession?.pacing || {}, Math.random(), {partitionedArchiveMode:partitionedArchiveModeForSession(pipelineSession)});
      await sessionStore.update(document.sessionId, {
        nextDownloadAt:Date.now() + pipelineDelayMs,
        autoPipelineActive:true,
        autoPipelineLastAdvanceAt:new Date().toISOString(),
        autoPipelineValidationDepth:sessionValidationCount(document.sessionId)
      });
      void runNextQueueItem();
    }

    const agent = await askNativeAgent({
      action: "validate_download",
      attemptId: document.attemptId,
      sessionId: document.sessionId,
      filename: item?.filename || document.actualFilename,
      downloadsRoot: pipelineSession?.downloadsRoot || null,
      stagingPath: document.stagingPath,
      targetPath: document.targetPath,
      extension: document.extension,
      useSelectedArchiveRoot: pipelineSession?.portableResumeEnabled === true,
      allowCanonicalAdoption: String(document.bindingMethod || "").startsWith("native-") || directDownloadEvidence(document, item),
      directDownloadEvidence: directDownloadEvidence(document, item),
      browserDownloadId: item?.id,
      allowLeaseAdoption: String(document.bindingMethod || "").startsWith("native-uyap-single-candidate-lease"),
      allowUnmatchedAdoption: Boolean(document.provisionalUnmatchedPath) && String(document.bindingMethod || "").includes("provisional-unmatched"),
      provisionalUnmatchedPath: document.provisionalUnmatchedPath || null,
      browserState: item?.state || "complete",
      browserExists: item?.exists !== false,
      browserFileSize: Number.isFinite(Number(item?.fileSize)) ? Number(item.fileSize) : null,
      browserTotalBytes: Number.isFinite(Number(item?.totalBytes)) ? Number(item.totalBytes) : null,
      document: { ...document, attempts: document.attempts || 1 }
    });
    if (agent?.downloadsRoot) {
      await reconcileAfterDownloadsRootDiscovery(document.sessionId, agent.downloadsRoot);
    }
    const completionNowMs = Date.now();
    const validationMs = metricDuration(validationStartedAtMs, completionNowMs);
    const nativePerf = agent?.nativePerf || {};
    const nativeAgentMs = Number.isFinite(Number(nativePerf.agentTotalMs)) ? Number(nativePerf.agentTotalMs) : null;
    const nativeBridgeMs = nativeAgentMs !== null && validationMs !== null
      ? Math.max(0, validationMs - nativeAgentMs)
      : null;
    const nativeJournalMs = [nativePerf.journalLookupMs, nativePerf.journalPrepareMs, nativePerf.databaseCommitMs]
      .map(Number).filter(Number.isFinite).reduce((sum, value) => sum + value, 0);
    const downloadMs = metricDuration(document.downloadStartedAtMs || downloadStartAtMs(item), validationStartedAtMs);
    const totalMs = metricDuration(document.attemptStartedAtMs || Date.parse(document.startedAt || ""), completionNowMs);
    const performanceFields = {
      perfDownloadMs: downloadMs,
      perfValidationMs: validationMs,
      perfNativeAgentMs: nativeAgentMs,
      perfNativeBridgeMs: nativeBridgeMs,
      perfNativeDbOpenMs: Number(nativePerf.databaseOpenMs) || 0,
      perfNativeTypeDetectMs: Number(nativePerf.typeDetectMs) || 0,
      perfNativeContentCheckMs: Number(nativePerf.contentValidationMs) || 0,
      perfNativeHashMs: Number(nativePerf.hashMs) || 0,
      perfNativeFsyncMs: Number(nativePerf.fsyncMs) || 0,
      perfNativeJournalMs: nativeJournalMs,
      perfNativeMoveMs: Number(nativePerf.moveMs) || 0,
      perfNativeContentHashReconcileMs: Number(nativePerf.contentHashReconcileMs) || 0,
      perfTotalMs: totalMs,
      perfCompletedAt: new Date(completionNowMs).toISOString()
    };
    await documentStore.update(document.key, performanceFields);

    let attemptOk = false;
    let attemptErrorCode = null;
    const nativePathMissingResponse = !agent?.ok && /(?:FileNotFoundError|WinError\s*[23]|belirtilen yolu bulamıyor|cannot find (?:the )?(?:file|path)|no such file or directory)/i.test(String(agent?.error || ""));
    if (nativePathMissingResponse) {
      attemptErrorCode = "SOURCE_FILE_MISSING";
      const recoveryAt = Date.now() + 1_500;
      await documentStore.update(document.key, {
        status: STATUS.RETRY_WAIT,
        errorCode: "SOURCE_FILE_MISSING",
        errorMessage: "Tarayıcı indirme kaydı mevcut ancak geçici dosya yolu bulunamadı; taze indirme otomatik sıraya alındı.",
        retryAt: new Date(recoveryAt).toISOString(),
        retryQueuedAt: new Date().toISOString(),
        retryClass: "source_path_recovery",
        retryExhausted: false,
        retryStrategy: "fresh_rebind",
        retryStrategyHistory: appendRetryStrategyHistory(document, "fresh_rebind"),
        downloadId: null,
        attemptId: null,
        stagingPath: null,
        actualFilename: null,
        quarantinePath: null
      });
      await recordDiagnosticEvent(document.sessionId, "SOURCE_PATH_RECOVERY_QUEUED", {
        documentId: document.documentId || null,
        retryDelayMs: 1500,
        reason: "native_file_not_found_response"
      });
      const nativePathSession = await sessionStore.get(document.sessionId);
      if (nativePathSession?.status === SESSION_STATUS.RECOVERING && nativePathSession?.pauseReason === "NATIVE_AGENT_RECOVERY") {
        await sessionStore.update(document.sessionId, {
          status: SESSION_STATUS.RUNNING, pauseReason: null, recoveryStartedAt: null, resumeAfterRecovery: false,
          sessionGuardStatus: "HEALTHY", queueError: null
        });
      }
      await scheduleQueueWake(document.sessionId, recoveryAt);
    } else if (!agent.ok && !nativeBridgeTransportFailed(agent)) {
      const operationErrorCode = nativeOperationFailureCode(agent);
      const operationRetryCount = Math.max(0, Number(document.nativeOperationRetryCount || 0)) + 1;
      const retryScheduled = retryableNativeOperation({...agent, errorCode:operationErrorCode}, operationRetryCount, MAX_NATIVE_OPERATION_RETRIES);
      attemptErrorCode = operationErrorCode;
      preservedNativeRetryDownloads.add(delta.id);
      await documentStore.update(document.key, {
        status: STATUS.REVIEW_REQUIRED,
        warnings: [...new Set([...(document.warnings || []), operationErrorCode])],
        actualFilename: item?.filename ?? document.actualFilename ?? null,
        actualSize: item?.fileSize ?? document.actualSize ?? null,
        actualMime: item?.mime ?? null,
        completedAt: new Date().toISOString(),
        errorCode: operationErrorCode,
        errorMessage: agent.error || "Yerel ajan dosyayı işlerken geçici bir hata verdi.",
        nativeOperationRetryCount: operationRetryCount,
        nativeOperationErrorType: String(agent.errorType || agent.error || "").split(":", 1)[0].slice(0, 80) || null
      });
      await recordDiagnosticEvent(document.sessionId, retryScheduled ? "NATIVE_OPERATION_RETRY_SCHEDULED" : "NATIVE_OPERATION_RETRY_EXHAUSTED", {
        documentId: document.documentId || null,
        errorCode: operationErrorCode,
        errorType: String(agent.errorType || agent.error || "").split(":", 1)[0].slice(0, 80) || null,
        retryCount: operationRetryCount,
        maxRetries: MAX_NATIVE_OPERATION_RETRIES,
        bridgeResponseReceived: true
      });
      if (retryScheduled) {
        await sessionStore.update(document.sessionId, {
          status: SESSION_STATUS.RECOVERING,
          pauseReason: "NATIVE_OPERATION_RECOVERY",
          recoveryStartedAt: new Date().toISOString(),
          resumeAfterRecovery: true,
          sessionGuardStatus: "NATIVE_OPERATION_RETRY",
          queueError: `${operationErrorCode}: yerel doğrulama yeniden denenecek.`,
          nativeOperationDocumentKey: document.key,
          nativeOperationDownloadId: delta.id,
          nativeOperationRetryAt: new Date(Date.now() + 750).toISOString()
        });
        await scheduleOperationalAlarm(recoveryAlarmName(document.sessionId), {when: Date.now() + 750});
      } else {
        await sessionStore.update(document.sessionId, {
          status: SESSION_STATUS.RUNNING,
          pauseReason: null,
          recoveryStartedAt: null,
          resumeAfterRecovery: false,
          sessionGuardStatus: "HEALTHY",
          queueError: null,
          nativeOperationDocumentKey: null,
          nativeOperationDownloadId: null,
          nativeOperationRetryAt: null
        });
      }
    } else if (!agent.ok) {
      attemptErrorCode = "NATIVE_AGENT_UNAVAILABLE";
      // Preserve the already-downloaded file and recover the native bridge
      // automatically. This is a runtime/service failure, not a bad document.
      await documentStore.update(document.key, {
        status: STATUS.REVIEW_REQUIRED,
        warnings: [...new Set([...(document.warnings || []), "NATIVE_AGENT_UNAVAILABLE"])],
        actualFilename: item?.filename ?? document.actualFilename ?? null,
        actualSize: item?.fileSize ?? document.actualSize ?? null,
        actualMime: item?.mime ?? null,
        completedAt: new Date().toISOString(),
        errorCode: "NATIVE_AGENT_UNAVAILABLE",
        errorMessage: agent.error
      });
      await sessionStore.update(document.sessionId, {
        status: SESSION_STATUS.RECOVERING,
        pauseReason: "NATIVE_AGENT_RECOVERY",
        recoveryStartedAt: new Date().toISOString(),
        resumeAfterRecovery: true,
        sessionGuardStatus: "NATIVE_RECOVERY",
        queueError: agent.error || "Yerel ajan geçici olarak yanıt vermedi.",
        nativeTransientFailureActive: true,
        nativeTransientFailureAt: new Date().toISOString(),
        nativeTransientFailureToken: crypto.randomUUID()
      });
      await scheduleOperationalAlarm(recoveryAlarmName(document.sessionId), {when: Date.now() + 5_000});
    } else if (agent.valid) {
      attemptOk = true;
      invalidateArchiveRegistryCache();
      await resetSourcePathSystemicSignal(document.sessionId);
      if (agent.sourcePathRecovered) {
        await recordDiagnosticEvent(document.sessionId, "SOURCE_PATH_STAGING_RECOVERED", {
          documentId: document.documentId || null,
          mode: agent.sourcePathRecoveryMode || "known-downloads-root-staging",
          browserDownloadId: item?.id || null
        });
      }
      await documentStore.update(document.key, {
        status: STATUS.VERIFIED,
        warnings: Array.isArray(agent.warnings) ? agent.warnings : [],
        validationNotes: Array.isArray(agent.validationNotes) ? agent.validationNotes : [],
        contentDetectedExtension: agent.contentDetectedExtension || agent.extension || document.extension || null,
        contentDetectionMode: agent.contentDetectionMode || null,
        contentDetectionHint: agent.contentDetectionHint || null,
        extension: agent.extension || document.extension,
        sourceDocumentId: agent.sourceDocumentId || document.sourceDocumentId || null,
        sourceDocumentIdOrigin: document.sourceDocumentIdOrigin || ((agent.sourceDocumentId || document.sourceDocumentId) ? "download-filename" : null),
        targetPath: agent.targetPath || document.targetPath,
        actualFilename: agent.localPath,
        actualSize: agent.size,
        actualMime: item?.mime ?? null,
        sha256: agent.sha256,
        contentKind: agent.contentKind || null,
        nativeOperationRetryCount: 0,
        nativeOperationErrorType: null,
        htmlValidationMode: agent.htmlValidationMode || null,
        imageDetectedType: agent.imageDetectedType || null,
        imageValidationMode: agent.imageValidationMode || null,
        imageWidth: Number.isFinite(Number(agent.imageWidth)) ? Number(agent.imageWidth) : null,
        imageHeight: Number.isFinite(Number(agent.imageHeight)) ? Number(agent.imageHeight) : null,
        imageTrailingBytes: Number.isFinite(Number(agent.imageTrailingBytes)) ? Number(agent.imageTrailingBytes) : null,
        imageTrailingKind: agent.imageTrailingKind || null,
        pdfEofOffset: Number.isFinite(Number(agent.pdfEofOffset)) ? Number(agent.pdfEofOffset) : null,
        pdfTrailingBytes: Number.isFinite(Number(agent.pdfTrailingBytes)) ? Number(agent.pdfTrailingBytes) : null,
        pdfTrailingCoreHex: typeof agent.pdfTrailingCoreHex === "string" ? agent.pdfTrailingCoreHex.slice(0, 512) : null,
        pdfStartXref: Number.isFinite(Number(agent.pdfStartXref)) ? Number(agent.pdfStartXref) : null,
        pdfXrefKind: agent.pdfXrefKind || null,
        pdfTrailingKind: agent.pdfTrailingKind || null,
        completedAt: new Date().toISOString(),
        errorCode: null,
        errorMessage: null,
        attemptRecoveryClass: null,
        retryClass: null,
        retryAt: null,
        retryQueuedAt: null,
        provisionalUnmatched: false,
        provisionalUnmatchedPath: null,
        contentHashReconciled: Boolean(agent.contentHashReconciled),
        contentHashReuseMode: agent.contentHashReuseMode || null,
        localHashReconcilePending: false,
        localArchiveMatchState: document.localArchiveMatchState === "ambiguous_hash_pending"
          ? (agent.contentHashReconciled ? "content_hash_reconciled" : "content_hash_distinct")
          : (agent.contentHashReconciled ? "content_hash_reconciled" : (document.localArchiveMatchState || null)),
        localArchiveMatchMethod: document.localArchiveMatchState === "ambiguous_hash_pending"
          ? (agent.contentHashReconciled ? `sha256-${agent.contentHashReuseMode || "verified"}` : "sha256-distinct")
          : (agent.contentHashReconciled ? `sha256-${agent.contentHashReuseMode || "verified"}` : (document.localArchiveMatchMethod || null)),
        forceCanonicalRecovery: false,
        forceCanonicalRecoveryReason: null,
        forceCanonicalRecoveryPreviousMatchMethod: null,
        canonicalRecoveryCompletedAt: document.forceCanonicalRecovery === true ? new Date().toISOString() : (document.canonicalRecoveryCompletedAt || null)
      });
      const wasDeferredRetry = Boolean(document.retryAt) || Number(document.attempts || 0) > 1;
      const latestSession = await sessionStore.get(document.sessionId);
      const recoveryHealth = recoveryBudgetAfterVerified({
        recoveryCount: latestSession?.recoveryCount,
        healthyVerifiedSinceRecovery: latestSession?.healthyVerifiedSinceRecovery
      });
      const verifiedArchiveBytes = Math.max(0, Number(latestSession?.verifiedArchiveBytes || 0)) + Math.max(0, Number(agent.size || 0));
      const partitionedArchiveMode = partitionedArchiveModeForSession({
        ...latestSession,
        verifiedArchiveBytes
      });
      const pacingProfileChanged = latestSession?.partitionedArchiveMode !== true && partitionedArchiveMode;
      const circuitHalfOpen = htmlCircuitState(latestSession) === "half_open";
      const circuitRecoverySuccesses = circuitHalfOpen
        ? Math.max(0, Number(latestSession?.htmlCircuitRecoverySuccesses || 0)) + 1
        : 0;
      const circuitClosed = circuitHalfOpen && circuitRecoverySuccesses >= HTML_CIRCUIT_STABLE_SUCCESSES;
      await sessionStore.update(document.sessionId, {
        downloadsRoot: agent.downloadsRoot,
        verifiedArchiveBytes,
        partitionedArchiveMode,
        downloadPacingProfile: partitionedArchiveMode ? "partitioned-controlled" : "standard-fast",
        consecutiveHtmlFailures: 0,
        lastHtmlFailureDocumentKey: null,
        consecutiveStartFailures: 0,
        recoveryCount: recoveryHealth.recoveryCount,
        healthyVerifiedSinceRecovery: recoveryHealth.healthyVerifiedSinceRecovery,
        recoveryBudgetResetAt: recoveryHealth.reset ? new Date().toISOString() : latestSession?.recoveryBudgetResetAt || null,
        contentCooldownCount: circuitClosed || !circuitHalfOpen ? 0 : Number(latestSession?.contentCooldownCount || 0),
        contentCooldownUntil: null,
        htmlCircuitState: circuitClosed ? "closed" : (circuitHalfOpen ? "half_open" : htmlCircuitState(latestSession)),
        htmlCircuitHalfOpen: circuitClosed ? false : circuitHalfOpen,
        htmlCircuitRecoverySuccesses: circuitClosed ? 0 : circuitRecoverySuccesses,
        htmlCircuitOpenCount: circuitClosed ? 0 : Number(latestSession?.htmlCircuitOpenCount || 0),
        htmlCircuitProbeAttempt: circuitClosed ? 0 : Number(latestSession?.htmlCircuitProbeAttempt || 0),
        htmlCircuitNextProbeAt: circuitClosed ? null : latestSession?.htmlCircuitNextProbeAt || null,
        htmlCircuitClosedAt: circuitClosed ? new Date().toISOString() : latestSession?.htmlCircuitClosedAt || null,
        sessionGuardStatus: circuitClosed
          ? "HEALTHY"
          : (circuitHalfOpen ? "CONTENT_CIRCUIT_HALF_OPEN" : (latestSession?.sessionGuardStatus === "CONTENT_DEGRADED_SOFT" ? "HEALTHY" : latestSession?.sessionGuardStatus)),
        queueRuntimeRecoveryCount: 0,
        startPathRecoveryCount: 0,
        primarySuccessesSinceRetry: wasDeferredRetry
          ? Number(latestSession?.primarySuccessesSinceRetry || 0)
          : Number(latestSession?.primarySuccessesSinceRetry || 0) + 1,
        localArchiveAmbiguousCount: document.localArchiveMatchState === "ambiguous_hash_pending"
          ? Math.max(0, Number(latestSession?.localArchiveAmbiguousCount || 0) - 1)
          : Number(latestSession?.localArchiveAmbiguousCount || 0)
      });
      if (circuitClosed) {
        await recordDiagnosticEvent(document.sessionId, "HTML_CIRCUIT_CLOSED", {
          stableVerified: HTML_CIRCUIT_STABLE_SUCCESSES,
          previousOpenCount: Number(latestSession?.htmlCircuitOpenCount || 0)
        });
      }
      if (pacingProfileChanged) {
        await recordDiagnosticEvent(document.sessionId, "DOWNLOAD_PACING_PROFILE_CHANGED", {
          profile: "partitioned-controlled",
          reason: Number(latestSession?.total || 0) > ARCHIVE_PART_MAX_DOCUMENTS ? "document-count" : "verified-bytes",
          verifiedArchiveBytes
        });
      }
      if (agent.contentHashReconciled) {
        await recordDiagnosticEvent(document.sessionId, "LOCAL_ARCHIVE_CONTENT_HASH_RECONCILED", {
          documentId: document.documentId || null,
          reuseMode: agent.contentHashReuseMode || "verified",
          targetPath: agent.targetPath || document.targetPath || null
        });
      }
      await chrome.storage.local.set({ [LAST_DOWNLOADS_ROOT_KEY]: agent.downloadsRoot });
      if (latestSession?.nativeTransientFailureActive === true || latestSession?.pacing?.recentOutcomes?.some?.((outcome) => outcome?.errorCode === TRANSIENT_NATIVE_ERROR_CODE)) {
        await cleanupResolvedNativeTransientState(document.sessionId, {reason:"native-validation-success", forceDocuments:true});
      }
    } else {
      // UYAP sometimes starts a valid PDF response and then appends the literal
      // server message "Invalid request" after %%EOF. This is not a damaged
      // source PDF: it is the same content-endpoint failure as Hata.html and
      // must enter the bounded endpoint recovery flow instead of manual PDF
      // review. The quarantined bytes stay preserved for audit.
      const uyapInvalidRequestTail = isUyapInvalidRequestPdfTail(agent);
      const effectiveErrorCode = uyapInvalidRequestTail ? "HTML_RESPONSE" : (agent.errorCode || "INVALID_CONTENT");
      const effectiveErrorDetail = uyapInvalidRequestTail
        ? "UYAP belge servisi PDF yanıtının sonuna 'Invalid request' hata iletisi ekledi."
        : (agent.detail || null);
      attemptErrorCode = effectiveErrorCode;
      const browserFileSize = Number.isFinite(Number(agent.browserFileSize)) ? Number(agent.browserFileSize) : (item?.fileSize ?? null);
      const browserTotalBytes = Number.isFinite(Number(agent.browserTotalBytes)) ? Number(agent.browserTotalBytes) : (item?.totalBytes ?? null);
      const observedInvalidSize = Number.isFinite(Number(agent.observedSize)) ? Number(agent.observedSize) : null;
      const sourcePathChecks = agent.sourcePathRecoveryChecks || {};
      await documentStore.update(document.key, {
        actualFilename: agent.quarantinePath || item?.filename || null,
        actualSize: observedInvalidSize ?? browserFileSize,
        actualMime: item?.mime ?? null,
        browserFileSize,
        browserTotalBytes,
        observedInvalidSize,
        sourcePathRecoveryMode: agent.sourcePathRecoveryMode || null,
        sourcePathRecoveryKnownRootExists: sourcePathChecks["known-downloads-root-staging"]?.exists === true,
        sourcePathRecoveryBrowserRootExists: sourcePathChecks["browser-reported-root-staging"]?.exists === true,
        sourcePathRecoverySelectedRootExists: sourcePathChecks["selected-archive-root-staging"]?.exists === true,
        quarantinePath: agent.quarantinePath || null,
        invalidRetryHint: agent.retryHint || null,
        invalidSha256: agent.invalidSha256 || null,
        contentDetectedExtension: agent.contentDetectedExtension || agent.extension || null,
        contentDetectionMode: agent.contentDetectionMode || null,
        contentDetectionHint: agent.contentDetectionHint || null,
        imageDetectedType: agent.imageDetectedType || null,
        imageValidationMode: agent.imageValidationMode || null,
        imageFallbackFromMode: agent.imageFallbackFromMode || null,
        imageWicFallbackMode: agent.imageWicFallbackMode || null,
        imageGdiplusFallbackMode: agent.imageGdiplusFallbackMode || null,
        imageTrailingBytes: Number.isFinite(Number(agent.imageTrailingBytes)) ? Number(agent.imageTrailingBytes) : null,
        imageTrailingKind: agent.imageTrailingKind || null,
        pdfEofOffset: Number.isFinite(Number(agent.pdfEofOffset)) ? Number(agent.pdfEofOffset) : null,
        pdfTrailingBytes: Number.isFinite(Number(agent.pdfTrailingBytes)) ? Number(agent.pdfTrailingBytes) : null,
        pdfTrailingCoreHex: typeof agent.pdfTrailingCoreHex === "string" ? agent.pdfTrailingCoreHex.slice(0, 512) : null,
        pdfStartXref: Number.isFinite(Number(agent.pdfStartXref)) ? Number(agent.pdfStartXref) : null,
        pdfXrefKind: agent.pdfXrefKind || null,
        pdfValidatorMode: Object.prototype.hasOwnProperty.call(agent, 'pdfTailWindow') ? 'v2' : (agent.errorCode === 'TRUNCATED_PDF' ? 'legacy' : null),
        completedAt: new Date().toISOString()
      });
      if (effectiveErrorCode === "SOURCE_FILE_MISSING") {
        await recordDiagnosticEvent(document.sessionId, "SOURCE_PATH_RECOVERY_PROBE_MISSED", {
          documentId: document.documentId || null,
          knownRootExists: sourcePathChecks["known-downloads-root-staging"]?.exists === true,
          browserRootExists: sourcePathChecks["browser-reported-root-staging"]?.exists === true,
          selectedRootExists: sourcePathChecks["selected-archive-root-staging"]?.exists === true
        });
      }
      if (effectiveErrorCode === "HTML_RESPONSE") {
        const sessionBeforeProbe = await sessionStore.get(document.sessionId);
        const htmlOrdinal = Math.max(1, Number(sessionBeforeProbe?.consecutiveHtmlFailures || 0) + 1);
        const lastProbeMs = Date.parse(sessionBeforeProbe?.lastHtmlSessionProbeAt || "");
        const probeStale = !Number.isFinite(lastProbeMs) || (Date.now() - lastProbeMs) >= 15_000;
        // RC15.8 circuit breaker: do not run a full heartbeat after every tiny
        // evrak.html/Hata.html response. Probe immediately on the first signal,
        // again on the third distinct HTML result, then at most once/15s.
        const shouldProbe = sessionBeforeProbe?.status === SESSION_STATUS.RUNNING &&
          (htmlOrdinal === 1 || htmlOrdinal === 3 || probeStale);
        if (shouldProbe) {
          await sessionStore.update(document.sessionId, {
            lastHtmlSessionProbeAt:new Date().toISOString(),
            lastHtmlCircuitProbeOrdinal:htmlOrdinal
          });
          await performSessionHeartbeat(sessionBeforeProbe);
        }
        const sessionAfterProbe = shouldProbe ? await sessionStore.get(document.sessionId) : sessionBeforeProbe;
        const sessionInterrupted =
          (sessionAfterProbe?.status === SESSION_STATUS.PAUSED && sessionAfterProbe.pauseReason === "LOGIN_REQUIRED") ||
          (sessionAfterProbe?.status === SESSION_STATUS.RECOVERING && sessionAfterProbe.pauseReason === "SOURCE_PAGE_NOT_READY");
        if (sessionInterrupted) {
          await requeueDocumentAfterSessionLoss(
            document,
            sessionAfterProbe.pauseReason === "LOGIN_REQUIRED" ? "HTML_RESPONSE_LOGIN_REQUIRED" : "HTML_RESPONSE_SOURCE_UNAVAILABLE"
          );
          await sessionStore.update(document.sessionId, {
            htmlSessionInterruptions: Number(sessionAfterProbe.htmlSessionInterruptions || 0) + 1,
            lastHtmlSessionInterruptionAt: new Date().toISOString()
          });
          return;
        }
      }
      const sourcePathCircuit = effectiveErrorCode === "SOURCE_FILE_MISSING"
        ? await maybePauseForSystemicSourcePathMissing(document, item, agent)
        : {tripped:false};
      if (!sourcePathCircuit.tripped) {
        await handleFailure(document, effectiveErrorCode, effectiveErrorDetail);
      }
      if (effectiveErrorCode === "HTML_RESPONSE") await handleHtmlFailure(document.sessionId, document.key);
    }
    await applyAttemptPerformance(document.sessionId, {
      documentKey: document.key,
      queueLookupMs: document.perfQueueLookupMs,
      clickToDownloadMs: document.perfClickToDownloadMs,
      downloadMs,
      validationMs,
      nativeAgentMs,
      nativeBridgeMs,
      nativeDbOpenMs: Number(nativePerf.databaseOpenMs) || 0,
      nativeTypeDetectMs: Number(nativePerf.typeDetectMs) || 0,
      nativeContentCheckMs: Number(nativePerf.contentValidationMs) || 0,
      nativeHashMs: Number(nativePerf.hashMs) || 0,
      nativeFsyncMs: Number(nativePerf.fsyncMs) || 0,
      nativeJournalMs,
      nativeMoveMs: Number(nativePerf.moveMs) || 0,
      failureLatencyMs: attemptOk ? null : totalMs,
      failureClickPhaseMs: attemptOk ? null : document.perfFailureClickPhaseMs,
      failureNativeWaitMs: attemptOk ? null : document.perfNativeWaitMs,
      failureStage: attemptOk ? null : "validation",
      failureStrategy: attemptOk ? null : (document.attemptStrategy || document.retryStrategy || "primary"),
      totalMs
    }, { ok: attemptOk, errorCode: attemptErrorCode, nativeHealthy: attemptOk && agent?.ok === true });
    void runNextQueueItem();
    } finally {
      validatingDocuments.delete(document.key);
      // Chrome's download history must never expose internal *.part staging files
      // after an archive attempt completes. The native agent has already moved or
      // quarantined the bytes at this point; erasing only removes the browser UI
      // entry and does not delete the preserved archive/rejected file.
      if (preservedNativeRetryDownloads.has(delta.id)) preservedNativeRetryDownloads.delete(delta.id);
      else await forgetManagedDownload(delta.id);
      completingDownloads.delete(delta.id);
      void runNextQueueItem();
    }
    return;
  }

  if (delta.state?.current === "interrupted" && document.status === STATUS.DOWNLOADING) {
    const [item] = await chrome.downloads.search({ id: delta.id });
    const errorCode = item?.error || "DOWNLOAD_INTERRUPTED";
    const nowMs = Date.now();
    const downloadMs = metricDuration(document.downloadStartedAtMs || downloadStartAtMs(item), nowMs);
    const totalMs = metricDuration(document.attemptStartedAtMs || Date.parse(document.startedAt || ""), nowMs);
    await documentStore.update(document.key, { perfDownloadMs: downloadMs, perfTotalMs: totalMs });
    await applyAttemptPerformance(document.sessionId, {
      documentKey: document.key,
      queueLookupMs: document.perfQueueLookupMs,
      clickToDownloadMs: document.perfClickToDownloadMs,
      downloadMs,
      failureLatencyMs: totalMs,
      failureClickPhaseMs: document.perfFailureClickPhaseMs,
      failureNativeWaitMs: document.perfNativeWaitMs,
      failureStage: "download",
      failureStrategy: document.attemptStrategy || document.retryStrategy || "primary",
      totalMs
    }, { ok: false, errorCode: "DOWNLOAD_INTERRUPTED" });
    await handleFailure(document, errorCode);
    // Interrupted staging files are not useful as evidence and must not remain
    // in Chrome's download bubble/history. The retry gets a fresh attempt path.
    await forgetManagedDownload(delta.id, { removeFile: true });
    void runNextQueueItem();
  }
}

const HTML_REVIEW_ERROR_CODES = new Set(["HTML_RESPONSE", "HTML_RESPONSE_RECEIVED"]);

function hasPreservedHtmlEvidence(document = {}) {
  const extensions = [document.extension, document.contentDetectedExtension]
    .map((value) => String(value || "").trim().toLowerCase().replace(/^\./, ""));
  const paths = [document.quarantinePath, document.actualFilename]
    .map((value) => String(value || "").trim());
  const previousCodes = [
    document.errorCode,
    document.completionRecoveryPreviousErrorCode,
    document.lastRuntimeResetPreviousErrorCode,
    document.userExcludedErrorCode
  ].map((value) => String(value || ""));
  return Boolean(
    extensions.includes("html") || extensions.includes("htm") ||
    String(document.actualMime || "").toLowerCase().includes("text/html") ||
    paths.some((value) => /\.(?:html?|jspx?|aspx?|php)$/i.test(value)) ||
    previousCodes.some((value) => HTML_REVIEW_ERROR_CODES.has(value))
  );
}

function isTerminalHtmlReviewDocument(document = {}) {
  // A started recovery attempt is not a failed recovery outcome. In particular
  // COMPLETED means native validation is still pending, not final success.
  if ([STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING, STATUS.COMPLETED, STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status)) return false;
  if (document.retryClass === "deferred_html" && document.retryExhausted !== true && !document.htmlRecoveryFinal) return false;
  if (!hasPreservedHtmlEvidence(document)) return false;
  const code = String(document.errorCode || "");
  const previous = String(document.completionRecoveryPreviousErrorCode || document.lastRuntimeResetPreviousErrorCode || "");
  return Boolean(
    document.htmlRecoveryFinal === true ||
    Number(document.htmlRecoveryAttempts || 0) >= 1 ||
    code === "ARCHIVE_INTEGRITY_FAILED" ||
    HTML_REVIEW_ERROR_CODES.has(previous) ||
    (HTML_REVIEW_ERROR_CODES.has(code) && document.retryExhausted === true)
  );
}

async function normalizeTerminalHtmlReviewDocument(document) {
  if (!document || !isTerminalHtmlReviewDocument(document)) return document;
  if ([STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING].includes(document.status)) return document;
  const alreadyNormalized =
    document.status === STATUS.REVIEW_REQUIRED &&
    document.errorCode === "HTML_RESPONSE" &&
    document.retryAt == null &&
    document.retryClass == null &&
    document.retryExhausted === true;
  if (alreadyNormalized) return document;
  const updated = await documentStore.update(document.key, {
    status: STATUS.REVIEW_REQUIRED,
    errorCode: "HTML_RESPONSE",
    errorMessage: document.errorMessage || "UYAP bu evrak için tekrar HTML yanıtı verdi. Kullanıcı kontrolü gerekiyor.",
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryExhausted: true,
    retryStrategy: null,
    manualRetryPriority: false,
    retryPriority: 1,
    attemptRecoveryClass: null,
    htmlRecoveryFinal: true
  });
  return updated || document;
}

async function migrateTerminalHtmlReviewState(sessionId) {
  const documents = await documentStore.bySession(sessionId);
  let changed = 0;
  for (const document of documents) {
    if (!isTerminalHtmlReviewDocument(document)) continue;
    if ([STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING, STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status)) continue;
    const normalized = await normalizeTerminalHtmlReviewDocument(document);
    if (normalized && (normalized.status !== document.status || normalized.errorCode !== document.errorCode || document.retryAt != null || document.retryClass != null)) {
      changed += 1;
    }
  }
  if (changed) {
    await sessionStore.update(sessionId, {
      htmlReviewStateMigrationAt: new Date().toISOString(),
      htmlReviewStateMigrationCount: changed
    });
    await recordDiagnosticEvent(sessionId, "HTML_REVIEW_STATE_REPAIRED", {documents: changed});
  }
  return changed;
}

function hasCompleteBrowserByteProof(item, agent = {}) {
  const state = String(agent.browserState || item?.state || "");
  const fileSize = Number.isFinite(Number(agent.browserFileSize))
    ? Number(agent.browserFileSize)
    : (Number.isFinite(Number(item?.fileSize)) ? Number(item.fileSize) : null);
  const totalBytes = Number.isFinite(Number(agent.browserTotalBytes))
    ? Number(agent.browserTotalBytes)
    : (Number.isFinite(Number(item?.totalBytes)) ? Number(item.totalBytes) : null);
  return state === "complete" &&
    Number.isFinite(fileSize) && fileSize > 0 &&
    Number.isFinite(totalBytes) && totalBytes > 0 &&
    fileSize === totalBytes;
}

async function maybePauseForSystemicSourcePathMissing(document, item, agent = {}) {
  if (!hasCompleteBrowserByteProof(item, agent)) return {tripped:false, streak:0, proof:false};
  const session = await sessionStore.get(document.sessionId);
  if (!session || session.status !== SESSION_STATUS.RUNNING) return {tripped:false, streak:0, proof:true};

  const nowMs = Date.now();
  const previousAtMs = Date.parse(String(session.sourcePathMissingLastAt || ""));
  const withinWindow = Number.isFinite(previousAtMs) && (nowMs - previousAtMs) <= SOURCE_PATH_SYSTEMIC_WINDOW_MS;
  const sameDocument = session.sourcePathMissingLastDocumentKey === document.key;
  const previousStreak = withinWindow ? Math.max(0, Number(session.sourcePathMissingSystemicStreak || 0)) : 0;
  const streak = sameDocument ? previousStreak : previousStreak + 1;
  const seen = Array.isArray(session.sourcePathMissingRecentDocuments)
    ? session.sourcePathMissingRecentDocuments.filter((value) => value && value !== document.key).slice(-4)
    : [];
  seen.push(document.key);

  await sessionStore.update(document.sessionId, {
    sourcePathMissingSystemicStreak: streak,
    sourcePathMissingLastDocumentKey: document.key,
    sourcePathMissingLastAt: new Date(nowMs).toISOString(),
    sourcePathMissingRecentDocuments: seen.slice(-5)
  });
  await recordDiagnosticEvent(document.sessionId, "SOURCE_PATH_SYSTEMIC_SIGNAL", {
    documentId: document.documentId || null,
    errorCode: "SOURCE_FILE_MISSING",
    streak,
    threshold: SOURCE_PATH_SYSTEMIC_THRESHOLD,
    browserBytesComplete: true
  });

  if (streak < SOURCE_PATH_SYSTEMIC_THRESHOLD) return {tripped:false, streak, proof:true};

  await documentStore.update(document.key, {
    status: STATUS.RETRY_WAIT,
    errorCode: "SOURCE_FILE_MISSING",
    errorMessage: "Tarayıcı indirmeyi tamamladı ancak yerel ajan dosyayı göremedi. Aynı sistemik hata farklı evraklarda tekrarlandığı için iş otomatik olarak duraklatıldı.",
    retryAt: null,
    retryQueuedAt: null,
    retryClass: "systemic_source_path_pause",
    retryExhausted: false
  });
  await clearSessionRuntimeAlarms(document.sessionId);
  await sessionStore.update(document.sessionId, {
    status: SESSION_STATUS.PAUSED,
    pauseReason: SOURCE_PATH_SYSTEMIC_PAUSE_REASON,
    pausedAt: new Date(nowMs).toISOString(),
    sessionGuardStatus: "SOURCE_PATH_SYSTEMIC",
    queueError: "Chrome indirmeyi tamamlıyor ancak Native Agent dosya yolunu göremiyor. Tekrar indirme zinciri durduruldu; yol görünürlüğü düzeltilip işe devam edilmesi gerekiyor.",
    sourcePathMissingCircuitOpenedAt: new Date(nowMs).toISOString(),
    sourcePathMissingCircuitTrips: Math.max(0, Number(session.sourcePathMissingCircuitTrips || 0)) + 1
  });
  await recordDiagnosticEvent(document.sessionId, "SOURCE_PATH_SYSTEMIC_PAUSED", {
    documentId: document.documentId || null,
    errorCode: "SOURCE_FILE_MISSING",
    streak,
    threshold: SOURCE_PATH_SYSTEMIC_THRESHOLD,
    browserBytesComplete: true
  });
  return {tripped:true, streak, proof:true};
}

async function resetSourcePathSystemicSignal(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session || !Number(session.sourcePathMissingSystemicStreak || 0)) return;
  await sessionStore.update(sessionId, {
    sourcePathMissingSystemicStreak: 0,
    sourcePathMissingLastDocumentKey: null,
    sourcePathMissingLastAt: null,
    sourcePathMissingRecentDocuments: []
  });
}

async function handleFailure(document, errorCode, errorMessage = null) {
  const latest = await documentStore.get(document.key) || document;
  const attempts = latest.attempts ?? 0;
  const htmlDecision = errorCode === "HTML_RESPONSE" ? htmlRetryDecision(latest) : null;
  const retryDelay = retryDelayForError(errorCode, attempts, {
    defaultDelays: RETRY_DELAYS_MS,
    htmlDelays: HTML_RETRY_DELAYS_MS
  });
  const nextStrategy = nextRetryStrategy(latest, errorCode);
  const strategyRequired = retryStrategySupported(errorCode);
  const isolatedContentFailure = isIsolatedContentError(errorCode);

  // Deterministic per-document format/signature failures do not improve by
  // downloading the same bytes three more times. Quarantine once, surface the
  // record for review, and immediately let the rest of the queue continue.
  if (isolatedContentFailure || htmlDecision?.retry === false || retryDelay === null || (strategyRequired && !nextStrategy)) {
    await documentStore.update(document.key, {
      status: STATUS.REVIEW_REQUIRED,
      errorCode,
      errorMessage,
      retryAt: null,
      retryQueuedAt: null,
      retryClass: null,
      retryExhausted: true,
      retryStrategy: null,
      attemptRecoveryClass: null,
      htmlRecoveryFinal: errorCode === "HTML_RESPONSE" ? true : latest.htmlRecoveryFinal || false
    });
    await recordDiagnosticEvent(document.sessionId, "DOCUMENT_FAILURE", {
      documentId: latest.documentId,
      errorCode,
      disposition: errorCode === "HTML_RESPONSE" ? "html_recovery_exhausted" : "review_required",
      attempts,
      strategy: latest.attemptStrategy || latest.retryStrategy || "primary"
    });
    return;
  }

  const nowMs = Date.now();
  const retryAt = new Date(nowMs + retryDelay).toISOString();
  await documentStore.update(document.key, {
    status: STATUS.RETRY_WAIT,
    errorCode,
    errorMessage,
    retryAt,
    retryQueuedAt: new Date(nowMs).toISOString(),
    retryClass: errorCode === "HTML_RESPONSE" ? "deferred_html" : "standard",
    retryExhausted: false,
    attemptRecoveryClass: null,
    retryStrategy: nextStrategy || latest.retryStrategy || "primary",
    retryStrategyHistory: nextStrategy
      ? appendRetryStrategyHistory(latest, nextStrategy)
      : (latest.retryStrategyHistory || [])
  });
  await recordDiagnosticEvent(document.sessionId, "DOCUMENT_FAILURE", {
    documentId: latest.documentId,
    errorCode,
    disposition: errorCode === "HTML_RESPONSE" ? "deferred_recovery" : "retry_wait",
    attempts,
    retryDelayMs: retryDelay,
    strategy: nextStrategy || latest.retryStrategy || "primary"
  });
  await scheduleQueueWake(document.sessionId, Date.parse(retryAt));
}

async function migrateLegacyHtmlRetry(document) {
  if (!document || document.status !== STATUS.RETRY_WAIT || document.errorCode !== "HTML_RESPONSE") return document;
  if (document.retryClass === "deferred_html") return document;
  const updated = await documentStore.update(document.key, {
    retryClass: "deferred_html"
  });
  return updated || document;
}

export async function handleHtmlFailure(sessionId, documentKey = null) {
  const session = await sessionStore.get(sessionId);
  if (!session) return;
  const sameDocumentRetry = Boolean(documentKey) && session?.lastHtmlFailureDocumentKey === documentKey;
  const failures = sameDocumentRetry
    ? Number(session?.consecutiveHtmlFailures || 0)
    : (Number(session?.consecutiveHtmlFailures || 0) + 1);
  await sessionStore.update(sessionId, {
    consecutiveHtmlFailures: failures,
    lastHtmlFailureDocumentKey: documentKey || session?.lastHtmlFailureDocumentKey || null
  });
  if (session?.status !== SESSION_STATUS.RUNNING) return;

  const projected = nextPacingState(session?.pacing || {}, {
    ok: false,
    errorCode: "HTML_RESPONSE",
    outcomeKey: documentKey || `html-${Date.now()}`
  });

  // RC15.9.3: HTML detection remains strict, but handling is adaptive.
  // Small/isolated HTML results are quarantined per document and fresh primary
  // work continues. Only a strong cross-document burst earns a short global
  // cooldown. Pure HTML pressure never changes the session to RECOVERING.
  if (!htmlCircuitShouldOpen(session, projected)) return;

  const nowMs = Date.now();
  const previousState = htmlCircuitState(session);
  const isReopen = previousState === "half_open";
  const episode = isReopen
    ? Math.max(1, Number(session.htmlCircuitEpisode || 1))
    : Math.max(0, Number(session.htmlCircuitEpisode || 0)) + 1;
  const probeAttempt = isReopen ? Math.max(1, Number(session.htmlCircuitProbeAttempt || 1)) : 0;
  const openCount = isReopen
    ? Math.max(1, Number(session.htmlCircuitOpenCount || 1))
    : 1;
  const cooldownStep = isReopen ? Math.min(HTML_CIRCUIT_COOLDOWNS_MS.length, probeAttempt + 1) : 1;
  const cooldownMs = htmlCircuitCooldownMs(cooldownStep);
  const cooldownUntil = nowMs + cooldownMs;
  const freshPrimary = await documentStore.nextQueued(sessionId);
  const resetPacing = {
    ...(session.pacing || {}),
    mode: "cautious",
    backoffReason: "html_burst_soft",
    successStreak: 0,
    failureStreak: 0,
    recentOutcomes: [],
    windowAttempts: 0,
    windowFailures: 0,
    windowHtmlFailures: 0,
    windowIsolatedContentFailures: 0,
    windowSystemicFailures: 0,
    windowErrorRate: 0,
    windowHtmlRate: 0,
    windowSystemicErrorRate: 0,
    consecutiveFailures: 0,
    consecutiveHtml: 0,
    delayMs: cooldownMs,
    updatedAt: new Date(nowMs).toISOString()
  };

  // Three controlled probes all returning endpoint error is no longer a short
  // transient. Stop issuing requests completely and ask for one explicit UYAP
  // page refresh. Verified/local records and the remaining queue are retained;
  // Resume runs the normal binding preflight before the next request.
  if (isReopen && probeAttempt >= HTML_CIRCUIT_MAX_SOFT_PROBES) {
    await clearSessionRuntimeAlarms(sessionId);
    await sessionStore.update(sessionId, {
      status: SESSION_STATUS.PAUSED,
      pauseReason: "CONTENT_ENDPOINT_REFRESH_REQUIRED",
      pausedAt: new Date(nowMs).toISOString(),
      resumeAfterRecovery: false,
      recoveryStartedAt: null,
      sessionGuardStatus: "CONTENT_ENDPOINT_REFRESH_REQUIRED",
      htmlCircuitState: "open",
      htmlCircuitHalfOpen: false,
      htmlCircuitRecoverySuccesses: 0,
      htmlCircuitLastReason: "probe_limit_reached",
      htmlCircuitNextProbeAt: null,
      contentCooldownUntil: null,
      nextDownloadAt: 0,
      pacing: resetPacing
    });
    await recordDiagnosticEvent(sessionId, "CONTENT_ENDPOINT_REFRESH_REQUIRED", {
      episode,
      probeAttempt,
      htmlBurstDocumentCount: failures,
      action: "manual_source_refresh_then_resume"
    });
    return;
  }

  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.RUNNING,
    pauseReason: null,
    recoveryStartedAt: null,
    resumeAfterRecovery: false,
    sessionGuardStatus: "CONTENT_COOLDOWN",
    htmlBurstDetectedAt: new Date(nowMs).toISOString(),
    htmlBurstDocumentCount: failures,
    htmlCircuitState: "open",
    htmlCircuitEpisode: episode,
    htmlCircuitProbeAttempt: probeAttempt,
    htmlCircuitOpenCount: openCount,
    htmlCircuitHalfOpen: false,
    htmlCircuitRecoverySuccesses: 0,
    htmlCircuitOpenedAt: isReopen ? (session.htmlCircuitOpenedAt || new Date(nowMs).toISOString()) : new Date(nowMs).toISOString(),
    htmlCircuitHalfOpenedAt: null,
    htmlCircuitNextProbeAt: new Date(cooldownUntil).toISOString(),
    htmlCircuitLastReason: isReopen ? "half_open_probe_failed" : "html_burst",
    contentCooldownCount: Math.max(0, Number(session.contentCooldownCount || 0)) + 1,
    contentCooldownUntil: new Date(cooldownUntil).toISOString(),
    nextDownloadAt: cooldownUntil,
    pacing: resetPacing
  });
  await recordDiagnosticEvent(sessionId, isReopen ? "HTML_CIRCUIT_PROBE_FAILED" : "HTML_CIRCUIT_OPEN", {
    reason: isReopen ? "half_open_probe_failed" : "html_burst",
    episode,
    probeAttempt,
    openCount,
    cooldownMs,
    freshPrimaryAvailable: Boolean(freshPrimary),
    htmlBurstDocumentCount: failures,
    windowAttempts: Number(projected.windowAttempts || 0),
    windowHtmlFailures: Number(projected.windowHtmlFailures || 0),
    windowHtmlRate: Number(projected.windowHtmlRate || 0),
    consecutiveHtml: Number(projected.consecutiveHtml || 0),
    queueFrozen: false
  });
  await scheduleQueueWake(sessionId, cooldownUntil);
}

export async function handleAlarm(alarm) {
  if (alarm?.name) cancelLocalOperationalTimer(alarm.name);
  if (await handleArchiveDownloadUiAlarm(alarm)) return;
  if (alarm.name.startsWith(DOWNLOAD_WATCHDOG_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(DOWNLOAD_WATCHDOG_ALARM_PREFIX.length);
    const document = await documentStore.firstBySessionStatus(sessionId, STATUS.DOWNLOADING)
      || await documentStore.firstBySessionStatus(sessionId, STATUS.AWAITING_BROWSER);
    if (!document) return;
    let item = null;
    try { [item] = Number.isInteger(document.downloadId) ? await chrome.downloads.search({id: document.downloadId}) : []; } catch {}
    if (item?.state === "complete") {
      await handleDownloadChanged({id: item.id, state: {current: "complete"}});
      return;
    }
    if (item?.state === "interrupted") {
      await handleDownloadChanged({id: item.id, state: {current: "interrupted"}});
      return;
    }

    const nowMs = Date.now();
    if (item?.state === "in_progress") {
      const observedBytes = Math.max(0, Number(item.bytesReceived || item.fileSize || 0));
      const previousBytes = Math.max(0, Number(document.watchdogLastBytesReceived || 0));
      let lastProgressAtMs = Date.parse(document.watchdogLastProgressAt || "");
      if (!Number.isFinite(lastProgressAtMs)) lastProgressAtMs = Number(document.downloadStartedAtMs || Date.parse(document.startedAt || "") || nowMs);

      if (observedBytes > previousBytes) {
        await documentStore.update(document.key, {
          watchdogLastBytesReceived: observedBytes,
          watchdogLastProgressAt: new Date(nowMs).toISOString(),
          watchdogLastObservedAt: new Date(nowMs).toISOString()
        });
        await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: nowMs + DOWNLOAD_WATCHDOG_POLL_MS});
        return;
      }

      if (item.paused && item.canResume) {
        try { await chrome.downloads.resume(item.id); } catch {}
        await documentStore.update(document.key, {
          watchdogLastObservedAt: new Date(nowMs).toISOString(),
          watchdogResumeAttemptAt: new Date(nowMs).toISOString()
        });
        await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: nowMs + DOWNLOAD_WATCHDOG_POLL_MS});
        return;
      }

      const stalledForMs = Math.max(0, nowMs - lastProgressAtMs);
      const runningForMs = metricDuration(document.downloadStartedAtMs || Date.parse(document.startedAt || ""), nowMs) || 0;
      if (stalledForMs < DOWNLOAD_STALL_TIMEOUT_MS && runningForMs < DOWNLOAD_PROGRESS_HARD_LIMIT_MS) {
        await documentStore.update(document.key, {watchdogLastObservedAt: new Date(nowMs).toISOString()});
        await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: nowMs + DOWNLOAD_WATCHDOG_POLL_MS});
        return;
      }

      const stallDetail = `Tarayıcı indirmesi ${Math.round(stalledForMs / 1000)} sn ilerleme göstermedi; ${observedBytes} baytta kaldı. Bu evrak ertelenip kuyruk devam ettirildi.`;
      // Move the document out of DOWNLOADING before cancel(), so the resulting
      // Chrome interrupted event cannot charge the same attempt twice.
      await clearPendingDownload();
      await clearOperationalAlarm(watchdogAlarmName(sessionId));
      await applyAttemptPerformance(document.sessionId, {
        documentKey: document.key,
        queueLookupMs: document.perfQueueLookupMs,
        clickToDownloadMs: document.perfClickToDownloadMs,
        downloadMs: metricDuration(document.downloadStartedAtMs, nowMs),
        totalMs: metricDuration(document.attemptStartedAtMs || Date.parse(document.startedAt || ""), nowMs)
      }, { ok: false, errorCode: "DOWNLOAD_STALLED", schedule: false });
      await handleFailure(document, "DOWNLOAD_STALLED", stallDetail);
      try { await chrome.downloads.cancel(item.id); } catch {}
      await forgetManagedDownload(item.id, {removeFile:true});
      await recordDiagnosticEvent(sessionId, "DOWNLOAD_STALLED_CONTINUED", {
        documentId: document.documentId,
        stalledForMs,
        bytesReceived: observedBytes,
        totalBytes: Number.isFinite(Number(item.totalBytes)) ? Number(item.totalBytes) : null,
        bindingMethod: document.bindingMethod || null
      });
      const sessionAfter = await sessionStore.get(sessionId);
      if (sessionAfter?.status === SESSION_STATUS.RECOVERING) {
        await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 500});
      } else {
        void runNextQueueItem();
      }
      return;
    }

    // No browser record yet: let the click-timeout path own AWAITING_BROWSER.
    if (document.status === STATUS.AWAITING_BROWSER) return;
    await handleFailure(document, "DOWNLOAD_INTERRUPTED", "Tarayıcı indirme kaydı bulunamadı.");
    void runNextQueueItem();
    return;
  }

  if (alarm.name.startsWith(HEARTBEAT_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(HEARTBEAT_ALARM_PREFIX.length);
    const session = await sessionStore.get(sessionId);
    if (session?.status === SESSION_STATUS.RUNNING) {
      // Session Guardian heartbeat is independent from the browser download.
      // A current download may finish/validate, but no new queue item starts if
      // the heartbeat detects a lost login.
      if (await performSessionHeartbeat(session)) void runNextQueueItem();
    }
    return;
  }

  if (alarm.name.startsWith(LOGIN_WATCH_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(LOGIN_WATCH_ALARM_PREFIX.length);
    const session = await sessionStore.get(sessionId);
    if (session?.status === SESSION_STATUS.PAUSED && session.pauseReason === "LOGIN_REQUIRED") {
      await attemptResumeLoginSession(session.tabId);
    }
    return;
  }

  if (alarm.name.startsWith(QUEUE_WAKE_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(QUEUE_WAKE_ALARM_PREFIX.length);
    await sessionStore.update(sessionId, {nextQueueWakeAt: null});
    void runNextQueueItem();
    return;
  }

  if (alarm.name.startsWith(QUEUE_GUARD_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(QUEUE_GUARD_ALARM_PREFIX.length);
    const session = await sessionStore.get(sessionId);
    if (!session || [SESSION_STATUS.PAUSED, SESSION_STATUS.INCOMPLETE, SESSION_STATUS.COMPLETED, SESSION_STATUS.READY].includes(session.status)) {
      await clearOperationalAlarm(queueGuardAlarmName(sessionId));
      return;
    }
    if (session?.status === SESSION_STATUS.RUNNING) {
      if (session.finalizationInProgress) return;
      const active = await documentStore.hasActiveDownload(sessionId);
      if (active) {
        await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: Date.now() + 30_000});
      } else {
        // Durable liveness nudge. This is intentionally harmless if the normal
        // hot-path timer is already moving the queue.
        await recordDiagnosticEvent(sessionId, "QUEUE_GUARD_WAKE", {
          nextQueueWakeAt: Number(session.nextQueueWakeAt || 0) || null
        });
        void runNextQueueItem();
      }
    }
    return;
  }

  if (alarm.name.startsWith(SESSION_DRAIN_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(SESSION_DRAIN_ALARM_PREFIX.length);
    const document = await documentStore.firstBySessionStatus(sessionId, STATUS.AWAITING_BROWSER);
    if (!document) return;
    const pending = await getPendingDownload();
    if (!pending || pending.key !== document.key || pending.attemptId !== document.attemptId) return;

    // If Chrome already created our attempt-specific staging download but the
    // event listener was asleep, recover it before retiring the attempt.
    if (document.stagingPath) {
      const name = document.stagingPath.split('/').at(-1);
      const items = await chrome.downloads.search({
        filenameRegex: name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '$'
      });
      const own = (items || []).filter((item) =>
        item.byExtensionId === chrome.runtime.id &&
        item.filename?.replaceAll('\\', '/').endsWith(document.stagingPath)
      );
      if (own.length === 1) {
        await documentStore.update(document.key, {
          downloadId: own[0].id,
          status: STATUS.DOWNLOADING,
          bindingMethod: 'extension-download-id'
        });
        await clearPendingDownload();
        if (['complete', 'interrupted'].includes(own[0].state)) {
          await handleDownloadChanged({id: own[0].id, state: {current: own[0].state}});
        } else {
          await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: Date.now() + 30_000});
        }
        return;
      }
    }

    const current = await documentStore.get(document.key);
    const currentPending = await getPendingDownload();
    if (current?.status !== STATUS.AWAITING_BROWSER || current?.attemptId !== document.attemptId ||
        !currentPending || currentPending.key !== document.key || currentPending.attemptId !== document.attemptId) {
      return;
    }
    await clearPendingDownload();
    await clearOperationalAlarm(clickTimeoutAlarmName(sessionId));
    await requeueDocumentAfterSessionLoss(current, "SESSION_LOSS_DRAIN_EXPIRED");
    const session = await sessionStore.get(sessionId);
    if (session?.status === SESSION_STATUS.RUNNING) {
      await scheduleQueueWake(sessionId, Date.now() + 250);
      void runNextQueueItem();
    }
    return;
  }

  // Backward compatibility: legacy per-document retry alarms can still fire
  // after an extension upgrade. They are never created by this version.
  if (alarm.name.startsWith("retry:")) {
    void runNextQueueItem();
    return;
  }

  if (alarm.name.startsWith(SESSION_RECOVERY_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(SESSION_RECOVERY_ALARM_PREFIX.length);
    const session = await sessionStore.get(sessionId);
    if (session?.status === SESSION_STATUS.RECOVERING) {
      await clearExpiredOrphanPendingDownload(sessionId, {reason:"source-recovery"});
      if (session.pauseReason === "NATIVE_OPERATION_RECOVERY") {
        const blocked = session.nativeOperationDocumentKey
          ? await documentStore.get(session.nativeOperationDocumentKey)
          : null;
        const downloadId = Number.isInteger(session.nativeOperationDownloadId)
          ? session.nativeOperationDownloadId
          : blocked?.downloadId;
        const [item] = Number.isInteger(downloadId)
          ? await chrome.downloads.search({id:downloadId})
          : [];
        if (blocked?.sessionId === sessionId && item?.state === "complete") {
          await documentStore.update(blocked.key, {
            status: STATUS.DOWNLOADING,
            warnings: (blocked.warnings || []).filter((value) => !NATIVE_OPERATION_ERROR_CODES.has(value)),
            errorCode: null,
            errorMessage: null
          });
          await sessionStore.update(sessionId, {
            status: SESSION_STATUS.RUNNING,
            pauseReason: null,
            recoveryStartedAt: null,
            resumeAfterRecovery: false,
            sessionGuardStatus: "HEALTHY",
            queueError: null,
            nativeOperationDocumentKey: null,
            nativeOperationDownloadId: null,
            nativeOperationRetryAt: null
          });
          await recordDiagnosticEvent(sessionId, "NATIVE_OPERATION_RETRY_STARTED", {
            documentId: blocked.documentId || null,
            errorCode: blocked.errorCode || null,
            retryCount: Number(blocked.nativeOperationRetryCount || 0),
            reusedBrowserDownload: true
          });
          await handleDownloadChanged({id:item.id, state:{current:"complete"}});
          return;
        }
        if (blocked?.sessionId === sessionId) {
          await documentStore.update(blocked.key, {
            status: STATUS.QUEUED,
            attempts: Math.max(0, Number(blocked.attempts || 1) - 1),
            downloadId: null,
            attemptId: null,
            stagingPath: null,
            actualFilename: null,
            errorCode: null,
            errorMessage: null,
            warnings: (blocked.warnings || []).filter((value) => !NATIVE_OPERATION_ERROR_CODES.has(value))
          });
          await recordDiagnosticEvent(sessionId, "NATIVE_OPERATION_RETRY_FALLBACK_DOWNLOAD", {
            documentId: blocked.documentId || null,
            retryCount: Number(blocked.nativeOperationRetryCount || 0),
            reason: "browser-history-missing"
          });
        }
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.RUNNING,
          pauseReason: null,
          recoveryStartedAt: null,
          resumeAfterRecovery: false,
          sessionGuardStatus: "HEALTHY",
          queueError: null,
          nativeOperationDocumentKey: null,
          nativeOperationDownloadId: null,
          nativeOperationRetryAt: null
        });
        await scheduleQueueWake(sessionId, Date.now() + 250);
        void runNextQueueItem();
        return;
      }
      if (session.pauseReason === "NATIVE_AGENT_RECOVERY") {
        const agent = await pingNativeAgent();
        if (!agent.ok) {
          await sessionStore.update(sessionId, {
            queueError: agent.error || "Yerel ajan hâlâ yanıt vermiyor.",
            sessionGuardStatus: "NATIVE_RECOVERY"
          });
          await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 30_000});
          return;
        }
        await cleanupResolvedNativeTransientState(sessionId, {reason:"native-recovery-ping-success", forceDocuments:true});
        const blocked = await documentStore.firstBySessionStatusError(
          sessionId, STATUS.REVIEW_REQUIRED, "NATIVE_AGENT_UNAVAILABLE"
        );
        if (blocked) {
          const [item] = Number.isInteger(blocked.downloadId)
            ? await chrome.downloads.search({id: blocked.downloadId})
            : [];
          if (item?.state === "complete") {
            await documentStore.update(blocked.key, {
              status: STATUS.DOWNLOADING,
              warnings: (blocked.warnings || []).filter((value) => value !== "NATIVE_AGENT_UNAVAILABLE"),
              errorCode: null,
              errorMessage: null
            });
            await sessionStore.update(sessionId, {
              status: SESSION_STATUS.RUNNING,
              pauseReason: null,
              recoveryStartedAt: null,
              resumeAfterRecovery: false,
              sessionGuardStatus: "HEALTHY",
              queueError: null
            });
            await handleDownloadChanged({id:item.id, state:{current:"complete"}});
            return;
          }
          // Browser history may have been pruned after a restart. Requeue the
          // document instead of requiring a manual intervention.
          await documentStore.update(blocked.key, {
            status: STATUS.QUEUED,
            attempts: Math.max(0, Number(blocked.attempts || 1) - 1),
            warnings: (blocked.warnings || []).filter((value) => value !== "NATIVE_AGENT_UNAVAILABLE"),
            downloadId: null,
            attemptId: null,
            stagingPath: null,
            retryAt: null,
            errorCode: null,
            errorMessage: null
          });
        }
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.RUNNING,
          pauseReason: null,
          recoveryStartedAt: null,
          resumeAfterRecovery: false,
          sessionGuardStatus: "HEALTHY",
          queueError: null,
          sourceWaitRecoveryCount: 0,
          sourceWaitNextRetryAt: null,
          recoveryProbeFailures: 0,
          recoveryProbeNextRetryAt: null,
          sourceRebindRecoveryCount: 0,
          sourceRebindNextRetryAt: null
        });
        await scheduleHeartbeat(sessionId);
        await scheduleQueueGuard(sessionId);
        await scheduleQueueWake(sessionId, Date.now() + 250);
        void runNextQueueItem();
        return;
      }
      let candidateTabId = await findReadySessionTab(session, session.tabId);
      if (!Number.isInteger(candidateTabId) && String(session.sourceSystem || '').toUpperCase() === 'UYAP_KURUM') {
        const activeDownload = await activeInProgressBrowserDownload(sessionId);
        if (!activeDownload && Number.isInteger(session.tabId)) {
          const restored = await tryRestoreKurumSourceView(session, session.tabId);
          if (restored) candidateTabId = session.tabId;
        }
      }
      if (!Number.isInteger(candidateTabId)) {
        const sourceWaitRecoveryCount = Math.max(0, Number(session.sourceWaitRecoveryCount || 0));
        const retryMs = sourceWaitRecoveryDelayMs(sourceWaitRecoveryCount);
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.RECOVERING,
          pauseReason: session.pauseReason || "SOURCE_PAGE_NOT_READY",
          resumeAfterRecovery: true,
          sessionGuardStatus: "WAITING_FOR_SOURCE",
          sourceWaitRecoveryCount: sourceWaitRecoveryCount + 1,
          sourceWaitNextRetryAt: new Date(Date.now() + retryMs).toISOString()
        });
        await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + retryMs});
        return;
      }
      const probe = await probeSessionHealth(session, candidateTabId);
      if (probe.loginRequired) {
        await pauseForLoginRequired(session, {source: "recovery_probe", detail: probe.reason || "LOGIN_REQUIRED"});
        return;
      }
      if (!probe.healthy) {
        const recoveryProbeFailures = Math.max(0, Number(session.recoveryProbeFailures || 0));
        const retryMs = recoveryProbeDelayMs(recoveryProbeFailures);
        await sessionStore.update(sessionId, {
          recoveryProbeFailures: recoveryProbeFailures + 1,
          recoveryProbeNextRetryAt: new Date(Date.now() + retryMs).toISOString(),
          sessionGuardStatus: "RECOVERY_PROBE_RETRY"
        });
        await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + retryMs});
        return;
      }
      if (session.pauseReason === "START_PATH_DEGRADED") {
        await recoverStaleAwaitingBrowser(sessionId);
        const preflight = await runStartPreflight(sessionId, candidateTabId, {autoRecover:true});
        if (preflight?.ok === false) return;
        await sessionStore.update(sessionId, {
          tabId: candidateTabId,
          status: SESSION_STATUS.RUNNING,
          pauseReason: null,
          recoveryStartedAt: null,
          resumeAfterRecovery: false,
          consecutiveStartFailures: 0,
          startPathRecoveryCount: 0,
          startPathNextRetryAt: null,
          sessionGuardStatus: "HEALTHY",
          queueError: null,
          sourceWaitRecoveryCount: 0,
          sourceWaitNextRetryAt: null,
          recoveryProbeFailures: 0,
          recoveryProbeNextRetryAt: null,
          sourceRebindRecoveryCount: 0,
          sourceRebindNextRetryAt: null
        });
        await scheduleHeartbeat(sessionId);
        await scheduleQueueGuard(sessionId);
        await scheduleQueueWake(sessionId, Date.now() + 250);
        void runNextQueueItem();
        return;
      }
      if (["CONTENT_ENDPOINT_DEGRADED", "CONTENT_ENDPOINT_CIRCUIT_OPEN"].includes(session.pauseReason)) {
        // The source/login probe is healthy. For legacy RC15.9.2 circuit-open recovery
        // resumes in HALF-OPEN state: one controlled document is allowed through.
        // Two verified documents close the circuit; a single HTML response opens
        // it again immediately with an escalated bounded cooldown.
        await recoverStaleAwaitingBrowser(sessionId);
        const shouldResume = session.resumeAfterRecovery !== false;
        const circuitRecovery = session.pauseReason === "CONTENT_ENDPOINT_CIRCUIT_OPEN";
        await sessionStore.update(sessionId, {
          tabId: candidateTabId,
          status: shouldResume ? SESSION_STATUS.RUNNING : SESSION_STATUS.PAUSED,
          pauseReason: shouldResume ? null : "CONNECTION_VERIFIED",
          consecutiveHtmlFailures: 0,
          lastHtmlFailureDocumentKey: null,
          recoveryStartedAt: null,
          resumeAfterRecovery: false,
          contentCooldownUntil: null,
          htmlCircuitHalfOpen: Boolean(circuitRecovery && shouldResume),
          htmlCircuitRecoverySuccesses: 0,
          htmlCircuitHalfOpenedAt: circuitRecovery && shouldResume ? new Date().toISOString() : session.htmlCircuitHalfOpenedAt || null,
          lastHeartbeatAt: shouldResume ? new Date().toISOString() : session.lastHeartbeatAt,
          sessionGuardStatus: shouldResume
            ? (circuitRecovery ? "CONTENT_CIRCUIT_HALF_OPEN" : "HEALTHY")
            : "READY",
          nextDownloadAt: shouldResume ? Date.now() + 500 : Number(session.nextDownloadAt || 0)
        });
        if (circuitRecovery && shouldResume) {
          await recordDiagnosticEvent(sessionId, "HTML_CIRCUIT_HALF_OPEN", {
            openCount: Number(session.htmlCircuitOpenCount || 0),
            requiredStableVerified: HTML_CIRCUIT_STABLE_SUCCESSES
          });
        }
        if (shouldResume) {
          await scheduleHeartbeat(sessionId);
          await scheduleQueueGuard(sessionId);
          await scheduleQueueWake(sessionId, Date.now() + 500);
          void runNextQueueItem();
        }
        return;
      }
      const binding = await refreshSessionRuntimeBindings(sessionId, candidateTabId, {activeBatchOnly: Boolean(session.largeCaseMode)});
      if (!bindingIsComplete(binding)) {
        if (binding.contextMismatch) {
          await pauseForSourceContextChanged(sessionId, binding);
          return;
        }
        const sourceRebindRecoveryCount = Math.max(0, Number(session.sourceRebindRecoveryCount || 0));
        const retryMs = sourceRebindRecoveryDelayMs(sourceRebindRecoveryCount, session.total);
        await sessionStore.update(sessionId, {
          status: SESSION_STATUS.RECOVERING,
          pauseReason: "SOURCE_REBIND_REQUIRED",
          runtimeBound: binding.bound,
          runtimeUnmatched: binding.unmatched,
          runtimeScanned: binding.scanned,
          resumeAfterRecovery: true,
          sessionGuardStatus: "AUTO_RECOVERY",
          sourceRebindRecoveryCount: sourceRebindRecoveryCount + 1,
          sourceRebindNextRetryAt: new Date(Date.now() + retryMs).toISOString()
        });
        await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + retryMs});
        return;
      }
      await recoverStaleAwaitingBrowser(sessionId);
      await repairMassDownloadStartFailures(sessionId);
      const shouldResume = session.resumeAfterRecovery !== false;
      await sessionStore.update(sessionId, {
        tabId: candidateTabId,
        status: shouldResume ? SESSION_STATUS.RUNNING : SESSION_STATUS.PAUSED,
        pauseReason: shouldResume ? null : "CONNECTION_VERIFIED",
        consecutiveHtmlFailures: 0,
        lastHtmlFailureDocumentKey: null,
        lastHeartbeatAt: shouldResume ? new Date().toISOString() : session.lastHeartbeatAt,
        recoveryStartedAt: null,
        resumeAfterRecovery: false,
        runtimeBound: binding.bound,
        runtimeUnmatched: binding.unmatched,
        runtimeScanned: binding.scanned,
        sessionGuardStatus: shouldResume ? "HEALTHY" : "READY",
        sourceWaitRecoveryCount: 0,
        sourceWaitNextRetryAt: null,
        recoveryProbeFailures: 0,
        recoveryProbeNextRetryAt: null,
        sourceRebindRecoveryCount: 0,
        sourceRebindNextRetryAt: null
      });
      if (shouldResume) {
        await setSessionGuardBadge(false);
        await scheduleHeartbeat(sessionId);
        await scheduleQueueGuard(sessionId);
        await scheduleQueueWake(sessionId, Date.now() + 250);
        void runNextQueueItem();
      }
    }
    return;
  }

  if (alarm.name.startsWith(CLICK_TIMEOUT_ALARM_PREFIX)) {
    const sessionId = alarm.name.slice(CLICK_TIMEOUT_ALARM_PREFIX.length);
    const pending = await getPendingDownload();
    if (!pending || pending.sessionId !== sessionId) return;
    const document = await documentStore.get(pending.key);
    const timeoutMs = Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0));
    if (document?.status === STATUS.AWAITING_BROWSER && document.attemptId === pending.attemptId &&
        Date.now() - Date.parse(document.startedAt) >= timeoutMs) {
      const session = await sessionStore.get(sessionId);
      if (session?.status === SESSION_STATUS.PAUSED && session.pauseReason === "LOGIN_REQUIRED") {
        // The click was in flight while UYAP logged out. This is a session
        // interruption, not a document failure; preserve the retry budget.
        await clearPendingDownload();
        await clearOperationalAlarm(clickTimeoutAlarmName(sessionId));
        await requeueDocumentAfterSessionLoss(document, "LOGIN_REQUIRED_DURING_CLICK");
        return;
      }
      // Nothing else caught session loss while this click was pending. A
      // degraded/slow UYAP response can outlast CLICK_DOWNLOAD_TIMEOUT_MS
      // without ever firing chrome.downloads.onCreated in time, which would
      // otherwise be charged as a structural start failure against
      // consecutiveStartFailures and can trip MASS_START_FAILURE_PROTECTED
      // on a run of genuinely session-caused timeouts. Probe once, the same
      // way the HTML_RESPONSE path does, before charging the document.
      if (session?.status === SESSION_STATUS.RUNNING) {
        await performSessionHeartbeat(session);
        const sessionAfterProbe = await sessionStore.get(sessionId);
        const sessionInterrupted =
          (sessionAfterProbe?.status === SESSION_STATUS.PAUSED && sessionAfterProbe.pauseReason === "LOGIN_REQUIRED") ||
          (sessionAfterProbe?.status === SESSION_STATUS.RECOVERING && sessionAfterProbe.pauseReason === "SOURCE_PAGE_NOT_READY");
        if (sessionInterrupted) {
          await clearPendingDownload();
          await clearOperationalAlarm(clickTimeoutAlarmName(sessionId));
          await requeueDocumentAfterSessionLoss(
            document,
            sessionAfterProbe.pauseReason === "LOGIN_REQUIRED" ? "LOGIN_REQUIRED_DURING_CLICK" : "SOURCE_UNAVAILABLE_DURING_CLICK"
          );
          return;
        }
      }
      await clearPendingDownload();
      await applyAttemptPerformance(document.sessionId, {
        documentKey: document.key,
      queueLookupMs: document.perfQueueLookupMs,
        totalMs: metricDuration(document.attemptStartedAtMs || Date.parse(document.startedAt || ""), Date.now())
      }, { ok: false, errorCode: "CLICK_DOWNLOAD_TIMEOUT", schedule: false });
      if (await recordStructuralStartFailure(document, "CLICK_DOWNLOAD_TIMEOUT", "UYAP Kaydet sonrası tarayıcı indirmesi zamanında başlamadı.")) return;
      await handleFailure(document, "CLICK_DOWNLOAD_TIMEOUT");
      void runNextQueueItem();
    }
  }
}

function problemCategory(errorCode = "", document = null) {
  const code = String(errorCode || "");
  if (HTML_REVIEW_ERROR_CODES.has(code) || (document && hasPreservedHtmlEvidence(document))) return "UYAP hata/oturum HTML yanıtı";
  if (["UNSUPPORTED_FORMAT", "SIGNATURE_MISMATCH", "INVALID_IMAGE", "INVALID_UDF_ENTRIES", "INVALID_UDF_XML", "UDF_LIMIT_EXCEEDED", "ZIP_CRC_FAILED", "ZIP_ENCRYPTED", "ZIP_LIMIT_EXCEEDED", "ZIP_UNSAFE_MEMBER"].includes(code)) return "Desteklenmeyen / doğrulanamayan format";
  if (["PDF_TRAILING_DATA", "TRUNCATED_PDF"].includes(code)) return "PDF yapı sorunu";
  if (["TRUNCATED_TIFF", "DOWNLOAD_SIZE_MISMATCH", "DOWNLOAD_NOT_COMPLETE", "SOURCE_FILE_MISSING", "DOWNLOAD_INTERRUPTED", "DOWNLOAD_STALLED"].includes(code)) return "Eksik / kesilmiş indirme";
  if (code === "LOCAL_ARCHIVE_AMBIGUOUS") return "Yerel arşiv eşleşmesi";
  if (["NATIVE_AGENT_UNAVAILABLE", "TRANSFER_INTEGRITY_FAILED"].includes(code) || NATIVE_OPERATION_ERROR_CODES.has(code)) return "Yerel ajan / dosya doğrulama";
  if (["DOWNLOAD_START_FAILED", "NATIVE_DOWNLOAD_NOT_CORRELATED", "CLICK_DOWNLOAD_TIMEOUT"].includes(code)) return "İndirme başlatma";
  return "Diğer";
}

function problemRecommendedAction(document = {}) {
  const code = String(document.errorCode || "");
  if (code === "LOCAL_ARCHIVE_AMBIGUOUS") return "Yerel arşivi kontrol et";
  if ((code === "NATIVE_AGENT_UNAVAILABLE" || NATIVE_OPERATION_ERROR_CODES.has(code)) && Number.isInteger(document.downloadId) && document.stagingPath && document.actualFilename) {
    return "Yeniden doğrula";
  }
  if (["UNSUPPORTED_FORMAT", "SIGNATURE_MISMATCH", "INVALID_IMAGE", "INVALID_UDF_ENTRIES", "INVALID_UDF_XML", "UDF_LIMIT_EXCEEDED", "ZIP_CRC_FAILED", "ZIP_ENCRYPTED", "ZIP_LIMIT_EXCEEDED", "ZIP_UNSAFE_MEMBER", "PDF_TRAILING_DATA"].includes(code)) {
    return "Yeniden indir (manuel)";
  }
  return "Yeniden indir";
}

function problemUiItem(document = {}) {
  return {
    key: document.key,
    documentId: document.documentId || null,
    title: document.documentTitle || document.originalFilename || document.actualFilename?.split(/[\\/]/).pop() || "Adsız evrak",
    status: document.status || null,
    errorCode: document.errorCode || null,
    errorMessage: document.errorMessage || null,
    attempts: Number(document.attempts || 0),
    category: problemCategory(document.errorCode, document),
    recommendedAction: problemRecommendedAction(document),
    retryAt: document.retryAt || null,
    retryClass: document.retryClass || null,
    manualRetryPriority: document.manualRetryPriority === true,
    extension: document.extension || null,
    actualMime: document.actualMime || null,
    sourceSystem: document.sourceSystem || "UYAP",
    targetPath: document.targetPath || null,
    quarantinePath: document.quarantinePath || null,
    canConfirmHtmlExclusion: HTML_REVIEW_ERROR_CODES.has(String(document.errorCode || "")) || isTerminalHtmlReviewDocument(document)
  };
}

export async function confirmHtmlExclusion(sessionId, key) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error("İş bulunamadı.");
  let document = await documentStore.get(key);
  if (!document || document.sessionId !== sessionId) throw new Error("Evrak kaydı bulunamadı.");
  if (isTerminalHtmlReviewDocument(document)) {
    document = await normalizeTerminalHtmlReviewDocument(document);
  }
  if (![STATUS.REVIEW_REQUIRED, STATUS.FAILED].includes(document.status)) {
    throw new Error("Bu kayıt kullanıcı HTML onayı için uygun durumda değil.");
  }
  if (!(HTML_REVIEW_ERROR_CODES.has(String(document.errorCode || "")) || hasPreservedHtmlEvidence(document))) {
    throw new Error("Yalnız HTML yanıtı olarak doğrulanan sorunlar bu işlemle hariç tutulabilir.");
  }
  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  const downloadsRoot = session.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  if (!downloadsRoot) throw new Error("Arşiv kökü bulunamadı.");
  const result = await askNativeAgent({
    action: "discard_review_html",
    sessionId,
    downloadsRoot,
    quarantinePath: document.quarantinePath,
    document: {...document, attempts:Number(document.attempts || 0)}
  });
  if (!result?.ok) throw new Error(result?.error || "HTML karantina kaydı silinemedi.");
  invalidateArchiveRegistryCache();
  const now = new Date().toISOString();
  await documentStore.update(document.key, {
    status: STATUS.SKIPPED,
    skipReason: "USER_CONFIRMED_HTML_RESPONSE",
    userExcluded: true,
    userExcludedAt: now,
    userExcludedErrorCode: document.errorCode || "HTML_RESPONSE",
    validationNotes: [...(Array.isArray(document.validationNotes) ? document.validationNotes : []), "Kullanıcı, UYAP kaynağının gerçekten HTML yanıtı verdiğini onayladı; kayıt arşiv kapsamından çıkarıldı."],
    quarantinePath: null,
    retryAt: null,
    retryClass: null,
    retryExhausted: true,
    errorCode: null,
    errorMessage: null,
    updatedAt: now
  });
  await recordDiagnosticEvent(sessionId, "USER_CONFIRMED_HTML_EXCLUSION", {
    documentId: document.documentId || null,
    previousErrorCode: document.errorCode || null
  });
  const documents = await documentStore.bySession(sessionId);
  const unresolved = documents.filter((item) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(item.status));
  const excludedCount = userExcludedDocuments(documents).length;
  await sessionStore.update(sessionId, {unresolved: unresolved.length, downloadsRoot, userExcludedCount: excludedCount});
  const current = await sessionStore.get(sessionId);
  if (!unresolved.length) {
    const exclusionOnly = allUserExcludedFinalization(documents);
    if (exclusionOnly) {
      const completedAt = new Date().toISOString();
      await sessionStore.update(sessionId, {
        status: SESSION_STATUS.COMPLETED,
        archiveStatus: exclusionOnly.archiveStatus,
        partialArchiveAvailable: false,
        unresolved: 0,
        manifestPath: null,
        zipPath: null,
        archiveFormat: exclusionOnly.archiveFormat,
        archiveParts: [],
        finalIntegrity: exclusionOnly.integrity,
        userExcludedCount: excludedCount,
        finalizationError: null,
        finalizationInProgress: false,
        finalRecoveryActive: false,
        finalRecoveryQueued: 0,
        finalRecoveryCompletedAt: completedAt,
        pauseReason: null,
        pausedAt: null
      });
      await recordDiagnosticEvent(sessionId, "USER_EXCLUSIONS_ONLY_COMPLETED", {userExcludedCount: excludedCount});
      const completed = await sessionStore.get(sessionId);
      await showArchiveCompletionNotification(completed);
      await restoreArchiveDownloadUi();
      return getSessionSummary(sessionId);
    }
  }
  if ([SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(current?.status)) {
    await scheduleQueueWake(sessionId, Date.now() + 100);
    void runNextQueueItem();
    return getSessionSummary(sessionId);
  }
  if (!unresolved.length) {
    try { return await repackageVerifiedArchive(sessionId); } catch (error) {
      await recordDiagnosticEvent(sessionId, "HTML_EXCLUSION_REPACKAGE_DEFERRED", {error:String(error).slice(0,240)});
    }
  }
  return getSessionSummary(sessionId);
}

function userExcludedDocuments(documents = []) {
  return documents.filter((document) => document.status === STATUS.SKIPPED && document.userExcluded === true && document.skipReason === "USER_CONFIRMED_HTML_RESPONSE");
}

function finalizationDocuments(documents = []) {
  return documents.filter((document) => !(document.status === STATUS.SKIPPED && document.userExcluded === true && document.skipReason === "USER_CONFIRMED_HTML_RESPONSE"));
}

function isFinalizationProofRecoverableHtml(document = {}) {
  const code = String(document.errorCode || "");
  const digest = String(document.sha256 || "").toLowerCase();
  const size = Number(document.actualSize || 0);
  return Boolean(
    [STATUS.REVIEW_REQUIRED, STATUS.FAILED].includes(document.status) &&
    code === "HTML_RESPONSE" &&
    document.contentKind === "HTML_DOCUMENT" &&
    String(document.htmlValidationMode || "").trim() &&
    /^[0-9a-f]{64}$/.test(digest) &&
    Number.isFinite(size) && size > 0 &&
    String(document.actualFilename || "").trim()
  );
}

function allUserExcludedFinalization(documents = [], excludedDocuments = userExcludedDocuments(documents)) {
  if (!documents.length || excludedDocuments.length !== documents.length) return null;
  return {
    ok: true,
    archiveStatus: "VERIFIED_COMPLETE",
    unresolved: 0,
    manifestPath: null,
    zipPath: null,
    manifestSha256: null,
    zipSha256: null,
    archiveFormat: "USER_EXCLUSIONS_ONLY",
    archiveParts: [],
    partsManifestPath: null,
    partsManifestSha256: null,
    checksumsPath: null,
    checksumsSha256: null,
    integrity: {
      gatePassed: true,
      verifiedCount: 0,
      manifestDocumentCount: 0,
      sourceInventoryTotal: documents.length,
      userExcludedCount: excludedDocuments.length,
      allDocumentsUserExcluded: true,
      partCount: 0,
      verifiedPartCount: 0
    }
  };
}

export async function getProblemItems(sessionId, {limit = 200} = {}) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error("İş bulunamadı.");
  // RC15.9.1: old builds could leave deterministic image failures in the
  // deferred bucket. Normalize them before rendering Problem Center so an
  // INVALID_IMAGE never inflates the automatic-retry counter.
  await quarantineLegacyDeterministicRetries(sessionId);
  await migrateTerminalHtmlReviewState(sessionId);
  const safeLimit = Math.min(500, Math.max(1, Number(limit) || 200));
  const [reviewRows, deferredRows, failedRows] = await Promise.all([
    documentStore.bySessionStatus(sessionId, STATUS.REVIEW_REQUIRED),
    documentStore.bySessionStatus(sessionId, STATUS.RETRY_WAIT),
    documentStore.bySessionStatus(sessionId, STATUS.FAILED)
  ]);
  const review = [...reviewRows, ...failedRows]
    .sort((a,b) => String(a.updatedAt || "").localeCompare(String(b.updatedAt || "")))
    .slice(0, safeLimit)
    .map(problemUiItem);
  const deferred = deferredRows
    .sort((a,b) => Number(b.manualRetryPriority === true) - Number(a.manualRetryPriority === true) || String(a.retryAt || a.updatedAt || "").localeCompare(String(b.retryAt || b.updatedAt || "")))
    .slice(0, safeLimit)
    .map(problemUiItem);
  const categories = {};
  for (const row of [...reviewRows, ...failedRows]) {
    const category = problemCategory(row.errorCode);
    categories[category] = Number(categories[category] || 0) + 1;
  }
  return {
    review,
    deferred,
    reviewTotal: reviewRows.length + failedRows.length,
    deferredTotal: deferredRows.length,
    truncatedReview: reviewRows.length + failedRows.length > safeLimit,
    truncatedDeferred: deferredRows.length > safeLimit,
    categories
  };
}


const LOCAL_REVALIDATION_CODES = new Set([
  "UNSUPPORTED_FORMAT",
  "SIGNATURE_MISMATCH",
  "INVALID_IMAGE",
  "INVALID_UDF_ENTRIES",
  "INVALID_UDF_XML",
  "UDF_LIMIT_EXCEEDED",
  "PDF_TRAILING_DATA"
]);

async function tryLocalQuarantineRevalidation(document) {
  if (!document?.quarantinePath || !LOCAL_REVALIDATION_CODES.has(String(document.errorCode || ""))) {
    return {handled:false, revalidated:false};
  }
  const session = await sessionStore.get(document.sessionId);
  if (!session?.downloadsRoot || !document.targetPath) return {handled:false, revalidated:false};

  const agent = await askNativeAgent({
    action: "revalidate_quarantined",
    sessionId: document.sessionId,
    downloadsRoot: session.downloadsRoot,
    quarantinePath: document.quarantinePath,
    targetPath: document.targetPath,
    document: {
      ...document,
      attempts: Number(document.attempts || 0) + 1
    }
  });
  if (!agent?.ok) return {handled:false, revalidated:false};
  if (!agent.valid) {
    if (typeof agent.pdfTrailingCoreHex === "string") {
      await documentStore.update(document.key, {pdfTrailingCoreHex:agent.pdfTrailingCoreHex.slice(0,512)});
    }
    await recordDiagnosticEvent(document.sessionId, "LOCAL_QUARANTINE_REVALIDATION_REJECTED", {
      documentId: document.documentId || null,
      previousErrorCode: document.errorCode || null,
      errorCode: agent.errorCode || null,
      detectedExtension: agent.contentDetectedExtension || agent.extension || null,
      detectionMode: agent.contentDetectionMode || null,
      detectionHint: agent.contentDetectionHint || null
    });
    return {handled:true, revalidated:false, endpointFailure:isUyapInvalidRequestPdfTail(agent)};
  }

  invalidateArchiveRegistryCache();
  const now = new Date().toISOString();
  await documentStore.update(document.key, {
    status: STATUS.VERIFIED,
    warnings: Array.isArray(agent.warnings) ? agent.warnings : [],
    validationNotes: Array.isArray(agent.validationNotes) ? agent.validationNotes : [],
    contentDetectedExtension: agent.contentDetectedExtension || agent.extension || document.extension || null,
    contentDetectionMode: agent.contentDetectionMode || null,
    contentDetectionHint: agent.contentDetectionHint || null,
    extension: agent.extension || document.extension,
    sourceDocumentId: agent.sourceDocumentId || document.sourceDocumentId || null,
    targetPath: agent.targetPath || document.targetPath,
    actualFilename: agent.localPath,
    actualSize: agent.size,
    sha256: agent.sha256,
    contentKind: agent.contentKind || null,
    opaqueValidationMode: agent.opaqueValidationMode || null,
    udfValidationMode: agent.udfValidationMode || null,
    imageDetectedType: agent.imageDetectedType || null,
    imageValidationMode: agent.imageValidationMode || null,
    imageWidth: Number.isFinite(Number(agent.imageWidth)) ? Number(agent.imageWidth) : null,
    imageHeight: Number.isFinite(Number(agent.imageHeight)) ? Number(agent.imageHeight) : null,
    pdfEofOffset: Number.isFinite(Number(agent.pdfEofOffset)) ? Number(agent.pdfEofOffset) : document.pdfEofOffset || null,
    pdfTrailingBytes: Number.isFinite(Number(agent.pdfTrailingBytes)) ? Number(agent.pdfTrailingBytes) : document.pdfTrailingBytes || null,
    pdfTrailingCoreHex: typeof agent.pdfTrailingCoreHex === "string" ? agent.pdfTrailingCoreHex.slice(0, 512) : (document.pdfTrailingCoreHex || null),
    pdfStartXref: Number.isFinite(Number(agent.pdfStartXref)) ? Number(agent.pdfStartXref) : document.pdfStartXref || null,
    pdfXrefKind: agent.pdfXrefKind || document.pdfXrefKind || null,
    pdfTrailingKind: agent.pdfTrailingKind || document.pdfTrailingKind || null,
    completedAt: now,
    errorCode: null,
    errorMessage: null,
    quarantinePath: null,
    invalidRetryHint: null,
    invalidSha256: null,
    retryClass: null,
    retryAt: null,
    retryQueuedAt: null,
    retryExhausted: false,
    manualRetryRequestedAt: now,
    localRevalidatedAt: now
  });
  await sessionStore.update(document.sessionId, {downloadsRoot: agent.downloadsRoot || session.downloadsRoot});
  await recordDiagnosticEvent(document.sessionId, "LOCAL_QUARANTINE_REVALIDATED", {
    documentId: document.documentId || null,
    previousErrorCode: document.errorCode || null,
    extension: agent.extension || document.extension || null,
    udfValidationMode: agent.udfValidationMode || null,
    imageValidationMode: agent.imageValidationMode || null,
    opaqueValidationMode: agent.opaqueValidationMode || null
  });
  return {handled:true, revalidated:true};
}

async function autoRevalidatePdfTrailingReviews(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session) return {candidates:0,revalidated:0,deferred:0};
  const manifest = chrome.runtime?.getManifest?.() || {};
  const build = String(manifest.version_name || manifest.version || "unknown");
  if (session.autoPdfCommentRecoveryBuild === build) return {candidates:0,revalidated:0,deferred:0};
  const reviews = await documentStore.bySessionStatus(sessionId, STATUS.REVIEW_REQUIRED);
  const candidates = reviews.filter((document)=>document.errorCode === "PDF_TRAILING_DATA" && document.quarantinePath);
  let revalidated = 0, deferred = 0, endpointRecovered = 0;
  for (const document of candidates) {
    // Legacy agents omitted the tail evidence. Re-read preserved bytes before
    // classifying; length alone must never authorize a retry or acceptance.
    if (!document.pdfTrailingCoreHex) {
      try {
        const local = await tryLocalQuarantineRevalidation(document);
        if (local.revalidated) { revalidated += 1; continue; }
        if (!local.handled) { deferred += 1; continue; }
        if (!local.endpointFailure) continue;
        document.pdfTrailingCoreHex = UYAP_INVALID_REQUEST_TAIL_HEX;
      } catch { deferred += 1; continue; }
    }
    if (isUyapInvalidRequestPdfTail(document)) {
      const now = new Date().toISOString();
      await documentStore.update(document.key, {
        status:STATUS.RETRY_WAIT,
        attempts:0,
        errorCode:"HTML_RESPONSE",
        errorMessage:"UYAP belge servisi PDF yanıtının sonuna 'Invalid request' hata iletisi ekledi.",
        retryAt:now,
        retryQueuedAt:now,
        retryClass:"deferred_html",
        retryStrategy:"fresh_rebind",
        retryStrategyHistory:["fresh_rebind"],
        retryExhausted:false,
        attemptStrategy:null,
        attemptRecoveryClass:null,
        htmlRecoveryAttempts:0,
        htmlRecoveryFinal:false,
        completionRecoveryPreviousErrorCode:null,
        lastRuntimeResetPreviousErrorCode:null,
        originalValidationErrorCode:"PDF_TRAILING_DATA",
        endpointRecoveryMigratedAt:now
      });
      endpointRecovered += 1;
      continue;
    }
    try {
      const result = await tryLocalQuarantineRevalidation(document);
      if (result.revalidated) revalidated += 1;
      else if (!result.handled) deferred += 1;
    } catch { deferred += 1; }
  }
  if (!deferred) {
    await sessionStore.update(sessionId, {
      autoPdfCommentRecoveryBuild:build,
      autoPdfCommentRecoveryAt:new Date().toISOString(),
      autoPdfCommentRecoveryDocuments:revalidated,
      autoPdfEndpointRecoveryDocuments:endpointRecovered
    });
  }
  if (candidates.length) await recordDiagnosticEvent(sessionId, "PDF_COMMENT_TAIL_AUTO_RECOVERY", {
    candidates:candidates.length,
    revalidated,
    deferred,
    endpointRecovered
  });
  return {candidates:candidates.length,revalidated,deferred,endpointRecovered};
}

async function completedDownloadStillAvailable(document) {
  if (!Number.isInteger(document?.downloadId)) return false;
  const [item] = await chrome.downloads.search({id:document.downloadId});
  return item?.state === "complete" && item.exists !== false;
}

async function queueManualReviewRetry(document) {
  if (!document || ![STATUS.REVIEW_REQUIRED, STATUS.FAILED].includes(document.status)) return {queued:false, revalidated:false};
  const local = await tryLocalQuarantineRevalidation(document);
  if (local.revalidated) return {queued:false, revalidated:true};
  if ((document.errorCode === "NATIVE_AGENT_UNAVAILABLE" || NATIVE_OPERATION_ERROR_CODES.has(document.errorCode)) && Number.isInteger(document.downloadId) && document.stagingPath && document.actualFilename && await completedDownloadStillAvailable(document)) {
    await documentStore.update(document.key, {status: STATUS.DOWNLOADING});
    await handleDownloadChanged({id:document.downloadId,state:{current:"complete"}});
    const latest = await documentStore.get(document.key);
    if (latest?.status === STATUS.VERIFIED) return {queued:false, revalidated:true};
    if (latest?.status !== STATUS.REVIEW_REQUIRED) return {queued:false, revalidated:false};
  }
  const now = new Date().toISOString();
  const code = document.errorCode || null;
  const strategy = ["DOWNLOAD_START_FAILED","NATIVE_DOWNLOAD_NOT_CORRELATED","CLICK_DOWNLOAD_TIMEOUT"].includes(code)
    ? "fallback_fast"
    : (code === "HTML_RESPONSE" || isIncompleteContentError(code) ? "fresh_rebind" : null);
  await documentStore.update(document.key, {
    status: STATUS.RETRY_WAIT,
    attempts: 0,
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: now,
    retryQueuedAt: now,
    retryClass: "manual_review",
    // A user explicitly asked for this review item to run now. Mark it as
    // priority retry so it cannot sit behind unrelated primary inventory.
    manualRetryPriority: true,
    retryPriority: 0,
    retryStrategy: strategy,
    retryStrategyHistory: strategy ? [strategy] : [],
    retryExhausted: false,
    attemptStrategy: null,
    attemptRecoveryClass: null,
    htmlRecoveryAttempts: 0,
    manualRetryRequestedAt: now,
    errorCode: code,
    errorMessage: document.errorMessage || null,
    warnings: Array.isArray(document.warnings) ? document.warnings : []
  });
  return {queued:true, revalidated:false};
}

async function expediteDeferredRetry(document) {
  if (!document || document.status !== STATUS.RETRY_WAIT) return {expedited:false};
  const now = new Date().toISOString();
  await documentStore.update(document.key, {
    retryAt: now,
    manualRetryPriority: true,
    retryPriority: 0,
    manualRetryRequestedAt: now,
    manualRetryPreviousAt: document.retryAt || null,
    manualRetryRequestCount: Number(document.manualRetryRequestCount || 0) + 1
  });
  return {expedited:true};
}

export async function retrySelectedProblems(sessionId, keys = [], {retryAll = false, queue = "review"} = {}) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error("İş bulunamadı.");
  const targetQueue = queue === "deferred" ? "deferred" : "review";
  const requested = new Set((Array.isArray(keys) ? keys : []).map(String));
  let candidates;

  if (targetQueue === "deferred") {
    if (retryAll) {
      candidates = await documentStore.bySessionStatus(sessionId, STATUS.RETRY_WAIT);
    } else {
      if (!requested.size) throw new Error("Şimdi yeniden denenecek evrak seçilmedi.");
      const rows = await Promise.all([...requested].slice(0, 500).map((key) => documentStore.get(key)));
      candidates = rows.filter((row) => row?.sessionId === sessionId && row.status === STATUS.RETRY_WAIT);
    }
    let expedited = 0;
    for (const document of candidates) {
      const result = await expediteDeferredRetry(document);
      if (result.expedited) expedited += 1;
    }
    await recordDiagnosticEvent(sessionId, "MANUAL_DEFERRED_RETRY_EXPEDITED", {
      selected: candidates.length,
      expedited,
      whileRunning: session.status === SESSION_STATUS.RUNNING
    });
    if (session.status === SESSION_STATUS.RUNNING && expedited > 0) {
      await scheduleQueueWake(sessionId, Date.now() + 100);
      void runNextQueueItem();
    }
    return {
      session: await getSessionSummary(sessionId),
      queued: 0,
      expedited,
      revalidated: 0,
      selected: candidates.length,
      queue: targetQueue,
      runsAfterActiveDownload: session.status === SESSION_STATUS.RUNNING
    };
  }

  if (retryAll) {
    const [review, failed] = await Promise.all([
      documentStore.bySessionStatus(sessionId, STATUS.REVIEW_REQUIRED),
      documentStore.bySessionStatus(sessionId, STATUS.FAILED)
    ]);
    candidates = [...review, ...failed];
  } else {
    if (!requested.size) throw new Error("Yeniden denenecek evrak seçilmedi.");
    const rows = await Promise.all([...requested].slice(0, 500).map((key) => documentStore.get(key)));
    candidates = rows.filter((row) => row?.sessionId === sessionId && [STATUS.REVIEW_REQUIRED, STATUS.FAILED].includes(row.status));
  }
  let queued = 0;
  let revalidated = 0;
  for (const document of candidates) {
    const result = await queueManualReviewRetry(document);
    if (result.queued) queued += 1;
    if (result.revalidated) revalidated += 1;
  }
  await recordDiagnosticEvent(sessionId, "MANUAL_REVIEW_RETRY_QUEUED", {
    selected: candidates.length,
    queued,
    revalidated,
    whileRunning: session.status === SESSION_STATUS.RUNNING
  });
  if (session.status === SESSION_STATUS.RUNNING) {
    await scheduleQueueWake(sessionId, Date.now() + 250);
    void runNextQueueItem();
  }
  return {
    session: await getSessionSummary(sessionId),
    queued,
    expedited: 0,
    revalidated,
    selected: candidates.length,
    queue: targetQueue,
    deferredBehindPrimary: session.status === SESSION_STATUS.RUNNING
  };
}

export async function retryFailed(sessionId, tabId = null) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error('İş bulunamadı. Önce sayfayı tarayın.');
  if (session.identitySchemaVersion !== 3) throw new Error('Eski kuyruk için önce Sayfayı tara ile yeni envanter oluşturun.');
  if ([SESSION_STATUS.RUNNING,SESSION_STATUS.RECOVERING].includes(session.status)) throw new Error('Önce mevcut işlemi duraklatın.');
  const agent = await pingNativeAgent();
  if (!agent.ok) throw new Error("Yerel doğrulama ajanına ulaşılamadı. Ajanı kurup Chrome'u yeniden açın.");
  const now = new Date().toISOString();
  const wasLoginResume = session.status === SESSION_STATUS.PAUSED && session.pauseReason === "LOGIN_REQUIRED";
  const needsRuntimeRefresh = session.status === SESSION_STATUS.PAUSED;
  const binding = await rebindSessionTab(sessionId, tabId, {refreshRuntime: needsRuntimeRefresh});
  if (needsRuntimeRefresh) {
    if (!bindingIsComplete(binding)) {
      await pauseForUnsafeBinding(sessionId, binding);
      throw new Error(`Mevcut iş sıfırlanmadı. ${binding.bound}/${binding.bound + binding.unmatched} bekleyen kayıt açık UYAP ağacıyla eşleşti; ${binding.unmatched} kayıt güvenli eşleşmedi. Bağlantıyı kontrol edin.`);
    }
    await repairMassDownloadStartFailures(sessionId);
  } else if (!await sessionPageReady(await sessionStore.get(sessionId))) {
    throw new Error("Kuyruğun ait olduğu dava/Evrak ekranı hazır değil.");
  }
  const documents = await documentStore.bySession(sessionId);
  for (const document of documents) {
    if (document.status === STATUS.REVIEW_REQUIRED &&
        (document.errorCode === 'NATIVE_AGENT_UNAVAILABLE' || NATIVE_OPERATION_ERROR_CODES.has(document.errorCode)) &&
        Number.isInteger(document.downloadId) && document.stagingPath && document.actualFilename && await completedDownloadStillAvailable(document)) {
      // Retry validation/commit of the already-downloaded file before redownloading.
      await documentStore.update(document.key,{status:STATUS.DOWNLOADING});
      await handleDownloadChanged({id:document.downloadId,state:{current:'complete'}});
      if ((await documentStore.get(document.key))?.status === STATUS.REVIEW_REQUIRED) return getSessionSummary(sessionId);
      continue;
    }
    if ([STATUS.FAILED, STATUS.REVIEW_REQUIRED].includes(document.status)) {
      const manualRetryStrategy = ["DOWNLOAD_START_FAILED","NATIVE_DOWNLOAD_NOT_CORRELATED","CLICK_DOWNLOAD_TIMEOUT"].includes(document.errorCode)
        ? "fallback_fast"
        : (isIncompleteContentError(document.errorCode) ? "fresh_rebind" : null);
      await documentStore.update(document.key, {
        status: STATUS.QUEUED,
        attempts: 0,
        downloadId: null,
        attemptId: null,
        stagingPath: null,
        retryAt: null,
        retryQueuedAt: null,
        retryClass: null,
        retryStrategy: manualRetryStrategy,
        retryStrategyHistory: manualRetryStrategy ? [manualRetryStrategy] : [],
        retryExhausted: false,
        attemptStrategy: null,
        errorCode: null,
        errorMessage: null,
        warnings: []
      });
    }
  }
  await compactOperationalAlarms();
  await sessionStore.update(sessionId, {
    status: SESSION_STATUS.RUNNING,
    pauseReason: null,
    pausedAt: null,
    lastHeartbeatAt: now,
    heartbeatFailures: 0,
    heartbeatWarning: null,
    lastHeartbeatError: null,
    loginRequiredAt: null,
    resumeAfterLogin: false,
    loginNotificationShown: false,
    loginNotificationFallback: false,
    loginNotificationError: null,
    sessionGuardStatus: "HEALTHY",
    recoveryCount: 0,
    healthyVerifiedSinceRecovery: 0,
    consecutiveStartFailures: 0,
    agentVersion: agent.version,
    agentAvailable: true,
    finalRecoveryActive: false,
    finalRecoveryPass: 0,
    finalRecoveryQueued: 0,
    finalRecoveryReason: null,
    finalRecoveryBlockedReason: null,
    finalRecoveryExhausted: false
  });
  await clearLoginRequiredNotification(sessionId);
  await setSessionGuardBadge(false);
  await scheduleHeartbeat(sessionId);
  void runNextQueueItem();
  return getSessionSummary(sessionId);
}


export async function recoverStaleAwaitingBrowser(sessionId) {
  const document = await documentStore.firstBySessionStatus(sessionId, STATUS.AWAITING_BROWSER);
  if (!document) return {requeued:false, recovered:false};

  const pending = await getPendingDownload();
  if (pending?.sessionId === sessionId && pending.key === document.key && pending.attemptId === document.attemptId) {
    const recovered = await recoverRecentNativeDownload(document, pending);
    const latest = await documentStore.get(document.key);
    if (recovered || latest?.status !== STATUS.AWAITING_BROWSER) {
      return {requeued:false, recovered:true};
    }
  }

  if (pending?.sessionId === sessionId && (!pending.key || pending.key === document.key)) {
    await clearPendingDownload();
  }
  await clearOperationalAlarm(clickTimeoutAlarmName(sessionId));
  await documentStore.update(document.key, {
    status: STATUS.QUEUED,
    attempts: Math.max(0, (document.attempts || 1) - 1),
    downloadId: null,
    attemptId: null,
    stagingPath: null,
    retryAt: null,
    retryQueuedAt: null,
    retryClass: null,
    retryStrategy: null,
    retryStrategyHistory: [],
    retryExhausted: false,
    attemptStrategy: null,
    startedAt: null,
    errorCode: null,
    errorMessage: null
  });
  return {requeued:true, recovered:false};
}

export async function handleSessionTabUnavailable(tabId) {
  const sessionId = await getActiveSessionId();
  if (!sessionId || !Number.isInteger(tabId)) return false;
  const session = await sessionStore.get(sessionId);
  if (!session || session.tabId !== tabId || ![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) return false;
  await recoverStaleAwaitingBrowser(sessionId);
  await clearQueueControlAlarms(sessionId);
  await sessionStore.update(sessionId, {
    tabId: null,
    status: SESSION_STATUS.RECOVERING,
    pauseReason: "SOURCE_PAGE_NOT_READY",
    recoveryStartedAt: session.recoveryStartedAt || new Date().toISOString(),
    resumeAfterRecovery: true,
    sessionGuardStatus: "SOURCE_UNAVAILABLE",
    lastSourceLossAt: new Date().toISOString(),
    lastSourceLossReason: "TAB_CLOSED"
  });
  await recordDiagnosticEvent(sessionId, "SOURCE_TAB_LOST", {reason: "TAB_CLOSED"});
  await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 3_000});
  return true;
}

export async function recoverRunningSessionAfterNavigation(tabId) {
  const sessionId = await getActiveSessionId();
  if (!sessionId || !Number.isInteger(tabId)) return false;
  const session = await sessionStore.get(sessionId);
  if (!session || ![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) return false;
  if (session.status === SESSION_STATUS.RUNNING && session.tabId !== tabId) return false;

  const ready = await sessionPageReady(session, tabId).catch(() => false);
  if (!ready) {
    if (session.status === SESSION_STATUS.RUNNING && session.tabId === tabId) {
      const probe = await probeSessionHealth(session, tabId);
      if (probe.loginRequired) {
        await pauseForLoginRequired(session, {source: "navigation", detail: probe.reason || "LOGIN_REQUIRED"});
        return false;
      }
      await recoverStaleAwaitingBrowser(sessionId);
      await clearQueueControlAlarms(sessionId);
      await sessionStore.update(sessionId, {
        status: SESSION_STATUS.RECOVERING,
        pauseReason: "SOURCE_PAGE_NOT_READY",
        recoveryStartedAt: session.recoveryStartedAt || new Date().toISOString(),
        resumeAfterRecovery: true,
        sessionGuardStatus: "SOURCE_UNAVAILABLE"
      });
      await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 800});
    }
    return false;
  }

  const probe = await probeSessionHealth(session, tabId);
  if (probe.loginRequired) {
    await pauseForLoginRequired(session, {source: "navigation_ready_probe", detail: probe.reason || "LOGIN_REQUIRED"});
    return false;
  }
  if (!probe.healthy) return false;

  const binding = await refreshSessionRuntimeBindings(sessionId, tabId, {activeBatchOnly: Boolean(session.largeCaseMode)});
  if (!bindingIsComplete(binding)) {
    await pauseForUnsafeBinding(sessionId, binding);
    return false;
  }

  await recoverStaleAwaitingBrowser(sessionId);
  await repairMassDownloadStartFailures(sessionId);
  await sessionStore.update(sessionId, {
    tabId,
    status: SESSION_STATUS.RUNNING,
    pauseReason: null,
    pausedAt: null,
    loginRequiredAt: null,
    resumeAfterLogin: false,
    loginNotificationShown: false,
    loginNotificationFallback: false,
    loginNotificationError: null,
    recoveryStartedAt: null,
    queueError: null,
    sessionGuardStatus: "HEALTHY"
  });
  await setSessionGuardBadge(false);
  await recordDiagnosticEvent(sessionId, "SOURCE_RECOVERED", {
    bound: binding.bound || 0,
    scanned: binding.scanned || 0
  });
  await scheduleHeartbeat(sessionId);
  await scheduleQueueWake(sessionId, Date.now() + 250);
  void runNextQueueItem();
  return true;
}

export async function getSessionSummary(sessionId) {
  if (!sessionId) sessionId = await getActiveSessionId();
  if (!sessionId) return null;
  const session = await sessionStore.get(sessionId);
  if (!session) return null;
  const statuses = Object.values(STATUS);
  const values = await Promise.all(
    statuses.map((status) => documentStore.countBySessionStatus(sessionId, status))
  );
  const counts = Object.fromEntries(
    statuses.map((status, index) => [status, values[index]])
  );
  const actualTotal = values.reduce((sum, value) => sum + Number(value || 0), 0);
  if (Number(session.total || 0) !== actualTotal) {
    // Self-heal legacy inflated totals (for example 390 -> 780) using the
    // actual number of IndexedDB document rows. This is safe because every
    // source record has exactly one status row.
    await sessionStore.update(sessionId, {
      total: actualTotal,
      totalReconciledAt: new Date().toISOString(),
      totalReconciledFrom: Number(session.total || 0),
      totalReconciledTo: actualTotal
    });
    session.total = actualTotal;
  }
  const warningCount = await documentStore.countWarnings(sessionId);
  const totalProblems = Number(counts[STATUS.FAILED] || 0) + Number(counts[STATUS.REVIEW_REQUIRED] || 0);
  let problemBreakdown = {
    incompleteDownloads: 0, htmlResponses: 0, other: totalProblems,
    truncatedPdf: 0, truncatedTiff: 0, sizeMismatch: 0, notComplete: 0
  };
  // Browser runtime has IndexedDB. A few isolated unit tests deliberately run
  // without it; keep summary rendering testable without forcing a full DB mock.
  if (typeof globalThis.indexedDB !== "undefined") {
    const problemStatuses = [STATUS.FAILED, STATUS.REVIEW_REQUIRED];
    const countProblemCode = async (errorCode) => {
      const values = await Promise.all(problemStatuses.map((status) =>
        documentStore.countBySessionStatusError(sessionId, status, errorCode)
      ));
      return values.reduce((sum, value) => sum + Number(value || 0), 0);
    };
    const [truncatedPdf, truncatedTiff, sizeMismatch, notComplete, htmlResponse, htmlResponseReceived] = await Promise.all([
      countProblemCode("TRUNCATED_PDF"),
      countProblemCode("TRUNCATED_TIFF"),
      countProblemCode("DOWNLOAD_SIZE_MISMATCH"),
      countProblemCode("DOWNLOAD_NOT_COMPLETE"),
      countProblemCode("HTML_RESPONSE"),
      countProblemCode("HTML_RESPONSE_RECEIVED")
    ]);
    const incompleteDownloads = truncatedPdf + truncatedTiff + sizeMismatch + notComplete;
    const htmlResponses = htmlResponse + htmlResponseReceived;
    problemBreakdown = {
      incompleteDownloads,
      htmlResponses,
      other: Math.max(0, totalProblems - incompleteDownloads - htmlResponses),
      truncatedPdf, truncatedTiff, sizeMismatch, notComplete
    };
  }
  const performance = summarizePerformance(session.performance || {});
  const currentFrameClient = getFrameClientStats();
  const frameBaseline = session.frameClientBaseline || null;
  const sameFrameScope = Boolean(frameBaseline?.scopeId && frameBaseline.scopeId === currentFrameClient.scopeId);
  const runtimeDiagnostics = {
    nativeBridge: getNativeBridgeStats(),
    frameClient: currentFrameClient,
    frameClientSession: {
      scope: sameFrameScope ? "session_delta" : "current_service_worker",
      scopeId: currentFrameClient.scopeId || null,
      activeInjections: Number(currentFrameClient.activeInjections || 0),
      injectionCount: sameFrameScope ? Math.max(0, Number(currentFrameClient.injectionCount || 0) - Number(frameBaseline.injectionCount || 0)) : Number(currentFrameClient.injectionCount || 0),
      injectionFailures: sameFrameScope ? Math.max(0, Number(currentFrameClient.injectionFailures || 0) - Number(frameBaseline.injectionFailures || 0)) : Number(currentFrameClient.injectionFailures || 0),
      maxConcurrentInjections: Number(currentFrameClient.maxConcurrentInjections || 0)
    },
    completingDownloads: completingDownloads.size
  };
  let largeCase = null;
  if (session.largeCaseMode) {
    const currentBatch = Math.max(1, Number(session.largeCaseCurrentBatch || 1));
    const totalBatches = Math.max(1, Number(session.largeCaseTotalBatches || 1));
    const batchStatuses = await Promise.all(statuses.map((status) =>
      documentStore.countBySessionBatchStatus(sessionId, currentBatch, status)
    ));
    const batchCounts = Object.fromEntries(statuses.map((status, index) => [status, batchStatuses[index]]));
    const batchDone = Number(batchCounts[STATUS.VERIFIED] || 0) + Number(batchCounts[STATUS.SKIPPED] || 0);
    const batchTotal = batchStatuses.reduce((sum, value) => sum + Number(value || 0), 0);
    largeCase = {
      enabled: true,
      currentBatch,
      totalBatches,
      completedBatches: Math.max(0, Number(session.largeCaseCompletedBatches || 0)),
      batchSize: Number(session.largeCaseBatchSize || LARGE_CASE_BATCH_SIZE),
      batchTotal,
      batchDone,
      batchPercent: batchTotal ? Math.min(100, Math.round(batchDone / batchTotal * 100)) : 0,
      batchCounts
    };
  }
  return { ...session, performance, runtimeDiagnostics, counts, warningCount, problemBreakdown, largeCase };
}


const KURUM_OPEN_CONTEXT_CACHE_KEY = 'kurumOpenContextCacheV1';

function sanitizePersistedKurumOpenContext(selection) {
  const caseId = String(selection?.caseId || selection?.openContext?.dosyaNo || '').replace(/\s+/g,'').replace('-', '/');
  const raw = selection?.openContext;
  if (!/^20\d{2}\/\d{1,10}$/.test(caseId) || !raw || typeof raw !== 'object') return null;
  const clean = (value,max=180) => String(value ?? '').replace(/\s+/g,' ').trim().slice(0,max);
  const dosyaId = clean(raw.dosyaId,240);
  if (!dosyaId) return null;
  return {
    caseId,
    courtName:clean(selection?.courtName || raw.birimAdi || 'UYAP Kurum',180),
    openContext:{
      dosyaId, dosyaNo:caseId, birimId:clean(raw.birimId,100),
      birimAdi:clean(raw.birimAdi || selection?.courtName || 'UYAP Kurum',180),
      dosyaTurKod:clean(raw.dosyaTurKod,60), birimTuru1:clean(raw.birimTuru1,80),
      birimTuru2:clean(raw.birimTuru2,80), birimTuru3:clean(raw.birimTuru3,80),
      kurumNo:clean(raw.kurumNo,100)
    },
    capturedAt: selection?.capturedAt || new Date().toISOString()
  };
}

export async function persistKurumCaseOpenContext(selection, tabId=null) {
  const safe = sanitizePersistedKurumOpenContext(selection);
  if (!safe) return {updatedSessions:0,cached:false};
  const sessions = await sessionStore.all();
  let updatedSessions = 0;
  for (const session of sessions || []) {
    if (String(session?.sourceSystem || '').toUpperCase() !== 'UYAP_KURUM') continue;
    if (String(session?.caseId || '') !== safe.caseId) continue;
    const sourceContext = {
      ...(session.sourceContext || {}),
      caseId:safe.caseId,
      kurumCaseOpenContext:safe.openContext,
      kurumCaseOpenContextCapturedAt:safe.capturedAt
    };
    await sessionStore.update(session.sessionId,{
      sourceContext,
      kurumCaseOpenContextCapturedAt:safe.capturedAt,
      kurumCaseOpenContextTabId:Number.isInteger(tabId)?tabId:(session.kurumCaseOpenContextTabId ?? null)
    });
    updatedSessions += 1;
  }

  try {
    const stored = await chrome.storage.local.get(KURUM_OPEN_CONTEXT_CACHE_KEY);
    const cache = stored?.[KURUM_OPEN_CONTEXT_CACHE_KEY] && typeof stored[KURUM_OPEN_CONTEXT_CACHE_KEY] === 'object'
      ? {...stored[KURUM_OPEN_CONTEXT_CACHE_KEY]} : {};
    cache[safe.caseId] = safe;
    const ordered = Object.entries(cache).sort((a,b)=>Date.parse(b[1]?.capturedAt||0)-Date.parse(a[1]?.capturedAt||0)).slice(0,60);
    await chrome.storage.local.set({[KURUM_OPEN_CONTEXT_CACHE_KEY]:Object.fromEntries(ordered)});
  } catch {}
  return {updatedSessions,cached:true,caseId:safe.caseId};
}

export async function getCachedKurumCaseOpenContext(caseId) {
  const normalized = String(caseId || '').replace(/\s+/g,'').replace('-', '/');
  if (!normalized) return null;
  try {
    const stored = await chrome.storage.local.get(KURUM_OPEN_CONTEXT_CACHE_KEY);
    const safe = sanitizePersistedKurumOpenContext(stored?.[KURUM_OPEN_CONTEXT_CACHE_KEY]?.[normalized]);
    return safe?.openContext || null;
  } catch { return null; }
}


export async function listArchiveRegistry({ sessionId = null, downloadsRoot = null, limit = 10000, forceRefresh = false } = {}) {
  let root = downloadsRoot;
  if (!root && sessionId) {
    const session = await sessionStore.get(sessionId);
    root = session?.downloadsRoot || null;
  }
  if (!root) {
    const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
    root = stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  }
  if (!root) {
    const sessions = await sessionStore.all();
    const withRoot = sessions
      .filter((session) => session?.downloadsRoot)
      .sort((a, b) => Date.parse(b.updatedAt || b.createdAt || 0) - Date.parse(a.updatedAt || a.createdAt || 0));
    root = withRoot[0]?.downloadsRoot || null;
  }
  const safeLimit = Math.min(20000, Math.max(1, Number(limit) || 10000));
  const rootKey = String(root || "");
  const cachedResult = () => {
    const cached = archiveRegistryCache;
    if (!cached || Date.now() - cached.atMs > ARCHIVE_REGISTRY_CACHE_TTL_MS) return null;
    if (rootKey !== cached.rootKey && rootKey !== cached.resolvedRootKey) return null;
    const documents = Array.isArray(cached.result?.documents) ? cached.result.documents : [];
    if (cached.limit < safeLimit && documents.length >= cached.limit) return null;
    return {...cached.result, documents:documents.slice(0, safeLimit)};
  };
  const immediate = forceRefresh ? null : cachedResult();
  if (immediate) return immediate;

  // Popup ve Workspace aynı anda istekte bulunduğunda pahalı _archive.db
  // taramalarını sıraya yığma. Tek taramaya katıl; gerekirse ardından yalnız
  // bir daha geniş tarama başlat.
  while (archiveRegistryInFlight) {
    await archiveRegistryInFlight.promise;
    if (!forceRefresh) {
      const joined = cachedResult();
      if (joined) return joined;
    }
  }

  const request = {rootKey, limit:safeLimit, promise:null};
  request.promise = (async () => {
    // Native Agent arşiv kökünü, tarayıcı kaydı sıfırlanmış olsa da keşfedebilir.
    const result = await askNativeAgent({action:"list_archive_registry", downloadsRoot:root, limit:safeLimit});
    if (!result?.ok) throw new Error(result?.error || "Yerel arşiv kayıtları okunamadı.");
    archiveRegistryCache = {
      rootKey,
      resolvedRootKey:String(result.downloadsRoot || root || ""),
      limit:safeLimit,
      atMs:Date.now(),
      result
    };
    if (result.archiveExists && result.downloadsRoot) await chrome.storage.local.set({[LAST_DOWNLOADS_ROOT_KEY]:result.downloadsRoot});
    return {...result, documents:Array.isArray(result.documents)?result.documents.slice(0,safeLimit):[]};
  })().finally(()=>{if(archiveRegistryInFlight===request)archiveRegistryInFlight=null;});
  archiveRegistryInFlight = request;
  return request.promise;
}

export async function selectExistingArchive() {
  const activeId = await getActiveSessionId();
  const active = activeId ? await sessionStore.get(activeId) : null;
  if (active && [SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(active.status)) {
    throw new Error("Çalışan iş sırasında arşiv kökü değiştirilemez. Önce işi duraklatın.");
  }
  const result = await askNativeAgent({action:"select_existing_archive", limit:10000});
  if (!result?.ok) throw new Error(result?.error || "Mevcut arşiv seçilemedi.");
  if (result.cancelled) return result;
  if (!result.downloadsRoot || !result.archiveRoot) throw new Error("Seçilen arşiv kökü doğrulanamadı.");
  await chrome.storage.local.set({
    [LAST_DOWNLOADS_ROOT_KEY]: result.downloadsRoot,
    [PORTABLE_RESUME_ENABLED_KEY]: true
  });
  invalidateArchiveRegistryCache();
  const sessionId = activeId;
  const session = active;
  if (session && ![SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) {
    await sessionStore.update(sessionId, {
      downloadsRoot: result.downloadsRoot,
      portableResumeEnabled: true,
      portableResumeSelectedAt: new Date().toISOString()
    });
  }
  return result;
}

export async function getSelectedArchive() {
  return askNativeAgent({action:"get_selected_archive"});
}

export async function listSessions({ limit = 100 } = {}) {
  const safeLimit = Math.min(250, Math.max(1, Number(limit) || 100));
  const sessions = await sessionStore.all();
  return sessions
    .sort((a, b) => Date.parse(b.updatedAt || b.createdAt || 0) - Date.parse(a.updatedAt || a.createdAt || 0))
    .slice(0, safeLimit)
    .map((session) => ({
      sessionId: session.sessionId,
      sourceSystem: session.sourceSystem || "UYAP",
      institutionName: session.institutionName || session.courtName || null,
      courtName: session.courtName || null,
      caseId: session.caseId || null,
      tabId: Number.isInteger(session.tabId) ? session.tabId : null,
      status: session.status || null,
      pauseReason: session.pauseReason || null,
      total: Number(session.total || 0),
      warningCount: Number(session.warningCount || 0),
      archiveStatus: session.archiveStatus || null,
      unresolved: Number(session.unresolved || 0),
      createdAt: session.createdAt || null,
      updatedAt: session.updatedAt || null,
      finalIntegrity: session.finalIntegrity ? {
        gatePassed: session.finalIntegrity.gatePassed === true,
        partCount: Number(session.finalIntegrity.partCount || 0),
        verifiedPartCount: Number(session.finalIntegrity.verifiedPartCount || 0),
        missingCount: Number(session.finalIntegrity.missingCount || 0)
      } : null
    }));
}

export async function listResumableSessions({ limit = 8, excludeSessionId = null } = {}) {
  const safeLimit = Math.min(20, Math.max(1, Number(limit) || 8));
  const sessions = (await sessionStore.all())
    .filter((session) => session?.sessionId && session.sessionId !== excludeSessionId)
    .sort((a, b) => Date.parse(b.updatedAt || b.createdAt || 0) - Date.parse(a.updatedAt || a.createdAt || 0));

  const result = [];
  for (const session of sessions) {
    if (result.length >= safeLimit) break;
    const documents = await documentStore.bySession(session.sessionId);
    if (!documents.length) continue;
    const verified = documents.filter((document) => document.status === STATUS.VERIFIED).length;
    const skipped = documents.filter((document) => document.status === STATUS.SKIPPED).length;
    const review = documents.filter((document) => [STATUS.REVIEW_REQUIRED, STATUS.FAILED].includes(document.status)).length;
    const deferred = documents.filter((document) => document.status === STATUS.RETRY_WAIT).length;
    const progress = verified + skipped;
    const total = documents.length;
    const remaining = Math.max(0, total - progress);
    const documentsComplete = total > 0 && remaining === 0 && review === 0 && deferred === 0;
    result.push({
      sessionId: session.sessionId,
      sourceSystem: session.sourceSystem || "UYAP",
      institutionName: session.institutionName || session.courtName || null,
      courtName: session.courtName || null,
      caseId: session.caseId || null,
      tabId: Number.isInteger(session.tabId) ? session.tabId : null,
      status: session.status || null,
      pauseReason: session.pauseReason || null,
      total,
      verified,
      skipped,
      progress,
      remaining,
      review,
      deferred,
      percent: total ? Math.min(100, Math.round(progress / total * 100)) : 0,
      updatedAt: session.updatedAt || session.createdAt || null,
      resumable: !documentsComplete && remaining > 0,
      completed: documentsComplete,
      archiveComplete: session.status === SESSION_STATUS.COMPLETED && session.archiveStatus === "VERIFIED_COMPLETE",
      completionLabel: documentsComplete ? "Tamamlandı" : "Yarım kaldı"
    });
  }
  return result;
}

function sessionScannedWithoutSourceIds(session) {
  const entries = Array.isArray(session?.inventoryEvidence) ? session.inventoryEvidence : [];
  if (!entries.length) return false;
  const merged = entries.find((item) => item?.scope === "cross-frame-merge" && Number(item?.uniqueDocuments || 0) > 0);
  if (merged) return Number(merged.sourceIdDocuments || 0) === 0;
  const populated = entries.filter((item) => Number(item?.uniqueDocuments || 0) > 0);
  return populated.length > 0 && populated.every((item) => Number(item?.sourceIdDocuments || 0) === 0);
}

function sourceDocumentIdOriginForFinalization(session, document) {
  if (document?.sourceDocumentIdOrigin) return document.sourceDocumentIdOrigin;
  if (!document?.sourceDocumentId) return null;
  // v2.7.15 and earlier did not persist provenance. If the persisted scan
  // evidence explicitly proves that zero source IDs existed at scan time, any
  // later numeric ID must have been learned during download correlation. This
  // lets a verified legacy archive be repackaged without weakening unknown
  // provenance cases.
  if (sessionScannedWithoutSourceIds(session)) return "legacy-post-scan-inferred";
  return null;
}

function nativeFinalizationDocument(document, session = null) {
  return {
    documentId: document.documentId,
    sourceSystem: document.sourceSystem || "UYAP",
    sourceContext: document.sourceContext || null,
    institutionName: document.institutionName || document.courtName || null,
    courtName: document.courtName || null,
    caseId: document.caseId || null,
    folderNames: document.folderNames || [],
    documentTitle: document.documentTitle || null,
    documentDate: document.documentDate || null,
    sourceDocumentId: document.sourceDocumentId || null,
    sourceDocumentIdOrigin: sourceDocumentIdOriginForFinalization(session, document),
    identityMethod: document.identityMethod || null,
    sourceIdentityHash: document.sourceIdentityHash || null,
    sourceFingerprint: document.sourceFingerprint || sourceFingerprint(document),
    targetPath: document.targetPath || null,
    sha256: document.sha256 || null,
    actualSize: document.actualSize || null,
    contentKind: document.contentKind || null,
    htmlValidationMode: document.htmlValidationMode || null,
    contentDetectedExtension: document.contentDetectedExtension || document.extension || null,
    completedAt: document.completedAt || null
  };
}

export async function openArchiveFolder(sessionId) {
  const session = sessionId ? await sessionStore.get(sessionId) : null;
  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  let downloadsRoot = session?.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  if (!downloadsRoot) {
    const registry = await listArchiveRegistry({sessionId, limit:1});
    downloadsRoot = registry?.archiveExists ? registry.downloadsRoot : null;
  }
  if (!downloadsRoot) throw new Error("UYAP_Arsiv klasörü bulunamadı.");
  const result = await askNativeAgent({action:"open_archive_folder", downloadsRoot});
  if (!result?.ok) throw new Error(result?.error || "Arşiv klasörü açılamadı.");
  return result;
}

export async function openFinalPackageFolder(sessionId) {
  const session = sessionId ? await sessionStore.get(sessionId) : null;
  if (!session) throw new Error("Arşiv işi bulunamadı.");
  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  const downloadsRoot = session.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  if (!downloadsRoot) throw new Error("Arşiv kökü bulunamadı.");
  const archivePartPath = Array.isArray(session.archiveParts) ? session.archiveParts.find(part => part?.path)?.path : null;
  const packagePath = session.finalPackagePath || session.zipPath || archivePartPath || null;
  if (!packagePath) throw new Error("Bu iş için final paket yolu kayıtlı değil. Final paketi yeniden oluşturun.");
  const result = await askNativeAgent({action:"open_final_package_folder", downloadsRoot, packagePath});
  if (!result?.ok) throw new Error(result?.error || "Final paket klasörü açılamadı.");
  return result;
}

export async function repackageVerifiedArchive(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session) throw new Error("Arşiv işi bulunamadı.");
  if ([SESSION_STATUS.RUNNING, SESSION_STATUS.RECOVERING].includes(session.status)) {
    throw new Error("Yerel yeniden paketleme için önce aktif işi duraklatın.");
  }
  if ((await getPendingDownload()) || await documentStore.hasActiveDownload(sessionId)) {
    throw new Error("Devam eden indirme tamamlanmadan yerel yeniden paketleme yapılamaz.");
  }
  let documents = await documentStore.bySession(sessionId);
  let unresolved = documents.filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status));
  let terminalCount = documents.length - unresolved.length;
  if (!documents.length || terminalCount !== Number(session.total || documents.length) || unresolved.length) {
    throw new Error(`Yerel yeniden paketleme yalnız tüm kayıtlar sonuçlandıysa çalışır. Sonuçlanan ${terminalCount}/${Number(session.total || documents.length)}.`);
  }
  const agent = await pingNativeAgent();
  if (!agent.ok || agent.downloadReady !== true) {
    throw new Error(`Yerel ajan hazır değil: ${agent.error || agent.version || "bağlantı kurulamadı"}`);
  }
  const stored = await chrome.storage.local.get(LAST_DOWNLOADS_ROOT_KEY);
  const downloadsRoot = session.downloadsRoot || stored[LAST_DOWNLOADS_ROOT_KEY] || null;
  if (!downloadsRoot) throw new Error("Arşiv kökü bulunamadı. Önce Yerel Ajanı kontrol edin.");

  // A previous finalization attempt may have left native DB rows as
  // INTEGRITY_FAILED even though the immutable file on disk is still valid.
  // Reconcile every terminal source record against the physical archive before
  // packaging, then rebuild the package inventory from the refreshed state.
  const reconciliation = await syncSessionWithLocalArchive(sessionId, {downloadsRoot, includeTerminal:true});
  documents = await documentStore.bySession(sessionId);
  unresolved = documents.filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status));
  terminalCount = documents.length - unresolved.length;
  // RC15.9.13 could incorrectly demote already-validated HTML documents during
  // final packaging because the native packager lost the original validation
  // evidence. Allow only that exact poisoned state through to the new native
  // proof gate. Native still requires byte-identical path/size/SHA-256 evidence.
  const proofRecoverableHtml = unresolved.filter(isFinalizationProofRecoverableHtml);
  const onlyProofRecoverableHtml = unresolved.length > 0 && proofRecoverableHtml.length === unresolved.length;
  const packageReadyCount = terminalCount + proofRecoverableHtml.length;
  if (!documents.length || packageReadyCount !== Number(session.total || documents.length) || (unresolved.length && !onlyProofRecoverableHtml)) {
    throw new Error(`Yerel arşiv uzlaştırması sonrası tüm kayıtlar paketlemeye hazır değil. Sonuçlanan ${terminalCount}/${Number(session.total || documents.length)}.`);
  }
  const excludedDocuments = userExcludedDocuments(documents);
  const packageDocuments = finalizationDocuments(documents);

  await recordDiagnosticEvent(sessionId, "LOCAL_REPACKAGE_STARTED", {
    expected: session.total, packaged: packageDocuments.length, userExcluded: excludedDocuments.length,
    localMatched: Number(reconciliation?.matched || 0), localAmbiguous: Number(reconciliation?.ambiguous || 0),
    localUnmatched: Number(reconciliation?.unmatched || 0)
  });
  await sessionStore.update(sessionId, {
    finalizationInProgress: true,
    finalizationStartedAt: new Date().toISOString(),
    finalizationError: null,
    downloadsRoot
  });

  const exclusionOnly = allUserExcludedFinalization(documents, excludedDocuments);
  const finalization = exclusionOnly || await askNativeAgent({
    action: "finalize_archive",
    sessionId,
    downloadsRoot,
    expected: packageDocuments.length,
    sourceInventoryTotal: documents.length,
    userExcludedDocuments: excludedDocuments.map((d) => ({documentId:d.documentId, reason:d.skipReason, excludedAt:d.userExcludedAt || null})),
    documentIds: packageDocuments.map((document) => document.documentId),
    reviewRequired: 0,
    allowPartial: false,
    partSizeBytes: ARCHIVE_PART_TARGET_BYTES,
    maxDocumentsPerPart: ARCHIVE_PART_MAX_DOCUMENTS,
    documents: packageDocuments.map((document) => nativeFinalizationDocument(document, session))
  });

  if (finalization.integrity) {
    finalization.integrity.sourceInventoryTotal = documents.length;
    finalization.integrity.userExcludedCount = excludedDocuments.length;
  }
  const completed = Boolean(finalization.ok && finalization.archiveStatus === "VERIFIED_COMPLETE" && finalization.integrity?.gatePassed);
  if (completed && proofRecoverableHtml.length) {
    const repairedAt = new Date().toISOString();
    for (const document of proofRecoverableHtml) {
      await documentStore.update(document.key, {
        status: STATUS.VERIFIED,
        errorCode: null,
        errorMessage: null,
        retryAt: null,
        retryClass: null,
        retryExhausted: false,
        htmlRecoveryFinal: false,
        finalizationProofRecoveredAt: repairedAt
      });
    }
    await recordDiagnosticEvent(sessionId, "FINALIZATION_HTML_PROOF_RECOVERED", {documents:proofRecoverableHtml.length});
  }
  await sessionStore.update(sessionId, {
    status: completed ? SESSION_STATUS.COMPLETED : SESSION_STATUS.INCOMPLETE,
    archiveStatus: finalization.archiveStatus || "INCOMPLETE",
    partialArchiveAvailable: finalization.archiveStatus === "PARTIAL_WITH_ERRORS",
    unresolved: Number(finalization.unresolved || 0),
    finalPackagePath: finalization.finalPackagePath || null,
    manifestPath: finalization.manifestPath || null,
    zipPath: finalization.zipPath || null,
    manifestSha256: finalization.manifestSha256 || null,
    zipSha256: finalization.zipSha256 || null,
    archiveFormat: finalization.archiveFormat || null,
    archiveParts: finalization.archiveParts || [],
    partsManifestPath: finalization.partsManifestPath || null,
    partsManifestSha256: finalization.partsManifestSha256 || null,
    checksumsPath: finalization.checksumsPath || null,
    checksumsSha256: finalization.checksumsSha256 || null,
    finalIntegrity: finalization.integrity || null,
    userExcludedCount: excludedDocuments.length,
    finalizationError: completed ? null : (finalization.error || finalization.detail || "LOCAL_REPACKAGE_FAILED"),
    finalizationInProgress: false,
    finalRecoveryActive: false,
    finalRecoveryQueued: 0,
    finalRecoveryCompletedAt: completed ? new Date().toISOString() : null,
    pauseReason: null,
    pausedAt: null
  });
  await recordDiagnosticEvent(sessionId, "LOCAL_REPACKAGE_FINISHED", {
    ok: completed,
    archiveStatus: finalization.archiveStatus || "INCOMPLETE",
    gatePassed: Boolean(finalization.integrity?.gatePassed),
    partCount: Number(finalization.integrity?.partCount || 0),
    learnedDuplicateSourceDocumentCount: Number(finalization.integrity?.learnedDuplicateSourceDocumentCount || 0),
    blockingDuplicateSourceDocumentCount: Number(finalization.integrity?.blockingDuplicateSourceDocumentCount || 0)
  });
  const repackagedSession = await sessionStore.get(sessionId);
  if (repackagedSession?.status === SESSION_STATUS.COMPLETED) {
    await showArchiveCompletionNotification(repackagedSession);
  } else if (repackagedSession?.status === SESSION_STATUS.INCOMPLETE && Number(repackagedSession.unresolved || 0) > 0) {
    await showArchiveProblemNotification(repackagedSession);
  }
  await restoreArchiveDownloadUi();
  return getSessionSummary(sessionId);
}

export async function getReportData(sessionId) {
  const summary = await getSessionSummary(sessionId);
  if (!summary) return null;
  const documents = await documentStore.bySession(summary.sessionId);
  return { schemaVersion: 1, generatedAt: new Date().toISOString(), session: summary, documents };
}

async function diagnosticDocumentView(sessionId, document) {
  return {
    ref: await diagnosticReference(sessionId, document.documentId || document.key),
    sourceSystem: document.sourceSystem || 'UYAP',
    identityMethod: document.identityMethod || null,
    runtimeBindingMethod: document.runtimeBindingMethod || null,
    status: document.status || null,
    attempts: Number(document.attempts || 0),
    errorCode: document.errorCode || null,
    errorMessage: document.errorMessage ? sanitizeDiagnosticText(document.errorMessage, 320) : null,
    warnings: Array.isArray(document.warnings) ? document.warnings.slice(0, 12).map((item) => sanitizeDiagnosticText(item, 120)) : [],
    validationNotes: Array.isArray(document.validationNotes) ? document.validationNotes.slice(0, 12).map((item) => sanitizeDiagnosticText(item, 160)) : [],
    contentDetectedExtension: document.contentDetectedExtension || null,
    contentDetectionMode: document.contentDetectionMode || null,
    contentDetectionHint: document.contentDetectionHint || null,
    retryAt: document.retryAt || null,
    retryClass: document.retryClass || null,
    retryStrategy: document.retryStrategy || null,
    attemptStrategy: document.attemptStrategy || null,
    bindingMethod: document.bindingMethod || null,
    correlationMode: document.correlationMode || null,
    correlationCandidateCount: Number.isFinite(Number(document.correlationCandidateCount)) ? Number(document.correlationCandidateCount) : null,
    imageDetectedType: document.imageDetectedType || null,
    imageValidationMode: document.imageValidationMode || null,
    imageWidth: Number.isFinite(Number(document.imageWidth)) ? Number(document.imageWidth) : null,
    imageHeight: Number.isFinite(Number(document.imageHeight)) ? Number(document.imageHeight) : null,
    createdAt: document.discoveredAt || null,
    updatedAt: document.updatedAt || null,
    startedAt: document.startedAt || null,
    completedAt: document.completedAt || null,
    actualSize: Number.isFinite(Number(document.actualSize)) ? Number(document.actualSize) : null,
    browserFileSize: Number.isFinite(Number(document.browserFileSize)) ? Number(document.browserFileSize) : null,
    browserTotalBytes: Number.isFinite(Number(document.browserTotalBytes)) ? Number(document.browserTotalBytes) : null,
    observedInvalidSize: Number.isFinite(Number(document.observedInvalidSize)) ? Number(document.observedInvalidSize) : null,
    sourcePathRecoveryMode: document.sourcePathRecoveryMode || null,
    sourcePathRecoveryKnownRootExists: document.sourcePathRecoveryKnownRootExists === true,
    sourcePathRecoveryBrowserRootExists: document.sourcePathRecoveryBrowserRootExists === true,
    sourcePathRecoverySelectedRootExists: document.sourcePathRecoverySelectedRootExists === true,
    actualMime: document.actualMime || null,
    pdfValidatorMode: document.pdfValidatorMode || null,
    pdfTrailingBytes: Number.isFinite(Number(document.pdfTrailingBytes)) ? Number(document.pdfTrailingBytes) : null,
    pdfTrailingCoreHex: typeof document.pdfTrailingCoreHex === "string" && /^[0-9a-f]{0,512}$/i.test(document.pdfTrailingCoreHex) ? document.pdfTrailingCoreHex : null,
    watchdogLastBytesReceived: Number.isFinite(Number(document.watchdogLastBytesReceived)) ? Number(document.watchdogLastBytesReceived) : null,
    watchdogLastProgressAt: document.watchdogLastProgressAt || null,
    watchdogLastObservedAt: document.watchdogLastObservedAt || null,
    lateDuplicateSuppressed: Number(document.lateDuplicateSuppressed || 0),
    sessionLossCount: Number(document.sessionLossCount || 0)
  };
}

function diagnosticSourceEvidence(summary) {
  const entries = Array.isArray(summary?.inventoryEvidence) ? summary.inventoryEvidence : [];
  return entries.slice(0, 32).map((item) => {
    const base = {
      frameId: Number.isInteger(item?.frameId) ? item.frameId : null,
      scope: item?.scope || null,
      parser: item?.parser || null,
      sourceSystem: item?.sourceSystem || summary?.sourceSystem || 'UYAP',
      sourceAdapter: item?.sourceAdapter || item?.adapter || null,
      contentVersion: item?.contentVersion || null,
      inventoryScope: item?.inventoryScope || null,
      uniqueDocuments: Number(item?.uniqueDocuments || 0),
      sourceIdDocuments: Number(item?.sourceIdDocuments || 0)
    };
    const isKurum = base.sourceSystem === 'UYAP_KURUM' || /kurum/i.test(String(base.sourceAdapter || base.parser || ''));
    if (isKurum) {
      return {
        ...base,
        candidateRows: Number(item?.candidateRows || 0),
        candidateDownloadActions: Number(item?.candidateDownloadActions || 0),
        sourceTokenRows: Number(item?.sourceTokenRows || 0),
        tableRows: Number(item?.tableRows || 0),
        pageCount: Number(item?.pageCount || 1),
        declaredRowCount: Number(item?.declaredRowCount || 0),
        framesHint: Boolean(item?.framesHint),
        virtualViewportSignals: Number(item?.virtualViewportSignals || 0),
        nextPageSignals: Number(item?.nextPageSignals || 0),
        nextPageEnabledSignals: Number(item?.nextPageEnabledSignals || 0),
        frameworkHints: item?.frameworkHints || {}
      };
    }
    return {
      ...base,
      fileNodes: Number(item?.fileNodes || 0),
      folderNodes: Number(item?.folderNodes || 0),
      saveMenuItems: Number(item?.saveMenuItems || 0),
      rawDocuments: Number(item?.rawDocuments || 0),
      duplicateDocuments: Number(item?.duplicateDocuments || 0),
      duplicateSourceIds: Number(item?.duplicateSourceIds || 0)
    };
  });
}

export async function getDiagnosticData(sessionId = null) {
  const summary = await getSessionSummary(sessionId);
  if (!summary) throw new Error("Tanılama kaydı için aktif arşiv işi bulunamadı.");
  const documents = await documentStore.bySession(summary.sessionId);
  const problemStatuses = new Set([STATUS.FAILED, STATUS.REVIEW_REQUIRED, STATUS.RETRY_WAIT]);
  const activeStatuses = new Set([STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING, STATUS.COMPLETED]);
  const errorCounts = {};
  const statusCounts = {};
  for (const document of documents) {
    const status = String(document.status || "unknown");
    statusCounts[status] = Math.max(0, Number(statusCounts[status] || 0)) + 1;
    if (document.errorCode) errorCounts[document.errorCode] = Math.max(0, Number(errorCounts[document.errorCode] || 0)) + 1;
  }

  const problemDocuments = documents
    .filter((document) => problemStatuses.has(document.status) || document.errorCode)
    .sort((a, b) => Date.parse(b.updatedAt || b.completedAt || 0) - Date.parse(a.updatedAt || a.completedAt || 0))
    .slice(0, DIAGNOSTIC_PROBLEM_LIMIT);
  const activeDocuments = documents
    .filter((document) => activeStatuses.has(document.status))
    .slice(0, 20);
  const pending = await getPendingDownload().catch(() => null);
  const alarms = typeof chrome?.alarms?.getAll === "function" ? await chrome.alarms.getAll().catch(() => []) : [];
  const sessionAlarms = (alarms || [])
    .filter((alarm) => String(alarm?.name || "").includes(summary.sessionId))
    .map((alarm) => ({
      name: String(alarm.name).replace(summary.sessionId, "<session>"),
      scheduledTime: Number(alarm.scheduledTime || 0) || null,
      periodInMinutes: Number(alarm.periodInMinutes || 0) || null
    }));

  const persistedEvents = await diagnosticStore.bySession(summary.sessionId).catch(() => []);
  const eventTypeCounts = {};
  const eventErrorCounts = {};
  for (const event of persistedEvents) {
    eventTypeCounts[event.type] = Math.max(0, Number(eventTypeCounts[event.type] || 0)) + 1;
    if (event.errorCode) eventErrorCounts[event.errorCode] = Math.max(0, Number(eventErrorCounts[event.errorCode] || 0)) + 1;
  }
  const events = [];
  for (const event of persistedEvents.slice(-DIAGNOSTIC_EVENT_EXPORT_LIMIT)) {
    const copy = {...event};
    delete copy.id;
    delete copy.sessionId;
    if (copy.documentId) {
      copy.documentRef = await diagnosticReference(summary.sessionId, copy.documentId);
      delete copy.documentId;
    }
    if (copy.detail) copy.detail = sanitizeDiagnosticText(copy.detail, 240);
    if (copy.error) copy.error = sanitizeDiagnosticText(copy.error, 240);
    events.push(copy);
  }

  const diagnosticPacing = {...(summary.pacing || {})};
  if (Array.isArray(diagnosticPacing.recentOutcomes)) {
    diagnosticPacing.recentOutcomes = await Promise.all(diagnosticPacing.recentOutcomes.map(async (outcome) => {
      const copy = {...outcome};
      if (copy.outcomeKey) {
        copy.outcomeRef = await diagnosticReference(summary.sessionId, copy.outcomeKey);
        delete copy.outcomeKey;
      }
      return copy;
    }));
  }

  const manifest = chrome.runtime.getManifest();
  const pendingAgeMs = pending?.createdAt ? Math.max(0, Date.now() - Number(pending.createdAt)) : null;
  const pendingDocument = pending?.key ? await documentStore.get(pending.key).catch(() => null) : null;

  return {
    schemaVersion: DIAGNOSTIC_SCHEMA_VERSION,
    kind: "UYAP_PRIVACY_LIGHT_DIAGNOSTIC",
    generatedAt: new Date().toISOString(),
    privacy: {
      localOnlyByDefault: true,
      cloudUploadByDefault: false,
      rawDocumentTitlesIncluded: false,
      rawDocumentIdentifiersIncluded: false,
      rawCaseOrCourtIncluded: false,
      rawUrlsOrTokensIncluded: false,
      note: "Tanılama olayları privacy-light şemada tutulur; belge kimlikleri yalnız oturuma özgü kısaltılmış referans olarak dışa aktarılır."
    },
    extension: {
      name: manifest.name,
      version: manifest.version,
      versionName: manifest.version_name || null,
      lastStartedAgentVersion: summary.agentVersion || null,
      manifestVersion: manifest.manifest_version,
      minimumChromeVersion: manifest.minimum_chrome_version || null
    },
    environment: {
      userAgent: sanitizeDiagnosticText(globalThis.navigator?.userAgent || "", 240),
      language: globalThis.navigator?.language || null
    },
    source: {
      system: summary.sourceSystem || 'UYAP',
      adapter: summary.sourceContext?.adapter || null,
      inventoryEvidence: diagnosticSourceEvidence(summary)
    },
    session: {
      sessionId: summary.sessionId,
      status: summary.status,
      pauseReason: summary.pauseReason || null,
      sourceSystem: summary.sourceSystem || "UYAP",
      total: Number(summary.total || 0),
      counts: summary.counts || statusCounts,
      warningCount: Number(summary.warningCount || 0),
      problemBreakdown: summary.problemBreakdown || {},
      createdAt: summary.createdAt || null,
      updatedAt: summary.updatedAt || null,
      lastHeartbeatAt: summary.lastHeartbeatAt || null,
      heartbeatFailures: Number(summary.heartbeatFailures || 0),
      sessionGuardStatus: summary.sessionGuardStatus || null,
      sessionLossCount: Number(summary.sessionLossCount || 0),
      htmlSessionInterruptions: Number(summary.htmlSessionInterruptions || 0),
      recoveryCount: Number(summary.recoveryCount || 0),
      startPathRecoveryCount: Number(summary.startPathRecoveryCount || 0),
      contentCooldownCount: Number(summary.contentCooldownCount || 0),
      htmlCircuitState: htmlCircuitState(summary),
      htmlCircuitEpisode: Number(summary.htmlCircuitEpisode || 0),
      htmlCircuitProbeAttempt: Number(summary.htmlCircuitProbeAttempt || 0),
      htmlCircuitOpenCount: Number(summary.htmlCircuitOpenCount || 0),
      htmlCircuitHalfOpen: Boolean(summary.htmlCircuitHalfOpen),
      htmlCircuitRecoverySuccesses: Number(summary.htmlCircuitRecoverySuccesses || 0),
      htmlCircuitLastReason: summary.htmlCircuitLastReason || null,
      htmlCircuitOpenedAt: summary.htmlCircuitOpenedAt || null,
      htmlCircuitHalfOpenedAt: summary.htmlCircuitHalfOpenedAt || null,
      htmlCircuitNextProbeAt: summary.htmlCircuitNextProbeAt || null,
      htmlCircuitClosedAt: summary.htmlCircuitClosedAt || null,
      queueRuntimeRecoveryCount: Number(summary.queueRuntimeRecoveryCount || 0),
      sourceWaitRecoveryCount: Number(summary.sourceWaitRecoveryCount || 0),
      recoveryProbeFailures: Number(summary.recoveryProbeFailures || 0),
      sourceRebindRecoveryCount: Number(summary.sourceRebindRecoveryCount || 0),
      sourceWaitNextRetryAt: summary.sourceWaitNextRetryAt || null,
      recoveryProbeNextRetryAt: summary.recoveryProbeNextRetryAt || null,
      sourceRebindNextRetryAt: summary.sourceRebindNextRetryAt || null,
      startPreflightState: summary.startPreflightState || null,
      startPreflightMs: Number(summary.startPreflightMs || 0),
      startPreflightRecovered: Boolean(summary.startPreflightRecovered),
      startPreflightError: sanitizeDiagnosticText(summary.startPreflightError || '', 240) || null,
      startPreflightFirstError: sanitizeDiagnosticText(summary.startPreflightFirstError || '', 240) || null,
      queueError: sanitizeDiagnosticText(summary.queueError || '', 240) || null,
      runtimeScanned: Number(summary.runtimeScanned || 0),
      runtimeBound: Number(summary.runtimeBound || 0),
      runtimeUnmatched: Number(summary.runtimeUnmatched || 0),
      observedCaseId: summary.observedCaseId || null,
      archiveStatus: summary.archiveStatus || null,
      unresolved: Number(summary.unresolved || 0)
    },
    performance: summary.performance || {},
    pacing: diagnosticPacing,
    runtime: summary.runtimeDiagnostics || {},
    archive: {
      format: summary.archiveFormat || null,
      status: summary.archiveStatus || null,
      partCount: Number(summary.finalIntegrity?.partCount || summary.archiveParts?.length || 0),
      verifiedPartCount: Number(summary.finalIntegrity?.verifiedPartCount || 0),
      zipEntryCount: Number(summary.finalIntegrity?.zipEntryCount || 0),
      finalIntegrity: summary.finalIntegrity ? {
        sourceCount: Number(summary.finalIntegrity.sourceCount || 0),
        verifiedCount: Number(summary.finalIntegrity.verifiedCount || 0),
        manifestCount: Number(summary.finalIntegrity.manifestCount || 0),
        archiveFileCount: Number(summary.finalIntegrity.archiveFileCount || 0),
        zipEntryCount: Number(summary.finalIntegrity.zipEntryCount || 0),
        partCount: Number(summary.finalIntegrity.partCount || 0),
        verifiedPartCount: Number(summary.finalIntegrity.verifiedPartCount || 0),
        missingCount: Number(summary.finalIntegrity.missingCount || 0),
        unexpectedCount: Number(summary.finalIntegrity.unexpectedCount || 0),
        invalidCount: Number(summary.finalIntegrity.invalidCount || 0),
        reviewRequiredCount: Number(summary.finalIntegrity.reviewRequiredCount || 0),
        duplicateDocumentCount: Number(summary.finalIntegrity.duplicateDocumentCount || 0),
        duplicateSourceDocumentCount: Number(summary.finalIntegrity.duplicateSourceDocumentCount || 0),
        duplicatePathCount: Number(summary.finalIntegrity.duplicatePathCount || 0),
        fingerprintMismatchCount: Number(summary.finalIntegrity.fingerprintMismatchCount || 0),
        gatePassed: Boolean(summary.finalIntegrity.gatePassed),
        partialGatePassed: Boolean(summary.finalIntegrity.partialGatePassed)
      } : null
    },
    diagnostic: {
      persistedEventCount: persistedEvents.length,
      exportedEventCount: events.length,
      olderEventsOmitted: Math.max(0, persistedEvents.length - events.length),
      eventTypeCounts,
      eventErrorCounts,
      currentErrorCounts: errorCounts,
      events
    },
    pendingDownload: pending ? {
      ageMs: pendingAgeMs,
      hasDownloadId: Number.isInteger(pending.downloadId),
      status: pendingDocument?.status || null,
      attemptStrategy: pendingDocument?.attemptStrategy || null,
      leaseVersion: Number(pending.leaseVersion || 0) || null,
      leaseFresh: isLeaseFresh(pending),
      baselineDownloadCount: Array.isArray(pending.baselineDownloadIds) ? pending.baselineDownloadIds.length : 0,
      watchdogLastBytesReceived: Number.isFinite(Number(pendingDocument?.watchdogLastBytesReceived)) ? Number(pendingDocument.watchdogLastBytesReceived) : null,
      watchdogLastProgressAt: pendingDocument?.watchdogLastProgressAt || null,
      documentRef: pendingDocument ? await diagnosticReference(summary.sessionId, pendingDocument.documentId || pendingDocument.key) : null
    } : null,
    alarms: sessionAlarms,
    activeDocuments: await Promise.all(activeDocuments.map((document) => diagnosticDocumentView(summary.sessionId, document))),
    problemDocuments: await Promise.all(problemDocuments.map((document) => diagnosticDocumentView(summary.sessionId, document))),
    problemDocumentsTruncated: Math.max(0, documents.filter((document) => problemStatuses.has(document.status) || document.errorCode).length - DIAGNOSTIC_PROBLEM_LIMIT)
  };
}

async function quarantineLegacyDeterministicRetries(sessionId) {
  const documents = await documentStore.bySession(sessionId);
  let changed = 0;
  for (const document of documents) {
    if (document.status !== STATUS.RETRY_WAIT || !isIsolatedContentError(document.errorCode)) continue;
    // Legacy automatic deterministic retries should be quarantined, but an
    // explicit user-requested retry must be allowed to run exactly once. The
    // previous implementation immediately moved a manually requeued item back
    // to REVIEW_REQUIRED during startSession(), which made the retry button
    // appear to do nothing.
    if (document.retryClass === "manual_review" || document.manualRetryPriority === true) continue;
    await documentStore.update(document.key, {
      status: STATUS.REVIEW_REQUIRED,
      retryAt: null,
      retryQueuedAt: null,
      retryClass: null,
      retryExhausted: true,
      retryStrategy: null
    });
    changed += 1;
  }
  if (changed) {
    await sessionStore.update(sessionId, {
      deterministicRetryMigrationCount: changed,
      deterministicRetryMigrationAt: new Date().toISOString()
    });
  }
  return changed;
}


const BENIGN_PDF_VALIDATION_WARNING_PREFIX = "UYAP/Belgenet PDF sonuna tanılama metni ekledi";

async function migrateBenignPdfValidationWarnings(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session || Number(session.validationNoteSchemaVersion || 0) >= 1) return 0;
  const documents = await documentStore.bySession(sessionId);
  let changed = 0;
  for (const document of documents) {
    const warnings = Array.isArray(document.warnings) ? document.warnings : [];
    const benign = warnings.filter((warning) => String(warning || "").startsWith(BENIGN_PDF_VALIDATION_WARNING_PREFIX));
    if (!benign.length) continue;
    const remaining = warnings.filter((warning) => !String(warning || "").startsWith(BENIGN_PDF_VALIDATION_WARNING_PREFIX));
    const notes = [...new Set([...(Array.isArray(document.validationNotes) ? document.validationNotes : []), ...benign])];
    await documentStore.update(document.key, { warnings: remaining, validationNotes: notes });
    changed += 1;
  }
  await sessionStore.update(sessionId, {
    validationNoteSchemaVersion: 1,
    validationNoteMigrationAt: new Date().toISOString(),
    validationNoteMigrationDocuments: changed
  });
  if (changed) {
    await recordDiagnosticEvent(sessionId, "BENIGN_PDF_WARNING_MIGRATED", {documents: changed});
  }
  return changed;
}

async function migrateReliabilityState(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session || Number(session.reliabilitySchemaVersion || 0) >= RELIABILITY_SCHEMA_VERSION) return 0;
  const documents = await documentStore.bySession(sessionId);
  const now = new Date().toISOString();
  const updates = [];
  for (const document of documents) {
    if (hasRecoveredNativeGhost(document)) {
      updates.push({
        ...document,
        warnings: withoutRecoveredNativeWarnings(document.warnings),
        errorCode: String(document.errorCode || "") === TRANSIENT_NATIVE_ERROR_CODE ? null : document.errorCode || null,
        errorMessage: String(document.errorCode || "") === TRANSIENT_NATIVE_ERROR_CODE ? null : document.errorMessage || null,
        transientNativeRecoveredAt: now,
        transientNativeRecoveryReason: "reliability-schema-v5",
        updatedAt: now
      });
      continue;
    }
    if (document.status === STATUS.REVIEW_REQUIRED && document.errorCode === "LOCAL_ARCHIVE_AMBIGUOUS") {
      updates.push({
        ...document,
        status: STATUS.QUEUED,
        errorCode: null,
        errorMessage: null,
        attempts: 0,
        retryAt: null,
        retryQueuedAt: null,
        retryClass: null,
        retryStrategy: null,
        retryExhausted: false,
        manualRetryPriority: false,
        localArchiveMatchState: "ambiguous_hash_pending",
        localArchiveMatchMethod: "content-sha256-pending",
        localHashReconcilePending: true,
        updatedAt: now
      });
      continue;
    }
    if (document.status === STATUS.RETRY_WAIT && document.errorCode === "HTML_RESPONSE") {
      updates.push({
        ...document,
        retryClass: "deferred_html",
        retryAt: now,
        retryQueuedAt: document.retryQueuedAt || now,
        retryStrategy: "fresh_rebind",
        retryStrategyHistory: [],
        htmlRecoveryAttempts: 0,
        attemptRecoveryClass: null,
        retryExhausted: false,
        updatedAt: now
      });
      continue;
    }
    if (document.attemptRecoveryClass || document.htmlRecoveryAttempts) {
      updates.push({
        ...document,
        attemptRecoveryClass: null,
        htmlRecoveryAttempts: Math.max(0, Number(document.htmlRecoveryAttempts || 0)),
        updatedAt: now
      });
    }
  }
  if (updates.length) await documentStore.putMany(updates);
  // Startup migration is not proof that the native host recovered. Preserve a
  // live recovery state and its pacing evidence until ping/validation succeeds.
  const nativeRecoveryActive = session.nativeTransientFailureActive === true ||
    (session.status === SESSION_STATUS.RECOVERING && ["NATIVE_AGENT_RECOVERY", "NATIVE_OPERATION_RECOVERY"].includes(session.pauseReason));
  const changes = {
    reliabilitySchemaVersion: RELIABILITY_SCHEMA_VERSION,
    reliabilityMigrationAt: now,
    reliabilityMigrationDocuments: updates.length,
    primarySuccessesSinceRetry: 0,
    finalizationInProgress: false,
    transientNativeCleanupSchemaVersion: 1
  };
  if (!nativeRecoveryActive) {
    changes.pacing = clearRecoveredPacingError(session.pacing || {}, TRANSIENT_NATIVE_ERROR_CODE);
    changes.nativeTransientFailureActive = false;
    changes.nativeTransientRecoveredAt = now;
    changes.nativeTransientRecoveryReason = "reliability-schema-v5";
  }
  await sessionStore.update(sessionId, changes);
  if (updates.length) {
    await recordDiagnosticEvent(sessionId, "RELIABILITY_STATE_MIGRATED", {
      schemaVersion: RELIABILITY_SCHEMA_VERSION,
      documents: updates.length
    });
  }
  return updates.length;
}

async function tryRepairFinalPackageFromLocalState(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session || session.status !== SESSION_STATUS.INCOMPLETE || session.archiveStatus === "VERIFIED_COMPLETE") return false;
  if (session.finalizationInProgress) return false;
  if ((await getPendingDownload()) || await documentStore.hasActiveDownload(sessionId)) return false;
  const documents = await documentStore.bySession(sessionId);
  if (!documents.length) return false;
  const unresolved = documents.filter((document) => ![STATUS.VERIFIED, STATUS.SKIPPED].includes(document.status));
  if (unresolved.length) return false;

  // Do not silently override a source list that was positively observed as
  // changed, or a known resource/integrity failure that needs user action.
  const failure = String(session.finalizationError || "");
  if (/SOURCE_INVENTORY_CHANGED|INSUFFICIENT_DISK_SPACE|DUPLICATE|FINGERPRINT_MISMATCH|UNEXPECTED/i.test(failure)) return false;

  const manifest = chrome.runtime?.getManifest?.() || {};
  const build = String(manifest.version_name || manifest.version || "unknown");
  if (session.autoFinalPackageRepairBuild === build) return false;
  await sessionStore.update(sessionId, {
    autoFinalPackageRepairBuild: build,
    autoFinalPackageRepairAttemptAt: new Date().toISOString()
  });
  await recordDiagnosticEvent(sessionId, "AUTO_FINAL_PACKAGE_REPAIR_STARTED", {
    previousError: failure || null,
    documents: documents.length
  });
  try {
    const summary = await repackageVerifiedArchive(sessionId);
    await recordDiagnosticEvent(sessionId, "AUTO_FINAL_PACKAGE_REPAIR_FINISHED", {
      completed: summary?.status === SESSION_STATUS.COMPLETED,
      archiveStatus: summary?.archiveStatus || null
    });
    return summary?.status === SESSION_STATUS.COMPLETED;
  } catch (error) {
    await recordDiagnosticEvent(sessionId, "AUTO_FINAL_PACKAGE_REPAIR_FAILED", {
      error: sanitizeDiagnosticText(error, 240)
    });
    return false;
  }
}

async function repairUnassignedBatches(sessionId) {
  const session = await sessionStore.get(sessionId);
  if (!session?.largeCaseMode) return;
  const documents = await documentStore.bySession(sessionId);
  const updates = documents.flatMap((document,index) => Number(document.largeBatchNo) > 0 ? [] : [{...document,largeBatchNo:Math.floor(index/LARGE_CASE_BATCH_SIZE)+1}]);
  if (updates.length) await documentStore.putMany(updates);
  const stale = session.finalIntegrity && Number(session.finalIntegrity.sourceCount) !== documents.length;
  if (updates.length || stale) {
    await sessionStore.update(sessionId,{
      total:documents.length,largeCaseTotalBatches:Math.ceil(documents.length/LARGE_CASE_BATCH_SIZE),
      unresolved:documents.filter(d=>![STATUS.VERIFIED,STATUS.SKIPPED].includes(d.status)).length,
      ...(stale ? {finalIntegrity:null} : {})
    });
    await recordDiagnosticEvent(sessionId,"UNASSIGNED_BATCHES_REPAIRED",{documents:updates.length,staleIntegrityCleared:Boolean(stale)});
  }
}

export async function recoverSession() {
  await compactOperationalAlarms();
  const sessionId = await getActiveSessionId();
  if (!sessionId) return;
  const previous = await sessionStore.get(sessionId);
  if (previous && previous.identitySchemaVersion !== 3) {
    await sessionStore.update(sessionId,{status:SESSION_STATUS.PAUSED,pauseReason:'QUEUE_SCHEMA_CHANGED'});
  }
  await migrateReliabilityState(sessionId);
  // One-time recovery of endpoint rows prematurely terminalized by old state
  // normalization. Only zero-attempt records with exact suffix evidence qualify.
  const endpointSession = await sessionStore.get(sessionId);
  if (!endpointSession?.endpointZeroAttemptRepairV1) {
    const rows = await documentStore.bySessionStatus(sessionId, STATUS.REVIEW_REQUIRED);
    for (const row of rows) {
      if (row.errorCode !== "HTML_RESPONSE" || Number(row.attempts || 0) !== 0 || row.pdfTrailingCoreHex !== UYAP_INVALID_REQUEST_TAIL_HEX) continue;
      await documentStore.update(row.key, {
        status:STATUS.RETRY_WAIT,retryClass:"deferred_html",retryAt:new Date().toISOString(),
        retryStrategy:"fresh_rebind",retryStrategyHistory:["fresh_rebind"],
        retryExhausted:false,htmlRecoveryFinal:false,htmlRecoveryAttempts:0,
        attemptRecoveryClass:null,completionRecoveryPreviousErrorCode:null,lastRuntimeResetPreviousErrorCode:null
      });
    }
    await sessionStore.update(sessionId,{endpointZeroAttemptRepairV1:true});
  }
  await migrateLegacySmallFiles(sessionId);
  await repairUnassignedBatches(sessionId);
  await migrateBenignPdfValidationWarnings(sessionId);
  await autoRevalidatePdfTrailingReviews(sessionId);
  await quarantineLegacyDeterministicRetries(sessionId);
  await migrateTerminalHtmlReviewState(sessionId);

  // Extension/browser startup must reflect what is already on disk before the
  // popup advertises progress. This is intentionally best-effort: a missing
  // agent must never destroy or reset the persisted queue.
  try {
    const recoveryScanSession = await sessionStore.get(sessionId);
    const recoveryBatch = recoveryScanSession?.largeCaseMode ? Math.max(1, Number(recoveryScanSession.largeCaseCurrentBatch || 1)) : null;
    await syncSessionWithLocalArchive(sessionId, {largeBatchNo:recoveryBatch});
  } catch (error) {
    await recordDiagnosticEvent(sessionId, "LOCAL_ARCHIVE_RECONCILE_SKIPPED", {
      error: sanitizeDiagnosticText(error, 240)
    }).catch(()=>{});
  }

  let session = await sessionStore.get(sessionId);
  if (
    session?.status === SESSION_STATUS.RUNNING &&
    ["open", "half_open"].includes(htmlCircuitState(session)) &&
    Number(session.htmlCircuitProbeAttempt || 0) >= HTML_CIRCUIT_MAX_SOFT_PROBES
  ) {
    await clearSessionRuntimeAlarms(sessionId);
    await sessionStore.update(sessionId, {
      status:SESSION_STATUS.PAUSED,
      pauseReason:"CONTENT_ENDPOINT_REFRESH_REQUIRED",
      pausedAt:new Date().toISOString(),
      resumeAfterRecovery:false,
      recoveryStartedAt:null,
      sessionGuardStatus:"CONTENT_ENDPOINT_REFRESH_REQUIRED",
      htmlCircuitState:"open",
      htmlCircuitHalfOpen:false,
      htmlCircuitNextProbeAt:null,
      contentCooldownUntil:null,
      nextDownloadAt:0
    });
    await recordDiagnosticEvent(sessionId, "CONTENT_ENDPOINT_REFRESH_REQUIRED", {
      probeAttempt:Number(session.htmlCircuitProbeAttempt || 0),
      action:"upgrade_guard_manual_source_refresh_then_resume"
    });
    session = await sessionStore.get(sessionId);
  }
  if (session?.status === SESSION_STATUS.RECOVERING || session?.status === SESSION_STATUS.RUNNING) {
    await clearExpiredOrphanPendingDownload(sessionId, {reason:"runtime-recovery"});
  }

  if (session?.finalizationInProgress) {
    await sessionStore.update(sessionId, {
      finalizationInProgress: false,
      finalizationInterruptedAt: new Date().toISOString(),
      finalizationError: "FINALIZATION_INTERRUPTED_RETRYING"
    });
    await recordDiagnosticEvent(sessionId, "FINALIZATION_RESUME_AFTER_RUNTIME_RESTART", {
      previousStartedAt: session.finalizationStartedAt || null
    });
    session = await sessionStore.get(sessionId);
  }

  // A previous runtime can die after the last document was verified but before
  // the final ZIP/manifest commit. Repair that packaging-only state once per
  // extension build without touching the source documents or re-downloading.
  if (session?.status === SESSION_STATUS.INCOMPLETE) {
    const repaired = await tryRepairFinalPackageFromLocalState(sessionId);
    if (repaired) {
      await setSessionGuardBadge(false);
      return;
    }
    session = await sessionStore.get(sessionId);
  }

  // v2.7.1-v2.7.4 could persist CONTENT_ENDPOINT_DEGRADED while thousands of
  // untouched documents still existed. On upgrade, resume that primary queue
  // immediately; HTML records remain deferred and are retried later.
  if (session?.status === SESSION_STATUS.RECOVERING && session.pauseReason === "CONTENT_ENDPOINT_DEGRADED") {
    const freshPrimary = await documentStore.nextQueued(sessionId);
    if (freshPrimary) {
      await sessionStore.update(sessionId, {
        status: SESSION_STATUS.RUNNING,
        pauseReason: null,
        recoveryStartedAt: null,
        resumeAfterRecovery: false,
        sessionGuardStatus: "CONTENT_DEGRADED_SOFT",
        contentCooldownUntil: null,
        nextDownloadAt: Date.now() + 500
      });
      await recordDiagnosticEvent(sessionId, "LEGACY_CONTENT_COOLDOWN_RELEASED", {
        reason: "fresh_primary_queue_available"
      });
      session = await sessionStore.get(sessionId);
    }
  }

  if (session?.status === SESSION_STATUS.RUNNING) {
    const ready = await sessionPageReady(session, session.tabId);
    if (ready) {
      const binding = await refreshSessionRuntimeBindings(sessionId, session.tabId, {activeBatchOnly: Boolean(session.largeCaseMode)});
      if (bindingIsComplete(binding)) {
        await recoverStaleAwaitingBrowser(sessionId);
        await repairMassDownloadStartFailures(sessionId);
      } else {
        await pauseForUnsafeBinding(sessionId, binding);
        return;
      }
    } else {
      const probe = await probeSessionHealth(session, session.tabId);
      if (probe.loginRequired) {
        await recoverStaleAwaitingBrowser(sessionId);
        await pauseForLoginRequired(session, {source: "startup_recovery", detail: probe.reason || "LOGIN_REQUIRED"});
        return;
      }
      // A service-worker or page reload destroys the in-memory click/capture flow.
      // Do not advertise RUNNING while a dead AWAITING_BROWSER attempt blocks the queue.
      await recoverStaleAwaitingBrowser(sessionId);
      await sessionStore.update(sessionId, {
        status: SESSION_STATUS.RECOVERING,
        pauseReason: "SOURCE_PAGE_NOT_READY",
        recoveryStartedAt: new Date().toISOString(),
        resumeAfterRecovery: true,
        sessionGuardStatus: "SOURCE_UNAVAILABLE"
      });
      await scheduleOperationalAlarm(recoveryAlarmName(sessionId), {when: Date.now() + 3_000});
      return;
    }
  }

  await reconcileInFlightDocuments(sessionId);
  session = await sessionStore.get(sessionId);
  if (session?.status === SESSION_STATUS.RUNNING) {
    await scheduleHeartbeat(sessionId);
    await scheduleQueueGuard(sessionId);
    await scheduleQueueWake(sessionId, Date.now() + 250);
    void runNextQueueItem();
  } else if (session?.status === SESSION_STATUS.RECOVERING) {
    await scheduleOperationalAlarm(recoveryAlarmName(sessionId), { when: Date.now() + 3_000 });
  } else if (session?.status === SESSION_STATUS.PAUSED && session.pauseReason === "LOGIN_REQUIRED") {
    await setSessionGuardBadge(true, session);
    await showLoginRequiredNotification(session, {ensureOnly:true});
    await scheduleOperationalAlarm(`${LOGIN_WATCH_ALARM_PREFIX}${sessionId}`, {
      when: Date.now() + LOGIN_WATCH_INTERVAL_MS
    });
  } else {
    await setSessionGuardBadge(false);
  }
}

function heartbeatIntervalMs(session) {
  // Large jobs run for hours. A slightly more frequent lightweight heartbeat
  // lowers idle-session risk without adding per-document requests.
  return Number(session?.total || 0) >= 1_000 ? 2 * 60_000 : SESSION_HEARTBEAT_INTERVAL_MS;
}

function heartbeatIsDue(session) {
  if (Number.isFinite(session?.nextHeartbeatAt)) return Date.now() >= session.nextHeartbeatAt;
  if (!session?.lastHeartbeatAt) return true;
  const lastHeartbeat = Date.parse(session.lastHeartbeatAt);
  return !Number.isFinite(lastHeartbeat) || Date.now() - lastHeartbeat >= heartbeatIntervalMs(session);
}

async function scheduleHeartbeat(sessionId, delay = null) {
  const session = delay === null ? await sessionStore.get(sessionId) : null;
  const effectiveDelay = delay === null ? heartbeatIntervalMs(session) : delay;
  const when = Date.now() + effectiveDelay;
  await sessionStore.update(sessionId,{nextHeartbeatAt:when, heartbeatIntervalMs:effectiveDelay});
  await scheduleOperationalAlarm(`${HEARTBEAT_ALARM_PREFIX}${sessionId}`, {
    when
  });
}

async function performSessionHeartbeat(session) {
  if (heartbeatTask) return heartbeatTask;
  heartbeatTask = runSessionHeartbeat(session).finally(() => { heartbeatTask = null; });
  return heartbeatTask;
}

async function probeSessionHealth(session, tabId = session?.tabId) {
  if (!session || !Number.isInteger(tabId)) {
    return {
      healthy: false,
      loginRequired: false,
      reason: "UYAP_TAB_NOT_FOUND",
      response: {error: "UYAP_TAB_NOT_FOUND"},
      frameId: null
    };
  }
  let frames = [];
  try {
    frames = await chrome.webNavigation.getAllFrames({tabId}) || [];
  } catch {
    // Ana çerçeve yine de denenir.
  }
  const frameIds = [...new Set([0, ...frames.map((frame) => frame.frameId)].filter(Number.isInteger))].slice(0, 24);
  let healthyResponse = null;
  let loginResponse = null;
  let lastResponse = null;

  for (const frameId of frameIds) {
    try {
      const response = await chrome.tabs.sendMessage(
        tabId,
        {type: MESSAGE.SESSION_HEARTBEAT, timeoutMs: SESSION_HEARTBEAT_TIMEOUT_MS},
        {frameId}
      );
      const health = classifyHeartbeatResponse(response);
      const result = {response, health, frameId};
      lastResponse = result;
      // Explicit login/session-expired evidence is stronger than a generic 200
      // from another application shell iframe, so never stop at the first 200.
      if (health.loginRequired && !loginResponse) loginResponse = result;
      if (health.healthy && !healthyResponse) healthyResponse = result;
    } catch (error) {
      const response = {ok: false, httpOk: false, error: String(error)};
      lastResponse = {response, health: classifyHeartbeatResponse(response), frameId};
    }
  }

  const selected = loginResponse || healthyResponse || lastResponse || {
    response: {error: "UYAP_FRAME_NOT_FOUND"},
    health: {healthy: false, loginRequired: false, reason: "HEARTBEAT_HTTP_ERROR"},
    frameId: null
  };
  return {
    ...selected.health,
    response: selected.response,
    frameId: selected.frameId
  };
}

async function runSessionHeartbeat(session) {
  const outcome = await probeSessionHealth(session, session.tabId);

  if (outcome.healthy && !outcome.loginRequired) {
    await sessionStore.update(session.sessionId, {
      lastHeartbeatAt: new Date().toISOString(),
      heartbeatFailures: 0,
      heartbeatWarning: null,
      lastHeartbeatError: null,
      sessionGuardStatus: "HEALTHY"
    });
    await scheduleHeartbeat(session.sessionId);
    return true;
  }

  const policy = heartbeatFailurePolicy(
    outcome,
    session.heartbeatFailures,
    SESSION_HEARTBEAT_RETRY_MS,
    heartbeatIntervalMs(session)
  );

  if (policy.pause) {
    await pauseForLoginRequired(session, {
      source: "heartbeat",
      detail: outcome.reason || outcome.response?.error || "LOGIN_REQUIRED"
    });
    await sessionStore.update(session.sessionId, {
      heartbeatFailures: policy.failures,
      heartbeatWarning: null,
      lastHeartbeatError: outcome.reason || outcome.response?.error || "LOGIN_REQUIRED",
      sessionGuardStatus: "LOGIN_REQUIRED"
    });
    return false;
  }

  // A single failed probe may be a transient iframe/service-worker race. Two
  // consecutive failed probes plus a missing case/Evrak DOM means the queue is
  // no longer safe to drive, so freeze new work without touching an in-flight
  // browser download.
  if (policy.failures >= 2) {
    const pageReady = await sessionPageReady(session, session.tabId).catch(() => false);
    if (!pageReady) {
      const activeDownload = await activeInProgressBrowserDownload(session.sessionId);
      if (activeDownload) {
        // A live browser transfer does not need the Evrak tree until it finishes.
        // Do not freeze a healthy queue merely because UYAP temporarily hides or
        // navigates the source view while serving a TIFF/PDF/UDF response.
        await sessionStore.update(session.sessionId, {
          heartbeatFailures: policy.failures,
          heartbeatWarning: "SOURCE_CHECK_DEFERRED_DURING_DOWNLOAD",
          lastHeartbeatError: outcome.reason || outcome.response?.error || "HEARTBEAT_UNAVAILABLE",
          sessionGuardStatus: "DOWNLOAD_IN_PROGRESS_SOURCE_DEFERRED"
        });
        await scheduleHeartbeat(session.sessionId, SESSION_HEARTBEAT_RETRY_MS);
        return true;
      }
      await clearQueueControlAlarms(session.sessionId);
      await sessionStore.update(session.sessionId, {
        status: SESSION_STATUS.RECOVERING,
        pauseReason: "SOURCE_PAGE_NOT_READY",
        recoveryStartedAt: new Date().toISOString(),
        resumeAfterRecovery: true,
        heartbeatFailures: policy.failures,
        heartbeatWarning: "HEARTBEAT_UNAVAILABLE",
        lastHeartbeatError: outcome.reason || outcome.response?.error || "HEARTBEAT_UNAVAILABLE",
        sessionGuardStatus: "SOURCE_UNAVAILABLE"
      });
      await scheduleOperationalAlarm(recoveryAlarmName(session.sessionId), {when: Date.now() + 5_000});
      return false;
    }
  }

  await sessionStore.update(session.sessionId, {
    heartbeatFailures: policy.failures,
    heartbeatWarning: "HEARTBEAT_UNAVAILABLE",
    lastHeartbeatError: outcome.reason || outcome.response?.error || "HEARTBEAT_UNAVAILABLE",
    sessionGuardStatus: "DEGRADED"
  });
  await scheduleHeartbeat(session.sessionId, policy.retryDelay);
  return true;
}

async function reconcileInFlightDocuments(sessionId) {
  const documents = await documentStore.bySession(sessionId);
  const pending = await getPendingDownload();

  for (const document of documents) {
    if (document.status === STATUS.AWAITING_BROWSER) {
      // Reconcile the crash window after Chrome created the download but before
      // its ID reached IndexedDB. The attempt-specific staging name is unique.
      if (document.attemptId && document.stagingPath) {
        const name = document.stagingPath.split('/').at(-1);
        const items = await chrome.downloads.search({filenameRegex:name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&') + '$'});
        const own = items.filter(item => item.byExtensionId === chrome.runtime.id &&
          item.filename?.replaceAll('\\','/').endsWith(document.stagingPath));
        if (own.length === 1) {
          await documentStore.update(document.key,{downloadId:own[0].id,status:STATUS.DOWNLOADING,bindingMethod:'extension-download-id'});
          if (pending?.key === document.key) await clearPendingDownload();
          if (['complete','interrupted'].includes(own[0].state)) await handleDownloadChanged({id:own[0].id,state:{current:own[0].state}});
          else await scheduleOperationalAlarm(watchdogAlarmName(sessionId),{when:Date.now()+30_000});
          continue;
        }
      }
      const pendingIsFresh =
        pending?.key === document.key &&
        Date.now() - pending.createdAt < Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0));
      if (pendingIsFresh) {
        const timeoutMs = Math.max(CLICK_DOWNLOAD_TIMEOUT_MS, Number(pending.clickTimeoutMs || 0));
        await scheduleOperationalAlarm(clickTimeoutAlarmName(sessionId), {when: pending.createdAt + timeoutMs});
      }
      if (!pendingIsFresh) {
        if (pending?.key === document.key) await clearPendingDownload();
        await clearOperationalAlarm(clickTimeoutAlarmName(document.sessionId));
        await handleFailure(document, "RECOVERED_STALE_DOWNLOAD");
      }
      continue;
    }

    if (document.status === STATUS.DOWNLOADING) {
      await scheduleOperationalAlarm(watchdogAlarmName(sessionId), {when: Date.now() + 30_000});
      const [item] = Number.isInteger(document.downloadId)
        ? await chrome.downloads.search({ id: document.downloadId })
        : [];
      if (item?.state === "complete") {
        await handleDownloadChanged({ id: item.id, state: { current: "complete" } });
      } else if (!item || item.state === "interrupted") {
        await handleFailure(document, item?.error || "DOWNLOAD_RECORD_NOT_FOUND");
      }
    }
  }
}

async function migrateLegacySmallFiles(sessionId) {
  const documents = await documentStore.bySession(sessionId);
  for (const document of documents) {
    const warnings = document.warnings || [];
    const activeWarnings = warnings.filter((warning) => warning !== SMALL_FILE_WARNING);
    const removedSmallFileWarning = activeWarnings.length !== warnings.length;
    const unusableLegacyFile =
      document.status === STATUS.REVIEW_REQUIRED &&
      activeWarnings.some((warning) => ["EMPTY_FILE", "FILE_NOT_CONFIRMED"].includes(warning));

    if (unusableLegacyFile) {
      const errorCode = activeWarnings.includes("FILE_NOT_CONFIRMED") ? "FILE_NOT_CONFIRMED" : "EMPTY_FILE";
      await documentStore.update(document.key, {
        status: STATUS.FAILED,
        warnings: activeWarnings,
        errorCode,
        errorMessage: null
      });
      continue;
    }

    const hasCompletedLocalFile =
      document.actualSize > 0 &&
      Boolean(document.actualFilename) &&
      Boolean(document.completedAt) &&
      !String(document.actualMime || "").includes("text/html") &&
      !/\.html?$/i.test(String(document.actualFilename || ""));
    const onlySmallFileWarning =
      document.status === STATUS.REVIEW_REQUIRED &&
      warnings.length > 0 &&
      activeWarnings.length === 0;
    const requeuedCompletedFile =
      [STATUS.QUEUED, STATUS.RETRY_WAIT, STATUS.FAILED].includes(document.status) &&
      activeWarnings.length === 0 &&
      hasCompletedLocalFile;

    if (document.sha256 && hasCompletedLocalFile && (onlySmallFileWarning || requeuedCompletedFile)) {
      await documentStore.update(document.key, {
        status: STATUS.VERIFIED,
        warnings: activeWarnings,
        errorCode: null,
        errorMessage: null
      });
    } else if (removedSmallFileWarning) {
      await documentStore.update(document.key, { warnings: activeWarnings });
    }
  }
}
