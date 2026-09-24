#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("uyap_archive_agent", HERE / "uyap_archive_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def req(value, message):
    if not value:
        raise AssertionError(message)


req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
req("portable_archive_resume_v1" in agent.dispatch({"action": "ping"})["capabilities"], "capability missing")
manager = (HERE.parent / "background" / "download-manager.js").read_text(encoding="utf-8")
worker = (HERE.parent / "background" / "service-worker.js").read_text(encoding="utf-8")
popup = (HERE.parent / "popup" / "popup.js").read_text(encoding="utf-8")
req("useSelectedArchiveRoot: pipelineSession?.portableResumeEnabled === true" in manager, "validation root flag missing")
req("selectExistingArchive" in worker and "MESSAGE.SELECT_EXISTING_ARCHIVE" in worker, "service worker route missing")
req("Başka bilgisayardaki arşivden devam et" in (HERE.parent / "popup" / "popup.html").read_text(encoding="utf-8"), "popup action missing")
req("SELECT_EXISTING_ARCHIVE" in popup, "popup message missing")

with tempfile.TemporaryDirectory() as temporary:
    base = Path(temporary)
    old_root = base / "old-computer-export"
    old_archive = old_root / "UYAP_Arsiv"
    old_archive.mkdir(parents=True)
    (old_archive / "existing-case").mkdir()
    (old_archive / "existing-case" / "existing.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

    config = base / "state" / "portable-resume-root.json"
    agent._portable_resume_config_path = lambda: config
    saved = agent.save_selected_archive_root(old_archive)
    req(Path(saved["downloadsRoot"]) == old_root.resolve(), "selected UYAP_Arsiv must normalize to parent root")
    req(agent.load_selected_archive_root() == old_root.resolve(), "selected root must persist across native processes")

    browser_root = base / "browser-downloads"
    relative = "UYAP_Arsiv/_incoming/session-1/document-1.part"
    source = browser_root / Path(*relative.split("/"))
    source.parent.mkdir(parents=True)
    source.write_bytes(b"portable-resume-bytes")
    moved, root = agent.normalize_source_to_staging(source.resolve(), {
        "stagingPath": relative,
        "useSelectedArchiveRoot": True,
    })
    expected = old_root / Path(*relative.split("/"))
    req(root == old_root.resolve(), "validation must use selected archive root")
    req(moved == expected.resolve() and moved.read_bytes() == b"portable-resume-bytes", "browser bytes must move into selected archive staging")
    req(not source.exists(), "browser-root staging file must not be left split across roots")

    # Retry after the first native process moved the file: original Chrome path
    # is gone but the selected archive staging bytes remain recoverable.
    replay, replay_root = agent.normalize_source_to_staging(source.resolve(), {
        "stagingPath": relative,
        "useSelectedArchiveRoot": True,
    })
    req(replay == expected.resolve() and replay_root == old_root.resolve(), "cross-process replay must find selected staging")

    canonical = browser_root / "evrak_1200123456.pdf"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"%PDF-1.4\n%%EOF\n")
    canonical_message = {
        "stagingPath": "UYAP_Arsiv/_incoming/session-2/document-2.part",
        "useSelectedArchiveRoot": True,
        "allowCanonicalAdoption": True,
        "directDownloadEvidence": True,
        "browserDownloadId": 77,
        "browserState": "complete",
        "attemptId": "attempt-2",
        "sessionId": "session-2",
        "document": {
            "downloadId": 77, "bindingMethod": "extension-download-id",
            "attemptId": "attempt-2", "sessionId": "session-2"
        }
    }
    adopted, adopted_root = agent.normalize_source_to_staging(canonical.resolve(), canonical_message)
    req(adopted_root == old_root.resolve(), "direct browser fallback must still use selected archive")
    req(adopted.is_file() and old_root.resolve() in adopted.parents, "canonical fallback split the archive root")

    empty = base / "empty"
    (empty / "UYAP_Arsiv").mkdir(parents=True)
    try:
        agent.resolve_existing_archive_selection(empty)
        raise AssertionError("empty archive selection accepted")
    except ValueError:
        pass

print("RC15.9.23 portable archive resume regression: PASS")
