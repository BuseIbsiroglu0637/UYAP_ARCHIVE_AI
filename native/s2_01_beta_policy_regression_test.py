#!/usr/bin/env python3
"""S2-01: executable security tests, no live UYAP portal or personal documents."""
import json
import re
from pathlib import Path

import commercial_beta_policy as policy
import uyap_archive_agent as agent

ROOT = Path(__file__).resolve().parents[1]


def test_product_profiles():
    channel = json.loads(
        (ROOT / "config/release-contract.json").read_text(encoding="utf-8")
    )["release"]["channel"]
    js = (ROOT / "shared/commercial-beta-policy.mjs").read_text(encoding="utf-8")
    assert channel == policy.RELEASE_CHANNEL
    assert f'export const RELEASE_CHANNEL = "{channel}";' in js
    assert policy.portal_automation_disabled("beta")
    assert policy.portal_automation_disabled("stable")
    assert policy.portal_automation_disabled("unknown-channel")
    assert not policy.portal_automation_disabled("rc")
    assert not policy.portal_automation_disabled("alpha")


def test_beta_denies_native_write_actions():
    for action in policy.PORTAL_NATIVE_ACTIONS:
        try:
            policy.assert_native_action_allowed(action, "beta")
        except PermissionError as error:
            assert str(error) == "PORTAL_AUTOMATION_BLOCKED_IN_LOCAL_BETA"
        else:
            raise AssertionError(action + " must be denied")

    for action in (
        "ping", "list_archive_registry", "select_existing_archive",
        "get_selected_archive", "open_archive_folder", "format_audit",
    ):
        assert policy.assert_native_action_allowed(action, "beta")

    try:
        policy.assert_native_action_allowed("ping", "unknown")
    except ValueError as exc:
        assert "PRODUCT_RELEASE_CHANNEL_INVALID" in str(exc)
    else:
        raise AssertionError("invalid product channels must fail closed")


def test_native_dispatch_denies_even_forged_legacy_action():
    old = agent.assert_native_action_allowed
    agent.assert_native_action_allowed = lambda act: policy.assert_native_action_allowed(
        act, "beta"
    )
    try:
        for action in policy.PORTAL_NATIVE_ACTIONS:
            try:
                agent.dispatch({"action": action, "downloadsRoot": "UNSAFE", "sessionId": "UNSAFE"})
            except PermissionError as exc:
                assert "PORTAL_AUTOMATION_BLOCKED_IN_LOCAL_BETA" in str(exc)
            else:
                raise AssertionError(f"Native dispatched {action!r} in beta")
        result = agent.dispatch({"action": "ping", "probe": "S2-01"})
        assert result["ok"] and result["echo"] == "S2-01"
    finally:
        agent.assert_native_action_allowed = old


def test_packaged_native_policy_and_signed_beta_build_gate():
    installer = (ROOT / "native/portable_installer.ps1").read_text(encoding="utf-8")
    setup = (ROOT / "native/build_offline_setup.ps1").read_text(encoding="utf-8")
    assert "$BetaPolicySource" in installer
    assert "commercial_beta_policy.py" in installer
    assert "Copy-Item -LiteralPath $BetaPolicySource" in installer
    assert "S2-01 release policy module missing" in setup
    assert "Release channel mismatch" in setup
    assert "Unsigned commercial beta/stable build blocked" in setup
    assert "Commercial local-only release cannot contain UYAP portal host permissions" in setup
    # Regression guard: regex matches 'uyap.gov.tr' literally, not a backslash.
    assert r"'uyap\.gov\.tr'" in setup


if __name__ == "__main__":
    test_product_profiles()
    test_beta_denies_native_write_actions()
    test_native_dispatch_denies_even_forged_legacy_action()
    test_packaged_native_policy_and_signed_beta_build_gate()
    print("S2-01 local beta policy regression: PASS")
