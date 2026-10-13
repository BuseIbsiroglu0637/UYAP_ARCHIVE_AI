const HOST_NAME = "tr.gov.uyap.archive_agent";
const REQUIRED_CAPABILITY = "validation_perf_v1";
const REQUIRED_FINAL_CAPABILITY = "final_integrity_v1";
const REQUIRED_SOURCE_CAPABILITY = "source_adapter_v1";
const REQUIRED_CHUNKED_CAPABILITY = "chunked_archive_v1";
const REQUIRED_COMPLETION_GUARD_CAPABILITY = "download_completion_guard_v1";
const PDF_STRUCTURE_V2_CAPABILITY = "pdf_structure_v2";
const UYAP_PDF_CONTROL_SUFFIX_CAPABILITY = "uyap_pdf_control_suffix_1a04_v1";
const BULK_RELIABILITY_CAPABILITY = "bulk_reliability_v1";
const LEGITIMATE_HTML_DOCUMENT_CAPABILITY = "legitimate_html_document_v2";
const IMAGE_DOCUMENT_CAPABILITY = "image_document_v1";
const QUARANTINE_REVALIDATION_CAPABILITY = "quarantine_revalidation_v1";
const PDF_BELGENET_SUFFIX_CAPABILITY = "uyap_pdf_belgenet_suffix_v1";
const UNIFIED_DOCUMENT_SNIFFER_CAPABILITY = "unified_document_sniffer_v1";
const RAW_XML_UDF_CAPABILITY = "uyap_raw_xml_udf_v1";
const FORMAT_LAB_CAPABILITY = "format_lab_v1";
const OFFICE_CONTAINER_CAPABILITY = "office_container_detection_v1";
const ARCHIVE_REGISTRY_CAPABILITY = "archive_registry_v1";
const USER_HTML_EXCLUSION_CAPABILITY = "user_html_exclusion_v1";
const ARCHIVE_ROOT_REDISCOVERY_CAPABILITY = "archive_root_rediscovery_v1";
const DUAL_LANE_SQLITE_CAPABILITY = "sqlite_wal_dual_lane_v1";
const SQLITE_SCANDIR_HOTPATH_CAPABILITY = "sqlite_scandir_hotpath_v1";
const JPEG_UYAP_COMPAT_CAPABILITY = "jpeg_uyap_compatible_v1";
const JPEG_WINDOWS_DECODER_CAPABILITY = "jpeg_windows_decoder_fallback_v1";
const JPEG_WINDOWS_WIC_CAPABILITY = "jpeg_windows_wic_decoder_v1";
const DOCUMENT_TYPE_TRUTH_CAPABILITY = "document_type_truth_v1";
const UYAP_EXACT_CONTENT_TRUTH_CAPABILITY = "uyap_exact_content_truth_v1";
const JPEG_UYAP_TRAILING_PRESERVE_CAPABILITY = "jpeg_uyap_trailing_preserve_v1";
const HTML_CIRCUIT_DEADLINE_CAPABILITY = "html_circuit_deadline_v1";
const SOURCE_PATH_SELF_HEAL_CAPABILITY = "source_path_self_heal_v1";
const VERIFIED_ROOT_ORPHAN_CLEANUP_CAPABILITY = "verified_root_orphan_cleanup_v1";
const OPAQUE_BINARY_ARCHIVE_CAPABILITY = "opaque_binary_archive_v1";
const FINALIZATION_VALIDATION_PROOF_V2_CAPABILITY = "finalization_validation_proof_v2";
const DUPLICATE_PATH_FRESH_OWNER_CAPABILITY = "duplicate_path_fresh_owner_v1";
const PORTABLE_ARCHIVE_RESUME_CAPABILITY = "portable_archive_resume_v1";
const LEGACY_LOOSE_ARCHIVE_IMPORT_CAPABILITY = "legacy_loose_archive_import_v2";
const ATTEMPT_OWNER_REPLAY_CAPABILITY = "attempt_owner_replay_v1";
const AI_LOCAL_INDEX_CAPABILITY = "ai_local_index_v1";
const AI_PROVIDER_GATEWAY_CAPABILITY = "ai_provider_gateway_v1";
const ARCHIVE_CASE_AI_CAPABILITY = "archive_case_ai_v1";
const MIN_AGENT_VERSION = "2.7.68";
const IDLE_DISCONNECT_MS = 3 * 60_000;

