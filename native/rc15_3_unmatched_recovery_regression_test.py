from pathlib import Path
import importlib.util
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "native" / "uyap_archive_agent.py"
DM = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
SW = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
PATHS = (ROOT / "shared" / "path-utils.js").read_text(encoding="utf-8")


def load_agent():
    spec = importlib.util.spec_from_file_location("uyap_agent_rc153", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    agent = load_agent()
    assert tuple(map(int, agent.AGENT_VERSION.split("."))) >= (2, 7, 35)

    # Native adoption: only the exact session/attempt-scoped _unmatched path may
    # be promoted into the normal _incoming staging path.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        session = "session-123"
        attempt = "attempt-456"
        unmatched_rel = f"UYAP_Arsiv/_unmatched/{session}/{attempt}__evrak_13729417527.tif"
        staging_rel = f"UYAP_Arsiv/_incoming/{session}/{attempt}.part"
        source = root / unmatched_rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"II*\x00dummy")
        moved, discovered_root = agent.normalize_source_to_staging(source.resolve(), {
            "sessionId": session,
            "attemptId": attempt,
            "stagingPath": staging_rel,
            "allowUnmatchedAdoption": True,
            "provisionalUnmatchedPath": unmatched_rel,
            "allowCanonicalAdoption": False,
        })
        assert discovered_root == root.resolve()
        assert moved == (root / staging_rel).resolve()
        assert moved.is_file() and not source.exists()

        bad_source = root / f"UYAP_Arsiv/_unmatched/{session}/wrong__evrak_1.pdf"
        bad_source.parent.mkdir(parents=True, exist_ok=True)
        bad_source.write_bytes(b"%PDF-1.3\n%%EOF\n")
        try:
            agent.normalize_source_to_staging(bad_source.resolve(), {
                "sessionId": session,
                "attemptId": attempt,
                "stagingPath": staging_rel,
                "allowUnmatchedAdoption": True,
                "provisionalUnmatchedPath": unmatched_rel,
                "allowCanonicalAdoption": False,
            })
        except ValueError:
            pass
        else:
            raise AssertionError("wrong unmatched path must remain fail-closed")

    # Extension side: unmatched downloads are quarantined, correlated again on
    # completion, and old canonical leaks get a one-candidate recovery pass.
    assert "buildUnmatchedPath" in PATHS
    assert '"_unmatched"' in PATHS
    assert "determineProvisionalBrowserFilename" in SW
    assert "DOWNLOAD_ROUTED_TO_UNMATCHED" in DM
    assert "UNMATCHED_DOWNLOAD_CORRELATED" in DM
    assert "UNMATCHED_DOWNLOAD_PRESERVED" in DM
    assert "recoverStrandedCanonicalDownload" in DM
    assert "recoverStrandedCanonicalDownloadsAtStart" in DM
    assert "STRAY_BROWSER_DOWNLOAD_ADOPTED" in DM
    assert "STRAY_DOWNLOAD_STARTUP_RECOVERY" in DM
    assert "allowUnmatchedAdoption" in DM
    assert "provisionalUnmatchedPath" in DM
    print("RC15.3 unmatched recovery regression PASS")


if __name__ == "__main__":
    main()
