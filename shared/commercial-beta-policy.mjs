/*
 * S2-01 commercial beta safety gate.
 *
 * RELEASE_CHANNEL is a compile-time distribution constant, never a value read
 * from chrome.storage, URL parameters, sender messages or external servers.
 * A beta release must set this to "beta" in the signed build and have the
 * same channel in the Native Agent + release-contract.json.
 *
 * RC/alpha compatibility is preserved while separate integration reviews are
 * open; public commercial distributions are local-only by default.
 */
export const RELEASE_CHANNEL = "rc";

export const PORTAL_AUTOMATION_MESSAGES = Object.freeze([
  "capture_save",
  "scan_session",
  "new_job",
  "create_session",
  "start_session",
  "resume_saved_session",
  "retry_selected_problems",
  "confirm_html_exclusion",
  "retry_failed",
  "open_login_route",
  "source_diagnostic",
  "format_lab_report",
  "list_kurum_cases",
  "open_kurum_case",
  // Portal-origin content-script reports must not mutate beta state.
  "kurum_case_context_observed",
  "session_expired_signal"
]);

const KNOWN_CHANNELS = new Set(["alpha", "beta", "rc", "stable"]);
const PORTAL_ACTIONS = new Set(PORTAL_AUTOMATION_MESSAGES);

// Not an end-user setting: changing a stored flag must never grant portal access.
export function portalAutomationDisabled(channel = RELEASE_CHANNEL) {
  if (!KNOWN_CHANNELS.has(channel)) return true;
  return channel !== "rc" && channel !== "alpha";
}

export function assertPortalMessageAllowed(messageType, channel = RELEASE_CHANNEL) {
  if (!KNOWN_CHANNELS.has(channel)) throw new Error("PRODUCT_RELEASE_CHANNEL_INVALID");
  if (portalAutomationDisabled(channel) && PORTAL_ACTIONS.has(String(messageType || ""))) {
    throw new Error("PORTAL_AUTOMATION_BLOCKED_IN_LOCAL_BETA");
  }
  return true;
}