// RC15.9.28: Native Messaging has three independent lanes.
// Chromium creates a separate native-host process per connectNative() port, so
// dashboard registry scans can never sit in front of batch reconciliation or a
// validate_download request. UYAP browser downloads remain strictly sequential.
const BULK_ACTIONS = new Set(["sync_inventory", "finalize_archive", "format_audit", "ai_build_index", "ai_build_archive_case_index", "ai_inspect_archive_case", "ai_search", "ai_index_status", "ai_answer"]);
const REGISTRY_ACTIONS = new Set(["list_archive_registry", "select_existing_archive"]);

function createLane(name) {
  return {
    name,
    nativePort: null,
    nativeRuntimeRef: null,
    activeRequest: null,
    requestQueue: [],
    idleTimer: null,
    stats: {
      connectCount: 0,
      disconnectCount: 0,
      timeoutCount: 0,
      failedCount: 0,
      maxQueueDepth: 0,
      lastConnectAt: null,
      lastDisconnectAt: null
    }
  };
}

const nativeLanes = Object.freeze({
  fast: createLane("fast"),
  bulk: createLane("bulk"),
  registry: createLane("registry")
});

function laneForAction(action) {
  const normalized = String(action || "");
  if (REGISTRY_ACTIONS.has(normalized)) return nativeLanes.registry;
  return BULK_ACTIONS.has(normalized) ? nativeLanes.bulk : nativeLanes.fast;
}

function classifyNativeBridgeError(message) {
  const raw = String(message || "");
  const lower = raw.toLowerCase();
  if (lower.includes("specified native messaging host not found") || lower.includes("native messaging host not found")) {
    return { errorCode:"NATIVE_HOST_NOT_FOUND", error:"Yerel ajan kaydı bulunamadı. YEREL_AJANI_KUR.bat dosyasını yeniden çalıştırın; Chrome/Edge kaydı otomatik onarılır." };
  }
  if (lower.includes("access to the specified native messaging host is forbidden") || lower.includes("forbidden")) {
    return { errorCode:"NATIVE_HOST_FORBIDDEN", error:"Yerel ajan kurulu ancak bu uzantı kimliğine izin verilmemiş. Ajan kurulumunu bu paket içinden yeniden çalıştırın." };
  }
  if (lower.includes("native host has exited") || lower.includes("host has exited")) {
    return { errorCode:"NATIVE_HOST_EXITED", error:"Yerel ajan başlatıldı ancak beklenmedik biçimde kapandı. Kurulum tanılamasını kontrol edin." };
  }
  if (lower.includes("invalid native messaging host name")) {
    return { errorCode:"NATIVE_HOST_NAME_INVALID", error:"Yerel ajan host adı geçersiz. Kurulum paketini yeniden çalıştırın." };
  }
  if (lower.includes("error when communicating with the native messaging host")) {
    return { errorCode:"NATIVE_HOST_COMMUNICATION_ERROR", error:"Yerel ajan bulundu ancak iletişim kurulamadı. Tarayıcıyı tamamen kapatıp yeniden açın; sorun sürerse kurulum tanılamasını kontrol edin." };
  }
  return { errorCode:"NATIVE_AGENT_UNAVAILABLE", error:raw || "Yerel ajan bağlantısı kurulamadı." };
}

function settleRequest(request, value) {
  if (!request || request.settled) return;
  request.settled = true;
  clearTimeout(request.timer);
  request.resolve(value);
}

function clearLaneIdleTimer(lane) {
  if (!lane.idleTimer) return;
  clearTimeout(lane.idleTimer);
  lane.idleTimer = null;
}

