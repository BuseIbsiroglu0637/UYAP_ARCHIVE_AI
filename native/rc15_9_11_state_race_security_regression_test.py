from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DM = (ROOT / "background" / "download-manager.js").read_text("utf-8")
BRIDGE = (ROOT / "background" / "native-agent.js").read_text("utf-8")
POPUP = (ROOT / "popup" / "popup.js").read_text("utf-8")
CONSTANTS = (ROOT / "shared" / "constants.js").read_text("utf-8")
MANIFEST = (ROOT / "manifest.json").read_text("utf-8")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


# Health cleanup must be based on positive native evidence, never a UI refresh.
require('problem-center-refresh' not in DM, "problem center can still clear live native state")
for marker in (
    'reason:"native-local-inventory-success", forceDocuments:true',
    'reason:"native-validation-success", forceDocuments:true',
    'reason:"native-recovery-ping-success", forceDocuments:true',
):
    require(marker in DM, f"confirmed recovery does not force terminal cleanup: {marker}")

# A newer outage must win over an older success while a 12k-document scan runs.
require("const cleanupStartedAtMs = Date.now();" in DM, "cleanup start epoch missing")
require("nativeTransientFailureToken: crypto.randomUUID()" in DM, "native failure generation token missing")
require("session.nativeTransientFailureToken !== initialFailureToken" in DM, "newer failure token is not compared")
require("latestFailureAtMs > cleanupStartedAtMs" in DM, "newer failure race guard missing")
require("sessionRecoverySuperseded: true" in DM, "superseded cleanup diagnostic missing")

# Startup migration must preserve a live NATIVE_AGENT_RECOVERY state.
require("RELIABILITY_SCHEMA_VERSION = 5" in CONSTANTS, "schema v5 missing")
require('"NATIVE_AGENT_RECOVERY", "NATIVE_OPERATION_RECOVERY"' in DM, "active recovery migration guard missing")
require("if (!nativeRecoveryActive)" in DM, "migration still clears live recovery unconditionally")

# Native bridge must retain structured failures and settle every request on shutdown.
require('{...response, ok:response.ok === true' in BRIDGE, "structured native error fields are dropped")
require('errorCode:"NATIVE_AGENT_TIMEOUT"' in BRIDGE, "native timeout is not classified")
require('errorCode:"NATIVE_AGENT_DISCONNECTED"' in BRIDGE, "native disconnect is not classified")
require("for (const queued of lane.requestQueue.splice(0)) settleRequest(queued, disconnected);" in BRIDGE,
        "queued native requests can remain orphaned")

# UYAP-derived job labels/session identifiers must never be interpolated as HTML.
render_block = POPUP.split("function renderResumableJobs(){", 1)[1].split("async function loadResumableJobs", 1)[0]
require("row.innerHTML" not in render_block, "resumable job data still reaches innerHTML")
require("title.textContent=resumableJobLabel(job)" in render_block, "job title is not rendered as text")
require("button.dataset.resumeSession=String(job.sessionId" in render_block, "session id is not assigned through dataset")

require("RC15.9.37 QUEUE-RECOVERY" in MANIFEST, "release identity mismatch")
print("RC15.9.11 STATE RACE + UI SECURITY REGRESSION: PASS")
