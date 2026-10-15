#!/usr/bin/env python3
"""S2-04 regression: 12,784-record archives stay below 1 MiB Native Messaging cap."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uyap_archive_agent import _archive_page_filter, MAX_RESPONSE_BYTES


def synthetic_records():
    return [
        {
            "documentId": f"D{i:06d}", "caseId": "2026/115" if i < 520 else "2026/987",
            "institutionName": "Sentetik 2. Mahkeme" if i < 520 else "Sentetik Icra",
            "documentTitle": f"Evrak-{i:05d}", "targetPath": f"Sentetik/2026_{115 if i<520 else 987}/Evrak-{i:05d}.pdf",
            "extension": ".pdf" if i % 5 else ".udf",
            "status": "archived" if i < 12743 else "review",
            "sha256": "a" * 64, "size": 123456, "physicalProofLevel": "size"
        } for i in range(12784)
    ]


def test_paginated_large_archive():
    docs = synthetic_records()
    page0 = _archive_page_filter(docs, {"limit": 20000})
    assert len(page0["documents"]) == 100
    assert page0["matchedFiles"] == 12784 and page0["hasMore"]
    assert len(json.dumps(page0, ensure_ascii=False).encode("utf-8")) < MAX_RESPONSE_BYTES

    page1 = _archive_page_filter(docs, {"offset": 100, "limit": 100})
    assert len(page1["documents"]) == 100
    assert {x["documentId"] for x in page0["documents"]}.isdisjoint(
        x["documentId"] for x in page1["documents"]
    )
    last = _archive_page_filter(docs, {"offset": 12700, "limit": 100})
    assert len(last["documents"]) == 84 and not last["hasMore"]
    assert last["matchedFiles"] == 12784
    assert all(x["documentId"].startswith("D") for x in last["documents"])

    summary = _archive_page_filter(docs, {"summaryOnly": True, "limit": 20000})
    assert summary["documents"] == [] and summary["matchedFiles"] == 12784

    case = _archive_page_filter(docs, {
        "institutionName": "Sentetik 2. Mahkeme", "caseId": "2026_115", "offset": 500
    })
    assert case["matchedFiles"] == 520
    assert len(case["documents"]) == 20
    assert case["documents"][0]["documentId"] == "D000500"

    missing = _archive_page_filter(docs, {"status":"review"})
    assert missing["matchedFiles"] == 41
    assert len(missing["documents"]) == 41

    local = _archive_page_filter(docs, {"status":"local_present"})
    assert local["matchedFiles"] == 12784

    udf = _archive_page_filter(docs, {"extension":".udf", "search":"Evrak-00010"})
    assert udf["matchedFiles"] == 1 and udf["documents"][0]["documentId"]=="D000010"
    beyond = _archive_page_filter(docs, {"offset": 999999})
    assert beyond["documents"] == [] and not beyond["hasMore"]


def test_bad_offsets_fail_closed():
    docs = synthetic_records()[:1]
    for bad in ("not_an_integer", {}, []):
        try:
            _archive_page_filter(docs, {"offset":bad})
        except ValueError as error:
            assert "ARCHIVE_PAGE_INVALID" in str(error)
        else:
            raise AssertionError("Invalid offset not rejected")


if __name__ == "__main__":
    test_paginated_large_archive()
    test_bad_offsets_fail_closed()
    print("S2-04 bounded archive pagination: PASS (12784 synthetic records)")
