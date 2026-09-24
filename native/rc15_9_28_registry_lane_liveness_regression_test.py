#!/usr/bin/env python3
from pathlib import Path

HERE = Path(__file__).resolve().parent
bridge = (HERE.parent / "background" / "native-agent.js").read_text(encoding="utf-8")
manager = (HERE.parent / "background" / "download-manager.js").read_text(encoding="utf-8")


def req(value, message):
    if not value:
        raise AssertionError(message)


req('registry: createLane("registry")' in bridge, "registry lane missing")
req('REGISTRY_ACTIONS = new Set(["list_archive_registry", "select_existing_archive"])' in bridge,
    "registry actions are not isolated")
req('if (REGISTRY_ACTIONS.has(normalized)) return nativeLanes.registry;' in bridge,
    "registry routing missing")
req('if (action === "sync_inventory") return 5 * 60_000;' in bridge,
    "sync inventory timeout must be bounded")
req('if (action === "list_archive_registry") return 2 * 60_000;' in bridge,
    "registry timeout must allow large DB scans")
req('await ensureLocalArchiveBatchReconciled(sessionId, pipelineBatch' in manager,
    "batch reconciliation guard missing")
req('AUTO_PIPELINE_BATCH_RECONCILE_FAILED' in manager,
    "failed reconciliation must release the queue")

print("RC15.9.28 registry-lane queue liveness regression: PASS")