function scheduleLaneIdleDisconnect(lane) {
  clearLaneIdleTimer(lane);
  // RC15.9: FAST lane is the latency-critical validation path. Keeping this
  // single native port warm avoids Python/bootstrap reconnect jitter between
  // documents and also gives MV3 an intentional long-lived native connection.
  // BULK still disconnects when idle so reconciliation/finalization does not
  // keep an unnecessary second host process around.
  if (lane.name === "fast") return;
  if (lane.activeRequest || lane.requestQueue.length || !lane.nativePort) return;
  lane.idleTimer = setTimeout(() => {
    const port = lane.nativePort;
    lane.nativePort = null;
    lane.nativeRuntimeRef = null;
    if (port) {
      lane.stats.disconnectCount += 1;
      lane.stats.lastDisconnectAt = new Date().toISOString();
    }
    try { port?.disconnect(); } catch {}
  }, IDLE_DISCONNECT_MS);
  lane.idleTimer?.unref?.();
}

function failLanePort(lane, port, error) {
  if (port !== lane.nativePort) return;
  lane.nativePort = null;
  lane.nativeRuntimeRef = null;
  clearLaneIdleTimer(lane);
  lane.stats.disconnectCount += 1;
  lane.stats.lastDisconnectAt = new Date().toISOString();
  const message = error || chrome.runtime.lastError?.message || "Yerel ajan bağlantısı kapandı";
  if (lane.activeRequest) {
    const request = lane.activeRequest;
    lane.activeRequest = null;
    const classified = classifyNativeBridgeError(message);
    settleRequest(request, { ok:false, ...classified, rawError:String(message || ""), nativeLane:lane.name });
  }
  if (lane.requestQueue.length) queueMicrotask(() => pumpLane(lane));
}

function ensureLanePort(lane) {
  if (lane.nativePort && lane.nativeRuntimeRef === chrome.runtime) return lane.nativePort;
  if (lane.nativePort && lane.nativeRuntimeRef !== chrome.runtime) {
    const stale = lane.nativePort;
    lane.nativePort = null;
    lane.nativeRuntimeRef = null;
    lane.stats.disconnectCount += 1;
    lane.stats.lastDisconnectAt = new Date().toISOString();
    try { stale.disconnect(); } catch {}
  }
  const port = chrome.runtime.connectNative(HOST_NAME);
  lane.stats.connectCount += 1;
  lane.stats.lastConnectAt = new Date().toISOString();
  lane.nativePort = port;
  lane.nativeRuntimeRef = chrome.runtime;
  port.onMessage.addListener(response => {
    if (port !== lane.nativePort || !lane.activeRequest) return;
    const request = lane.activeRequest;
    lane.activeRequest = null;
    // Preserve structured errorCode/diagnostic fields on negative replies.
    // Dropping them makes recoverable file/path failures look like a generic
    // native-host outage in the queue manager.
    const normalized = response && typeof response === "object"
      ? {...response, ok:response.ok === true, error:response.error || (response.ok === true ? null : "Geçersiz ajan yanıtı")}
      : {ok:false, errorCode:"NATIVE_INVALID_RESPONSE", error:"Geçersiz ajan yanıtı"};
    settleRequest(request, {...normalized, nativeLane: lane.name, bridgeResponseReceived:true});
    scheduleLaneIdleDisconnect(lane);
    queueMicrotask(() => pumpLane(lane));
  });
  port.onDisconnect.addListener(() => failLanePort(lane, port));
  return port;
}

function pumpLane(lane) {
  if (lane.activeRequest || !lane.requestQueue.length) {
    scheduleLaneIdleDisconnect(lane);
    return;
  }
  clearLaneIdleTimer(lane);
  const request = lane.requestQueue.shift();
  lane.activeRequest = request;
  try {
    const port = ensureLanePort(lane);
    request.timer = setTimeout(() => {
      if (lane.activeRequest !== request) return;
      lane.activeRequest = null;
      const timedOutPort = lane.nativePort;
      lane.nativePort = null;
      lane.nativeRuntimeRef = null;
      lane.stats.timeoutCount += 1;
      if (timedOutPort) {
        lane.stats.disconnectCount += 1;
        lane.stats.lastDisconnectAt = new Date().toISOString();
      }
      settleRequest(request, {
        ok:false,
        errorCode:"NATIVE_AGENT_TIMEOUT",
        error:"Yerel ajan zaman aşımı; dosyalar korunuyor.",
        nativeLane:lane.name
      });
      try { timedOutPort?.disconnect(); } catch {}
      queueMicrotask(() => pumpLane(lane));
    }, request.timeoutMs);
    request.timer?.unref?.();
    port.postMessage(request.message);
  } catch (error) {
    lane.activeRequest = null;
    lane.stats.failedCount += 1;
    const classified = classifyNativeBridgeError(String(error));
    settleRequest(request, {ok:false, ...classified, rawError:String(error), nativeLane:lane.name});
    queueMicrotask(() => pumpLane(lane));
  }
}

