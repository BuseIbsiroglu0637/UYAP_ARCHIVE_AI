"""Saved AI results: rule views beside Dosya Özeti, delete with undo, no resurrection.

All case details are synthetic.
"""
from pathlib import Path
import json
import os
import shutil
import tempfile
import time

from uyap_archive_agent import (
    delete_ai_summary, get_ai_summary, list_ai_summaries, restore_ai_summary, save_ai_summary,
)

CASE = {"institutionName": "Örnekköy 3. Aile Mahkemesi", "caseId": "2026/45"}
DATES_SECTIONS = [
    {"key": "upcoming", "title": "Yaklaşan", "highlight": True,
     "items": [{"date": "2026-11-11", "dateText": "11.11.2026 saat 10:50", "text": "Ön inceleme duruşması — 28 gün sonra",
                "refs": ["D000009"], "moreRefs": 0, "kind": "hearing"}]},
    {"key": "coverage", "title": "Kapsam", "highlight": False,
     "items": [{"dateText": "", "text": "14 evrakın tamamı tarandı.", "refs": [], "moreRefs": 0, "kind": "note"}]},
]


def _case(root, **extra):
    return {"downloadsRoot": str(root), **CASE, **extra}


def _summary(root):
    return _case(root, answer="KÜNYE\n- Mahkeme / dosya: Örnekköy 3. Aile Mahkemesi · 2026/45 Esas [D000001]",
                 sources=[{"sourceRef": "D000001", "title": "Dava Dilekçesi"}], sourceRefs=["D000001"],
                 provider="lmstudio", model="yerel-model", retrievalMode="case_summary_v1")


def _dates(root, answer="YAKLAŞAN\n- 11.11.2026 saat 10:50 · Ön inceleme duruşması — 28 gün sonra [D000009]"):
    sources = [{"sourceRef": f"D{index:06d}", "title": f"Evrak {index}", "documentDate": "2026-09-11"}
               for index in range(1, 41)]
    return _case(root, workflowId="important_dates", answer=answer, sections=DATES_SECTIONS,
                 workflowNotice="Tarihler kaynaktan kurallarla çıkarıldı.", sources=sources,
                 sourceRefs=[s["sourceRef"] for s in sources], provider="Yerel kurallar",
                 retrievalMode="important_dates_rules_v2")


def _with_store(test):
    def run():
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "UYAP_Arsiv").mkdir(parents=True)
            previous = os.environ.get("UYAP_AI_SUMMARY_STORE_ROOT")
            os.environ["UYAP_AI_SUMMARY_STORE_ROOT"] = str(root / "central")
            try:
                test(root)
            finally:
                if previous is None:
                    os.environ.pop("UYAP_AI_SUMMARY_STORE_ROOT", None)
                else:
                    os.environ["UYAP_AI_SUMMARY_STORE_ROOT"] = previous
    run.__name__ = test.__name__
    return run


@_with_store
def test_rule_views_are_saved_listed_light_and_reopened_whole(root):
    summary = save_ai_summary(_summary(root))["summary"]
    dates = save_ai_summary(_dates(root))
    assert dates["deduplicated"] is False and dates["summary"]["summaryId"].startswith("dates-"), dates
    assert save_ai_summary(_dates(root))["deduplicated"] is True
    # The same text as another workflow is a different record.
    orders = save_ai_summary({**_dates(root), "workflowId": "court_orders"})["summary"]
    assert orders["summaryId"].startswith("orders-") and orders["workflowTitle"] == "Ara Kararlar ve Yapılacaklar"

    listing = list_ai_summaries(_case(root, limit=20))
    by_id = {row["summaryId"]: row for row in listing["summaries"]}
    assert listing["count"] == 3, listing
    assert by_id[summary["summaryId"]]["workflowId"] == "case_summary"
    listed = by_id[dates["summary"]["summaryId"]]
    assert (listed["workflowId"], listed["workflowTitle"], listed["hasSections"]) == (
        "important_dates", "Önemli Tarihler", True), listed
    assert "sections" not in listed and listed["answer"] is None, "lists stay small"
    assert len(listed["sources"]) == 40, "rule views keep every source for the PDF table"

    whole = get_ai_summary(_case(root, summaryId=listed["summaryId"]))["summary"]
    assert whole["sections"] == DATES_SECTIONS and whole["sectionsSchema"] == "important_dates_v2", whole
    assert whole["workflowNotice"] == "Tarihler kaynaktan kurallarla çıkarıldı."
    assert get_ai_summary(_case(root, summaryId="nope"))["ok"] is False

    for bad in ({**_dates(root), "workflowId": "chronology"}, {**_dates(root), "sections": []}):
        try:
            save_ai_summary(bad)
        except ValueError:
            continue
        raise AssertionError(f"must be refused: {bad['workflowId']}")


