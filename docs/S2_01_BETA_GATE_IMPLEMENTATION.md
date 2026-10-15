# Sprint 2 — S2-01 | Local-only Beta Portal Safety Gate (implementation draft)

**State:** CODED ON FEATURE BRANCH / REVIEW REQUESTED / NOT RELEASED.  
**Created:** 2026-10-15.  
**Product decision:** DEC-S1-001: local archive first, AI optional.  
**Base:** `feature/phase-1-product-foundation` (currently draft PR #1 toward `develop`).
**Reason for base:** `develop` does not yet contain the Phase 1 release contract/installer policy that this change must validate. No code is merged to `develop`, RC release or `main`.

## What this change implements

1. **Build-time release policy:** `shared/commercial-beta-policy.mjs` and `native/commercial_beta_policy.py` compile the current `RELEASE_CHANNEL = "rc"`. Pure assertions simulate the `beta` channel, where all portal automation entry points fail closed.
2. **Extension service-worker:** For `beta`, incoming portal scan/create/start/resume/retry/Kurum navigation/repackage/runtime messages are rejected. Startup recovery, watchdog alarm handling, Chrome download routing and portal network/navigation observers do not start their existing side-effect machinery. Read-only/local archive actions remain candidates for the beta flow.
3. **Popup:** Beta runtime policy is displayed; portal action buttons disabled, Archive Workspace and existing local archive selection stay available.
4. **Native Agent:** For `beta`, download validation, inventory reconciliation, finalization and quarantine operations are rejected before dispatcher execution. A ping advertises its compiled channel. Beta extension additionally refuses to access local filesystem functions through an incompatible legacy/RC Native Agent.
5. **Portable Python runtime:** Bundles `commercial_beta_policy.py` with `uyap_archive_agent.py` to avoid a missing import in the installed host.
6. **Installer preflight:** `native/build_offline_setup.ps1` rejects differences between release contract and compiled JS/Python profile; beta/stable builds stop if production code signing is not implemented; beta/stable also reject the current UYAP portal host permissions/content-script manifest.

## Compatibility and honest limits

- **Current repository release is still `rc`**, not beta. In RC/alpha legacy mode the previous portal behavior remains unchanged; NO commercial beta installation was produced or installed in a customer device.
- **Current beta manifest is not ready:** existing `manifest.json` still requests `*.uyap.gov.tr` host permissions and portal content scripts. Package preflight intentionally rejects it for a local-only beta until a separate least-privilege beta manifest is implemented.
- **Code signing is not implemented:** beta/stable build preflight deliberately stops until signing pipeline is actually available.
- **General-purpose local folder import is not guaranteed:** existing archive-folder restore/read features are preserved; S2-04 remains a separate product task.
- **AI remains optional and out of scope for this PR**, and the later `feature/ai-six-workflows` branch is not merged into this code.
- **Browser UI negative tests and actual Windows Native Agent beta tests are not done:** source smoke tests and simulated beta denylisting are engineering evidence only, not external pilot acceptance.
- Older external extensions or non-product host copies must be inventoried/uninstalled/retired in a controlled migration. This feature branch cannot manage all independently installed old clients.
- Native `repackage_archive` is blocked until finalization is split from portal-driven download routines; harmless local repackage is not claimed yet.

## Tests

**Automated, newly wired:**
- `node --test native/s2_01_beta_policy.test.mjs`: denylist, local allowlist, recovery/observer guards, UI wording, native-match source checks.
- `python native/s2_01_beta_policy_regression_test.py`: Python denylist, simulated Native dispatch, packaging preconditions, compiled release-profile consistency.
- Both added to Linux `Offline source and regression smoke`; Python test also to `Windows installer lifecycle smoke`.

**Next acceptance:**
- Build a `beta` release with compiled profile and extension manifest stripped of portal permissions/scripts; only after signing readiness, rerun E2E in isolated environment.
- Replay forged/legacy runtime messages, automatic startup alarms, native action requests and old-agent mismatch with sentinels.
- Verify no new UYAP requests are sent in beta (network trace, no real portal automation tests without proper use authorization).
- Verify selecting existing local archive still works without UYAP portal, internet or LM Studio.
- Validate clean Windows 11 + Chrome/Edge and signed N-1→N / failure rollback before any external distribution.
- Record signed test proof under `docs/productization/S1_BETA_TEST_RUN_TEMPLATE.yaml` (S1-06).

## Outstanding related work

- **S2-02 (P0):** Packaging allowlist instead of broad `Copy-Item $PackageRoot`; no Git/private files in the package.
- **S2-03 (P0):** Diagnostic masking/redaction; existing GitHub issue #6.
- **S2-04 (P0):** Portal-independent import and case matching acceptance.
- **S2-05 (P0):** Signed Windows hardware acceptance; existing issue #4.
- **S1-03:** Legal/portal permission review; existing product documentation.
- **S1-04:** KVKK, sensitive log and provider data-transfer review.

**Conclusion:** S2-01 code slice has a reviewable implementation and simulated fail-closed tests. Actual commercial beta safety is **NOT YET ACCEPTED**.