function timeoutForNativeAction(action) {
  if (action === "ping") return 8_000;
  if (action === "finalize_archive") return 4 * 60 * 60_000;
  if (action === "sync_inventory") return 5 * 60_000;
  if (action === "open_archive_folder" || action === "open_final_package_folder") return 8_000;
  if (action === "list_archive_registry") return 2 * 60_000;
  if (action === "select_existing_archive") return 190_000;
  if (action === "ai_answer") return 3 * 60_000;
  if (action === "discard_review_html") return 15_000;
  return 30 * 60_000;
}

export async function askNativeAgent(message, timeoutMs = timeoutForNativeAction(message?.action)) {
  return new Promise(resolve => {
    const lane = laneForAction(message?.action);
    const priority = Number.isFinite(Number(message?.__nativePriority)) ? Number(message.__nativePriority) : (lane.name === "fast" ? 0 : 10);
    const wireMessage = {...(message || {})};
    delete wireMessage.__nativePriority;
    lane.requestQueue.push({ message:wireMessage, timeoutMs, resolve, timer:null, settled:false, priority, queuedAt:Date.now() });
    lane.requestQueue.sort((a,b) => (a.priority - b.priority) || (a.queuedAt - b.queuedAt));
    lane.stats.maxQueueDepth = Math.max(lane.stats.maxQueueDepth, lane.requestQueue.length + (lane.activeRequest ? 1 : 0));
    pumpLane(lane);
  });
}

function versionAtLeast(value, minimum = MIN_AGENT_VERSION) {
  const parse = input => {
    const parts = String(input || "").trim().split(".");
    if (!parts.length || parts.some(part => !/^\d+$/.test(part))) return null;
    return parts.slice(0, 3).map(Number).concat([0, 0, 0]).slice(0, 3);
  };
  const current = parse(value);
  const required = parse(minimum);
  if (!current || !required) return false;
  for (let index = 0; index < 3; index += 1) {
    if (current[index] > required[index]) return true;
    if (current[index] < required[index]) return false;
  }
  return true;
}