@_with_store
def test_delete_moves_both_copies_to_trash_and_undo_restores_them(root):
    saved = save_ai_summary(_dates(root))["summary"]
    summary_id = saved["summaryId"]
    central = Path(saved["path"]).parent
    mirror = next((root / "UYAP_Arsiv" / "_ai" / "summaries").iterdir())
    assert (mirror / f"{summary_id}.json").is_file(), "the archive keeps a mirror copy"

    result = delete_ai_summary(_case(root, summaryId=summary_id))
    assert result["ok"] is True and result["mirrorRemoved"] is True, result
    assert not (central / f"{summary_id}.json").exists() and (central / ".trash" / f"{summary_id}.json").is_file()
    assert not (mirror / f"{summary_id}.json").exists() and (mirror / ".trash" / f"{summary_id}.json").is_file()
    assert list_ai_summaries(_case(root))["count"] == 0
    assert get_ai_summary(_case(root, summaryId=summary_id))["ok"] is False
    assert delete_ai_summary(_case(root, summaryId=summary_id))["ok"] is False

    restored = restore_ai_summary(_case(root, summaryId=summary_id))
    assert restored["ok"] is True and restored["summary"]["summaryId"] == summary_id, restored
    assert (central / f"{summary_id}.json").is_file() and (mirror / f"{summary_id}.json").is_file()
    assert [row["summaryId"] for row in list_ai_summaries(_case(root))["summaries"]] == [summary_id]
    assert restore_ai_summary(_case(root, summaryId=summary_id))["ok"] is False


@_with_store
def test_a_deleted_record_never_comes_back_from_the_archive_mirror(root):
    saved = save_ai_summary(_summary(root))["summary"]
    summary_id = saved["summaryId"]
    mirror = next((root / "UYAP_Arsiv" / "_ai" / "summaries").iterdir())
    copy = mirror.parent / "leftover.json"
    shutil.copy2(mirror / f"{summary_id}.json", copy)
    delete_ai_summary(_case(root, summaryId=summary_id))
    # A mirror copy the delete could not reach (archive offline, older copy) must not be re-imported.
    shutil.move(str(copy), str(mirror / f"{summary_id}.json"))
    assert list_ai_summaries(_case(root))["count"] == 0
    again = save_ai_summary(_summary(root))
    assert again["deduplicated"] is False and again["summary"]["summaryId"] != summary_id, again
    assert [row["summaryId"] for row in list_ai_summaries(_case(root))["summaries"]] == [again["summary"]["summaryId"]]


@_with_store
def test_trash_is_purged_after_a_week(root):
    saved = save_ai_summary(_dates(root))["summary"]
    summary_id = saved["summaryId"]
    central = Path(saved["path"]).parent
    delete_ai_summary(_case(root, summaryId=summary_id))
    trashed = central / ".trash" / f"{summary_id}.json"
    week = time.time() - 8 * 86400
    os.utime(trashed, (week, week))
    list_ai_summaries(_case(root))
    assert not trashed.exists(), "trash older than a week is removed"
    assert restore_ai_summary(_case(root, summaryId=summary_id))["ok"] is False
    log = json.loads((central / "_deleted.json").read_text(encoding="utf-8"))
    assert summary_id in log["deleted"], "the deletion stays recorded"


if __name__ == "__main__":
    test_rule_views_are_saved_listed_light_and_reopened_whole()
    test_delete_moves_both_copies_to_trash_and_undo_restores_them()
    test_a_deleted_record_never_comes_back_from_the_archive_mirror()
    test_trash_is_purged_after_a_week()
    print("Saved results regression: PASS")
