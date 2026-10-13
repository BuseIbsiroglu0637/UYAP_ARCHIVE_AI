export const STATUS = Object.freeze({
  DISCOVERED: "discovered",
  QUEUED: "queued",
  AWAITING_BROWSER: "awaiting_browser",
  DOWNLOADING: "downloading",
  COMPLETED: "completed",
  VERIFIED: "verified",
  REVIEW_REQUIRED: "review_required",
  RETRY_WAIT: "retry_wait",
  FAILED: "failed",
  SKIPPED: "skipped"
});

export const SESSION_STATUS = Object.freeze({
  READY: "ready",
  RUNNING: "running",
  PAUSED: "paused",
  RECOVERING: "recovering",
  INCOMPLETE: "incomplete",
  COMPLETED: "completed"
});

export const CONTENT_PROTOCOL_VERSION = "2.9.0";
export const RELIABILITY_SCHEMA_VERSION = 5;

export const MESSAGE = Object.freeze({
  SCAN_PAGE: "scan_page",
  SESSION_HEARTBEAT: "session_heartbeat",
  SESSION_EXPIRED_SIGNAL: "session_expired_signal",
  CLICK_DOCUMENT: "click_document",
  PREFLIGHT_DOCUMENT: "preflight_document",
  REBIND_DOCUMENT: "rebind_document",
  PROBE_PAGE: "probe_page",
  SOURCE_DIAGNOSTIC: "source_diagnostic",
  FORMAT_LAB_REPORT: "format_lab_report",
  LIST_KURUM_CASES: "list_kurum_cases",
  OPEN_KURUM_CASE: "open_kurum_case",
  OPEN_KURUM_CASE_LIST: "open_kurum_case_list",
  KURUM_CASE_CONTEXT_OBSERVED: "kurum_case_context_observed",
  CREATE_SESSION: "create_session",
  START_SESSION: "start_session",
  PAUSE_SESSION: "pause_session",
  GET_ACTIVE_SESSION: "get_active_session",
  GET_SESSIONS: "get_sessions",
  GET_RESUMABLE_SESSIONS: "get_resumable_sessions",
  RESUME_SAVED_SESSION: "resume_saved_session",
  GET_REPORT_DATA: "get_report_data",
  GET_AI_CONTEXT: "get_ai_context",
  GET_AI_SERVICE_STATUS: "get_ai_service_status",
  BUILD_AI_INDEX: "build_ai_index",
  GET_AI_INDEX_STATUS: "get_ai_index_status",
  SEARCH_AI_INDEX: "search_ai_index",
  AI_ASK: "ai_ask",
  GET_AI_ARCHIVE_CASES: "get_ai_archive_cases",
  BUILD_AI_ARCHIVE_CASE_INDEX: "build_ai_archive_case_index",
  GET_AI_ARCHIVE_CASE_INDEX_STATUS: "get_ai_archive_case_index_status",
  SEARCH_AI_ARCHIVE_CASE: "search_ai_archive_case",
  AI_ASK_ARCHIVE_CASE: "ai_ask_archive_case",
  GET_ARCHIVE_REGISTRY: "get_archive_registry",
  SELECT_EXISTING_ARCHIVE: "select_existing_archive",
  GET_SELECTED_ARCHIVE: "get_selected_archive",
  GET_DIAGNOSTIC_DATA: "get_diagnostic_data",
  GET_PROBLEM_ITEMS: "get_problem_items",
  RETRY_SELECTED_PROBLEMS: "retry_selected_problems",
  OPEN_LOGIN_ROUTE: "open_login_route",
  RETRY_FAILED: "retry_failed",
  RESET_RUNTIME: "reset_runtime",
  REPACKAGE_ARCHIVE: "repackage_archive",
  NEW_JOB: "new_job",
  TEST_NOTIFICATION: "test_notification",
  OPEN_ARCHIVE_FOLDER: "open_archive_folder",
  OPEN_FINAL_PACKAGE_FOLDER: "open_final_package_folder",
  CONFIRM_HTML_EXCLUSION: "confirm_html_exclusion"
});

export const RETRY_DELAYS_MS = Object.freeze([5_000, 20_000, 60_000]);
export const HTML_RETRY_DELAYS_MS = Object.freeze([5_000]);
export const CLICK_DOWNLOAD_TIMEOUT_MS = 90_000;
export const SESSION_HEARTBEAT_INTERVAL_MS = 3 * 60_000;
export const SESSION_HEARTBEAT_RETRY_MS = 30_000;
export const SESSION_HEARTBEAT_TIMEOUT_MS = 15_000;
export const DB_NAME = "uyap-profesyonel-arsiv-v1";
export const DB_VERSION = 6;