export async function pingNativeAgent() {
  const result = await askNativeAgent({action:'ping'});
  if (result.ok && !versionAtLeast(result.version)) {
    return {
      ok:false,
      error:`Ajan sürümü uyumsuz. En az ${MIN_AGENT_VERSION} gerekli; bağlı ajan ${result.version || "bilinmiyor"}.`,
      errorCode:'AGENT_VERSION_MISMATCH',
      version:result.version
    };
  }
  if (result.ok && (
    result.protocolVersion !== 2 ||
    !result.capabilities?.includes('attempt_journal') ||
    !result.capabilities?.includes(REQUIRED_CAPABILITY) ||
    !result.capabilities?.includes(REQUIRED_FINAL_CAPABILITY) ||
    !result.capabilities?.includes(REQUIRED_SOURCE_CAPABILITY) ||
    !result.capabilities?.includes(REQUIRED_CHUNKED_CAPABILITY) ||
    !result.capabilities?.includes(BULK_RELIABILITY_CAPABILITY) ||
    !result.capabilities?.includes(LEGITIMATE_HTML_DOCUMENT_CAPABILITY) ||
    !result.capabilities?.includes(IMAGE_DOCUMENT_CAPABILITY) ||
    !result.capabilities?.includes(QUARANTINE_REVALIDATION_CAPABILITY) ||
    !result.capabilities?.includes(PDF_BELGENET_SUFFIX_CAPABILITY) ||
    !result.capabilities?.includes(UNIFIED_DOCUMENT_SNIFFER_CAPABILITY) ||
    !result.capabilities?.includes(RAW_XML_UDF_CAPABILITY) ||
    !result.capabilities?.includes(FORMAT_LAB_CAPABILITY) ||
    !result.capabilities?.includes(OFFICE_CONTAINER_CAPABILITY) ||
    !result.capabilities?.includes(ARCHIVE_REGISTRY_CAPABILITY) ||
    !result.capabilities?.includes(USER_HTML_EXCLUSION_CAPABILITY) ||
    !result.capabilities?.includes(ARCHIVE_ROOT_REDISCOVERY_CAPABILITY) ||
    !result.capabilities?.includes(DUAL_LANE_SQLITE_CAPABILITY) ||
    !result.capabilities?.includes(SQLITE_SCANDIR_HOTPATH_CAPABILITY) ||
    !result.capabilities?.includes(JPEG_UYAP_COMPAT_CAPABILITY) ||
    !result.capabilities?.includes(JPEG_WINDOWS_DECODER_CAPABILITY) ||
    !result.capabilities?.includes(JPEG_WINDOWS_WIC_CAPABILITY) ||
    !result.capabilities?.includes(DOCUMENT_TYPE_TRUTH_CAPABILITY) ||
    !result.capabilities?.includes(UYAP_EXACT_CONTENT_TRUTH_CAPABILITY) ||
    !result.capabilities?.includes(JPEG_UYAP_TRAILING_PRESERVE_CAPABILITY) ||
    !result.capabilities?.includes(HTML_CIRCUIT_DEADLINE_CAPABILITY) ||
    !result.capabilities?.includes(SOURCE_PATH_SELF_HEAL_CAPABILITY) ||
    !result.capabilities?.includes(VERIFIED_ROOT_ORPHAN_CLEANUP_CAPABILITY) ||
    !result.capabilities?.includes(PORTABLE_ARCHIVE_RESUME_CAPABILITY) ||
    !result.capabilities?.includes(LEGACY_LOOSE_ARCHIVE_IMPORT_CAPABILITY) ||
    !result.capabilities?.includes(ATTEMPT_OWNER_REPLAY_CAPABILITY) ||
    !result.capabilities?.includes(AI_LOCAL_INDEX_CAPABILITY) ||
    !result.capabilities?.includes(AI_PROVIDER_GATEWAY_CAPABILITY) ||
    !result.capabilities?.includes(ARCHIVE_CASE_AI_CAPABILITY)
  )) {
    return {ok:false,error:'Yerel ajan bu UYAP AI sürümüyle uyumlu değil. Bu branch içindeki yerel ajanı yeniden kurun.',errorCode:'AGENT_CAPABILITY_MISMATCH'};
  }
  if (result.ok) {
    return {
      ...result,
      completionGuardAvailable: result.capabilities?.includes(REQUIRED_COMPLETION_GUARD_CAPABILITY) === true,
      pdfStructureV2Available: result.capabilities?.includes(PDF_STRUCTURE_V2_CAPABILITY) === true,
      pdfControlSuffixAvailable: result.capabilities?.includes(UYAP_PDF_CONTROL_SUFFIX_CAPABILITY) === true,
      downloadReady: result.capabilities?.includes(UYAP_PDF_CONTROL_SUFFIX_CAPABILITY) === true && result.capabilities?.includes(BULK_RELIABILITY_CAPABILITY) === true && result.capabilities?.includes(LEGITIMATE_HTML_DOCUMENT_CAPABILITY) === true && result.capabilities?.includes(IMAGE_DOCUMENT_CAPABILITY) === true && result.capabilities?.includes(PDF_BELGENET_SUFFIX_CAPABILITY) === true && result.capabilities?.includes(UNIFIED_DOCUMENT_SNIFFER_CAPABILITY) === true && result.capabilities?.includes(RAW_XML_UDF_CAPABILITY) === true && result.capabilities?.includes(OPAQUE_BINARY_ARCHIVE_CAPABILITY) === true && result.capabilities?.includes(FINALIZATION_VALIDATION_PROOF_V2_CAPABILITY) === true && result.capabilities?.includes(DUPLICATE_PATH_FRESH_OWNER_CAPABILITY) === true,
      bulkReliabilityAvailable: result.capabilities?.includes(BULK_RELIABILITY_CAPABILITY) === true,
      legitimateHtmlDocumentAvailable: result.capabilities?.includes(LEGITIMATE_HTML_DOCUMENT_CAPABILITY) === true,
      imageDocumentAvailable: result.capabilities?.includes(IMAGE_DOCUMENT_CAPABILITY) === true,
      quarantineRevalidationAvailable: result.capabilities?.includes(QUARANTINE_REVALIDATION_CAPABILITY) === true,
      pdfBelgenetSuffixAvailable: result.capabilities?.includes(PDF_BELGENET_SUFFIX_CAPABILITY) === true,
      unifiedDocumentSnifferAvailable: result.capabilities?.includes(UNIFIED_DOCUMENT_SNIFFER_CAPABILITY) === true,
      rawXmlUdfAvailable: result.capabilities?.includes(RAW_XML_UDF_CAPABILITY) === true,
      formatLabAvailable: result.capabilities?.includes(FORMAT_LAB_CAPABILITY) === true,
      officeContainerDetectionAvailable: result.capabilities?.includes(OFFICE_CONTAINER_CAPABILITY) === true,
      archiveRegistryAvailable: result.capabilities?.includes(ARCHIVE_REGISTRY_CAPABILITY) === true,
      userHtmlExclusionAvailable: result.capabilities?.includes(USER_HTML_EXCLUSION_CAPABILITY) === true,
      verifiedRootOrphanCleanupAvailable: result.capabilities?.includes(VERIFIED_ROOT_ORPHAN_CLEANUP_CAPABILITY) === true,
      compatibilityMode: result.capabilities?.includes(REQUIRED_COMPLETION_GUARD_CAPABILITY) ? null : 'extension_completion_guard'
    };
  }
  return result;
}

