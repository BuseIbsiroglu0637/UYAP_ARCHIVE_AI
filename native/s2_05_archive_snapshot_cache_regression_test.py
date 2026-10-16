#!/usr/bin/env python3
"""S2-05: private, short-lived registry snapshot cache; no user archive required."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uyap_archive_agent as agent


def fake_snapshot():
    documents = [
        {
            "documentId": f"D{i:05d}", "caseId": "2026/115",
            "institutionName": "Sentetik Mahkeme",
            "documentTitle": f"Evrak {i:05d}", "extension": ".pdf",
            "status": "archived", "targetPath": f"Sentetik/{i:05d}.pdf"
        }
        for i in range(4192)
    ]
    return {
        "ok": True, "archiveExists": True, "totalRows": 12784,
        "uniqueFiles": len(documents), "caseIndex": [{"caseId": "2026/115"}],
        "registryWarnings": [], "documents": documents
    }


def test_read_only_browse_snapshot():
    agent._archive_browse_snapshot = None
    signatures = ["db0"]
    scans = []
    def build(message):
        scans.append(dict(message))
        return fake_snapshot()

    with patch.object(agent, "_build_archive_registry_snapshot", side_effect=build), patch.object(
        agent, "_archive_registry_snapshot_fingerprint", side_effect=lambda _root: signatures[0]
    ):
        root = "C:/synthetic"
        a = agent.list_archive_registry({"downloadsRoot": root, "browseCache": True, "summaryOnly": True})
        assert a["snapshotCached"] is False
        assert a["documents"] == [] and a["uniqueFiles"] == 4192
        assert len(scans) == 1

        first = agent.list_archive_registry({
            "downloadsRoot": root, "browseCache": True, "limit": 100
        })
        second = agent.list_archive_registry({
            "downloadsRoot": root, "browseCache": True, "limit": 100, "offset": 100
        })
        assert len(scans) == 1
        assert first["snapshotCached"] is True and second["snapshotCached"] is True
        assert first["matchedFiles"] == second["matchedFiles"] == 4192
        assert len(first["documents"]) == len(second["documents"]) == 100
        assert {d["documentId"] for d in first["documents"]}.isdisjoint(
            d["documentId"] for d in second["documents"]
        )
        assert "D00000" == first["documents"][0]["documentId"]
        assert second["documents"][0]["documentId"] == "D00100"

        # Consumer filters must NOT be part of the expensive disk snapshot build.
        filtered = agent.list_archive_registry({
            "downloadsRoot": root, "browseCache": True,
            "search": "Evrak 04191", "limit": 100
        })
        assert len(scans) == 1
        assert filtered["matchedFiles"] == 1
        assert filtered["documents"][0]["documentId"] == "D04191"

        # A test case explicitly requesting a new disk scan cannot get stale cached data.
        force = agent.list_archive_registry({
            "downloadsRoot": root, "browseCache": True,
            "summaryOnly": True, "forceRefresh": True
        })
        assert len(scans) == 2 and force["snapshotCached"] is False

        # SQLite/WAL or archive-root change invalidates the short-lived snapshot.
        signatures[0] = "wal1"
        fresh = agent.list_archive_registry({"downloadsRoot": root, "browseCache": True})
        assert len(scans) == 3 and fresh["snapshotCached"] is False

        # TTL also invalidates if nested physical files change without DB mutation.
        agent._archive_browse_snapshot["builtAt"] -= agent._ARCHIVE_BROWSE_CACHE_TTL_SECONDS + 5
        expired = agent.list_archive_registry({"downloadsRoot": root, "browseCache": True})
        assert len(scans) == 4 and expired["snapshotCached"] is False

        # Critical gates or callers that do not opt in ALWAYS get a fresh scan.
        unsafe = agent.list_archive_registry({"downloadsRoot": root, "limit": 1})
        assert len(scans) == 5 and unsafe["snapshotCached"] is False

        # A different root must never receive another archive's cached contents.
        other = agent.list_archive_registry({"downloadsRoot": root + "/other", "browseCache": True})
        assert len(scans) == 6 and other["snapshotCached"] is False

    agent._archive_browse_snapshot = None


def test_error_and_invalid_pages_never_leak_full_snapshot():
    agent._archive_browse_snapshot = None
    with patch.object(agent, "_build_archive_registry_snapshot", return_value=fake_snapshot()), patch.object(
        agent, "_archive_registry_snapshot_fingerprint", return_value=("stable",)
    ):
        try:
            agent.list_archive_registry({"browseCache": True, "offset": {"bad": "value"}})
        except ValueError as exc:
            assert str(exc) == "ARCHIVE_PAGE_INVALID"
        else:
            raise AssertionError("Bad page input not rejected")
        assert agent._archive_browse_snapshot is not None
        assert len(agent._archive_browse_snapshot["snapshot"]["documents"]) == 4192

    agent._archive_browse_snapshot = None
    with patch.object(agent, "_build_archive_registry_snapshot", return_value={"ok": False, "documents": [], "caseIndex": []}), patch.object(
        agent, "_archive_registry_snapshot_fingerprint", return_value=("stable",)
    ):
        err = agent.list_archive_registry({"browseCache": True, "summaryOnly": True})
        assert err["ok"] is False
        assert agent._archive_browse_snapshot is None


def test_browser_opt_in_contract():
    root = Path(__file__).resolve().parents[1]
    workspace = (root / "workspace/workspace.js").read_text(encoding="utf-8")
    worker = (root / "background/service-worker.js").read_text(encoding="utf-8")
    bridge = (root / "background/download-manager.js").read_text(encoding="utf-8")
    assert "summaryOnly:true,limit:1,browseCache:true" in workspace
    assert "limit:PAGE_SIZE,offset:state.archivePage*PAGE_SIZE,browseCache:true" in workspace
    assert "browseCache:request.browseCache===true" in worker
    assert "browseCache:browseCache===true,forceRefresh:forceRefresh===true" in bridge
    assert 'action:"list_archive_registry",downloadsRoot:root,...filters' in bridge


if __name__ == "__main__":
    test_read_only_browse_snapshot()
    test_error_and_invalid_pages_never_leak_full_snapshot()
    test_browser_opt_in_contract()
    print("S2-05 native archive snapshot cache: PASS (4192 synthetic documents)")
