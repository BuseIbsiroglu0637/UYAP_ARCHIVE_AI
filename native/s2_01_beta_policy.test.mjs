import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {
  RELEASE_CHANNEL,
  portalAutomationDisabled,
  assertPortalMessageAllowed,
  PORTAL_AUTOMATION_MESSAGES
} from "../shared/commercial-beta-policy.mjs";

const root = new URL("../", import.meta.url);
const read = (p) => readFileSync(new URL(p, root), "utf8");
const release = JSON.parse(read("config/release-contract.json"));

test("built release matches extension and native channel", () => {
  assert.equal(RELEASE_CHANNEL, release.release.channel);
  assert.equal(RELEASE_CHANNEL, "rc"); // source branch remains historical RC; beta builder must retarget.
  assert.equal(portalAutomationDisabled("rc"), false);
  assert.equal(portalAutomationDisabled("alpha"), false);
  assert.equal(portalAutomationDisabled("beta"), true);
  assert.equal(portalAutomationDisabled("stable"), true);
  assert.equal(portalAutomationDisabled("invalid"), true);
});

test("beta blocks every privileged runtime entry and does not trust sender toggles", () => {
  assert.ok(PORTAL_AUTOMATION_MESSAGES.length >= 12);
  for (const message of PORTAL_AUTOMATION_MESSAGES) {
    assert.throws(() => assertPortalMessageAllowed(message, "beta"),
      /PORTAL_AUTOMATION_BLOCKED_IN_LOCAL_BETA/, message);
    assert.equal(assertPortalMessageAllowed(message, "rc"), true);
  }
  for (const message of [
    "get_product_policy", "ping_agent", "get_archive_registry",
    "select_existing_archive", "get_selected_archive",
    "get_active_session", "get_report_data", "open_archive_folder"
  ]) {
    assert.equal(assertPortalMessageAllowed(message, "beta"), true, message);
  }
  assert.throws(() => assertPortalMessageAllowed("get_archive_registry", "something-else"),
    /PRODUCT_RELEASE_CHANNEL_INVALID/);
});

test("beta worker has no automatic recovery, download routing or navigation side effects", () => {
  const worker = read("background/service-worker.js");
  assert.match(worker, /assertPortalMessageAllowed\(request\?\.type\)/);
  assert.match(worker, /if \(LOCAL_ONLY_BETA\) return Promise\.resolve\(null\)/);
  assert.match(worker, /if \(LOCAL_ONLY_BETA\) return; \/\/ Old persisted queue/);
  assert.match(worker, /if \(LOCAL_ONLY_BETA\) \{ suggest\(\); return; \}/);
  assert.match(worker, /if \(!LOCAL_ONLY_BETA\) void handleDownloadCreated/);
  assert.match(worker, /if \(!LOCAL_ONLY_BETA\) void handleDownloadChanged/);
  assert.match(worker, /if \(!LOCAL_ONLY_BETA\) void observeUyapNetworkRequest/);
  assert.match(worker, /if \(!LOCAL_ONLY_BETA\) void handleUyapNetworkRedirect/);
  assert.match(worker, /if \(!LOCAL_ONLY_BETA\) void handleUyapNetworkCompleted/);
  assert.match(worker, /case 'get_product_policy':/);
  assert.match(worker, /const nativeStatus = await pingNativeAgent\(\);/);
  assert.match(worker, /NATIVE_PRODUCT_PROFILE_MISMATCH/);
  assert.match(read("background/native-agent.js"), /NATIVE_PRODUCT_PROFILE_MISMATCH/);
});

test("beta popup explains restrictions and keeps local archive browsing", () => {
  const popup = read("popup/popup.js");
  assert.match(popup, /void sendRuntime\("get_product_policy"\)/);
  assert.match(popup, /if\(localOnlyBeta\)\{/);
  assert.match(popup, /el\.primaryButton\.disabled=true/);
  assert.match(popup, /el\.rebindButton\.disabled=true/);
  assert.match(popup, /el\.newJobButton\.disabled=true/);
  assert.match(popup, /"Yerel arşiv kullanılabilir"/);
  assert.match(popup, /sendRuntime\(MESSAGE\.SELECT_EXISTING_ARCHIVE\)/);
});