export function disconnectNativeAgent() {
  for (const lane of Object.values(nativeLanes)) {
    clearLaneIdleTimer(lane);
    const port = lane.nativePort;
    lane.nativePort = null;
    lane.nativeRuntimeRef = null;
    const disconnected = {
      ok:false,
      errorCode:"NATIVE_AGENT_DISCONNECTED",
      error:"Yerel ajan bağlantısı kapatıldı; bekleyen dosyalar korunuyor.",
      nativeLane:lane.name
    };
    if (lane.activeRequest) {
      const active = lane.activeRequest;
      lane.activeRequest = null;
      settleRequest(active, disconnected);
    }
    for (const queued of lane.requestQueue.splice(0)) settleRequest(queued, disconnected);
    if (port) {
      lane.stats.disconnectCount += 1;
      lane.stats.lastDisconnectAt = new Date().toISOString();
    }
    try { port?.disconnect(); } catch {}
  }
}

export function getNativeBridgeStats() {
  const lanes = {};
  let connectCount = 0;
  let disconnectCount = 0;
  let timeoutCount = 0;
  let failedCount = 0;
  let pendingRequests = 0;
  let activeConnections = 0;
  let maxQueueDepth = 0;
  for (const [name, lane] of Object.entries(nativeLanes)) {
    const pending = lane.requestQueue.length + (lane.activeRequest ? 1 : 0);
    const connected = Boolean(lane.nativePort);
    lanes[name] = {
      ...lane.stats,
      pendingRequests: pending,
      connected,
      activeConnections: connected ? 1 : 0,
      reconnectCount: Math.max(0, lane.stats.connectCount - 1)
    };
    connectCount += lane.stats.connectCount;
    disconnectCount += lane.stats.disconnectCount;
    timeoutCount += lane.stats.timeoutCount;
    failedCount += lane.stats.failedCount;
    pendingRequests += pending;
    activeConnections += connected ? 1 : 0;
    maxQueueDepth = Math.max(maxQueueDepth, lane.stats.maxQueueDepth);
  }
  return {
    connectCount,
    disconnectCount,
    timeoutCount,
    failedCount,
    maxQueueDepth,
    lastConnectAt: Object.values(lanes).map((lane)=>lane?.lastConnectAt).filter(Boolean).sort().at(-1) || null,
    lastDisconnectAt: Object.values(lanes).map((lane)=>lane?.lastDisconnectAt).filter(Boolean).sort().at(-1) || null,
    pendingRequests,
    connected: activeConnections > 0,
    activeConnections,
    reconnectCount: Math.max(0, connectCount - activeConnections),
    lanes
  };
}
