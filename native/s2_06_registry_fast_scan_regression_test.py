#!/usr/bin/env python3
"""S2-06: archive registry scan without per-row resolve()/stat(); same answers.

Synthetic archive only (temporary folder, invented names); the user's real
archive is never touched because _archive_root_candidates is patched.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uyap_archive_agent as agent

INSTITUTION = "Sentetik Asliye Hukuk"
CASE_DIR = "2026_115_Dosya"
COLUMNS = (
    "session_id", "document_id", "case_id", "display_name", "uyap_path", "local_path",
    "size", "sha256", "status", "updated_at", "source_fingerprint", "source_system",
    "institution_name", "target_path",
)


def write(path: Path, size: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def build_archive(root: Path, bulk: int):
    archive = root / "UYAP_Arsiv"
    case = archive / INSTITUTION / CASE_DIR / "2026_115 Sentetik Dava"
    rows = []

    def row(n, rel, size, status="VERIFIED", local=None, session="S1"):
        target = f"UYAP_Arsiv/{INSTITUTION}/{CASE_DIR}/2026_115 Sentetik Dava/{rel}"
        rows.append({
            "session_id": session, "document_id": f"D{n:05d}", "case_id": "2026/115",
            "display_name": f"Evrak {n}", "uyap_path": "Evraklar", "size": size,
            "local_path": local if local is not None else str(root / target.replace("/", os.sep)),
            "sha256": None, "status": status, "updated_at": f"2026-10-01T10:{n % 60:02d}:00Z",
            "source_fingerprint": None, "source_system": "UYAP", "institution_name": INSTITUTION,
            "target_path": target,
        })

    write(case / "a_present.pdf", 10)
    row(1, "a_present.pdf", 10)
    row(1, "a_present.pdf", 10, session="S2")  # same file, second run
    write(case / "b_mismatch.pdf", 7)
    row(2, "b_mismatch.pdf", 99)
    row(3, "c_missing.pdf", 5)  # VERIFIED in DB, gone from disk
    write(case / "d_copied_db.pdf", 4)  # DB copied from another PC
    row(4, "d_copied_db.pdf", 4, local="D:\\Eski\\Downloads\\UYAP_Arsiv\\x\\d_copied_db.pdf")
    write(case / "e_unverified.udf", 3)
    row(5, "e_unverified.udf", 3, status="INVALID_CONTENT")
    for n in range(bulk):  # many rows pointing at the same few files
        row(100 + n, "a_present.pdf", 10, session=f"R{n}")
    write(case / "f_physical_only.pdf", 6)  # no DB row at all
    write(archive / "_incoming" / "g.part", 2)

    archive.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(archive / "_archive.db"))
    connection.execute(f"CREATE TABLE documents ({', '.join(COLUMNS)})")
    connection.executemany(
        f"INSERT INTO documents ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
        [tuple(item[name] for name in COLUMNS) for item in rows],
    )
    connection.commit()
    connection.close()
    return rows


def test_lexical_target_matches_safe_target(root: Path):
    for rel in ("UYAP_Arsiv/a/b.pdf", f"UYAP_Arsiv/{INSTITUTION}/{CASE_DIR}/x y.pdf"):
        fast = agent._lexical_archive_target(root, rel)
        legacy = agent.safe_target(root, rel)
        assert os.path.normcase(str(fast)) == os.path.normcase(str(legacy)), (fast, legacy)
    for bad in ("../x.pdf", "/etc/x", "UYAP_Arsiv/../../x", "C:/x.pdf", "a\\b.pdf", ""):
        for resolver in (agent.safe_target, agent._lexical_archive_target):
            try:
                resolver(root, bad)
            except ValueError:
                continue
            raise AssertionError(f"{resolver.__name__} accepted {bad!r}")


def test_row_paths_match_legacy(root: Path, rows):
    probe = agent._ArchiveFileProbe([(root, root / "UYAP_Arsiv")])
    for item in rows[:7]:
        legacy = agent._registry_row_path(root, item)
        fast = agent._registry_row_path(root, item, probe)
        assert os.path.normcase(str(legacy)) == os.path.normcase(str(fast)), (legacy, fast)
        info = probe(fast)
        assert legacy.is_file() == (info is not None), legacy
        if info is not None:
            assert info.st_size == legacy.stat().st_size


def test_probe_fallback(root: Path):
    probe = agent._ArchiveFileProbe([(root, root / "UYAP_Arsiv")])
    beside = write(root / "evrak_1234567.pdf", 8)  # outside UYAP_Arsiv: not walked
    assert probe(beside) is not None and probe(beside).st_size == 8
    assert probe(root / "yok.pdf") is None
    assert probe(root / "UYAP_Arsiv") is None  # a folder is not a file
    late = write(root / "UYAP_Arsiv" / "late" / "x" / "y.pdf", 3)  # appeared after the walk
    assert probe(late) is not None


def test_snapshot_without_per_row_resolve(root: Path, rows, bulk: int):
    real_resolve = Path.resolve
    calls = []

    def counting(self, *args, **kwargs):
        calls.append(1)
        return real_resolve(self, *args, **kwargs)

    with patch.object(agent, "_archive_root_candidates", return_value=[root]), patch.object(Path, "resolve", counting):
        snap = agent._build_archive_registry_snapshot({"downloadsRoot": str(root)})
    assert snap["ok"] is True and snap["totalRows"] == len(rows)
    assert len(calls) <= 3, f"resolve() called {len(calls)} times for {len(rows)} rows"
    by_name = {Path(item["targetPath"]).name: item for item in snap["documents"]}
    assert by_name["a_present.pdf"]["status"] == "archived"
    assert by_name["a_present.pdf"]["runCount"] == 2 + bulk
    assert by_name["b_mismatch.pdf"]["status"] == "review"
    assert by_name["b_mismatch.pdf"]["physicalState"] == "size_mismatch"
    assert by_name["c_missing.pdf"]["status"] == "missing"
    assert by_name["d_copied_db.pdf"]["status"] == "archived"
    assert by_name["e_unverified.udf"]["status"] == "review"
    assert by_name["f_physical_only.pdf"]["nativeStatus"] == "PHYSICAL_FILE_DISCOVERED"
    assert by_name["g.part"]["status"] == "partial"
    case = snap["caseIndex"][0]
    assert case["missingCount"] == 1 and case["unregisteredPhysicalCount"] == 1
    return len(calls)


if __name__ == "__main__":
    bulk_rows = 2000
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp).resolve()
        made = build_archive(base, bulk_rows)
        test_lexical_target_matches_safe_target(base)
        test_row_paths_match_legacy(base, made)
        test_snapshot_without_per_row_resolve(base, made, bulk_rows)
        resolve_calls = test_snapshot_without_per_row_resolve(base, made, bulk_rows)
        test_probe_fallback(base)
    print(f"S2-06 registry fast scan: PASS ({len(made)} synthetic rows, {resolve_calls} resolve call(s))")
