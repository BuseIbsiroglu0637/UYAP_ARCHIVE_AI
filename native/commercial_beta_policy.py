"""S2-01: built-in channel gate for native archive actions.

RELEASE_CHANNEL is a distribution/build constant, not an environment,
user-editable JSON setting or request parameter. Before a public beta,
the signed packager must stamp the same channel into this module,
shared/commercial-beta-policy.mjs and release-contract.json.
"""

RELEASE_CHANNEL = "rc"
KNOWN_CHANNELS = frozenset({"alpha", "beta", "rc", "stable"})

# These actions are part of the portal-triggered ingestion/recovery pipeline.
# They must remain inaccessible from a local-only commercial beta even when
# an older extension sends a direct Native Messaging action.
PORTAL_NATIVE_ACTIONS = frozenset({
    "validate_download",
    "revalidate_quarantined",
    "finalize_archive",
    "sync_inventory",
    "discard_review_html",
})


def portal_automation_disabled(channel=RELEASE_CHANNEL):
    return channel not in ("rc", "alpha")


def assert_native_action_allowed(action, channel=RELEASE_CHANNEL):
    if channel not in KNOWN_CHANNELS:
        raise ValueError("PRODUCT_RELEASE_CHANNEL_INVALID")
    if portal_automation_disabled(channel) and action in PORTAL_NATIVE_ACTIONS:
        raise PermissionError("PORTAL_AUTOMATION_BLOCKED_IN_LOCAL_BETA")
    return True
