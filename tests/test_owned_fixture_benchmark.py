import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark.goldset_evaluator import evaluate, sign_manifest, verify_manifest
from rainbo_scrape.extract import extract


FIXTURES = Path(__file__).parent / "benchmark" / "fixtures"
TEST_KEY = b"synthetic-fixture-test-key-never-used-for-real-signatures"
NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def test_owned_multilayout_fixtures_reach_full_field_coverage_and_accuracy():
    fixture_index = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
    cases = fixture_index["cases"]
    manifest = sign_manifest({
        "schema_version": 1,
        "dataset_id": fixture_index["dataset_id"],
        "version": fixture_index["version"],
        "created_at": "2026-11-01T00:00:00Z",
        "expires_at": "2027-11-01T00:00:00Z",
        "permission": {
            "explicit_owner_consent": True,
            "owner": "Apex Flow Web Scraper project authors",
            "evidence_ref": "tests/benchmark/fixtures authored for local QA",
            "allowed_domains": [fixture_index["allowed_domain"]],
        },
        "cases": [{key: value for key, value in case.items() if key not in ("fixture", "recipe")}
                  for case in cases],
    }, TEST_KEY)
    signed = verify_manifest(manifest, TEST_KEY, now=NOW)

    pages = []
    for case in cases:
        html = (FIXTURES / case["fixture"]).read_bytes()
        text = html.decode("utf-8")
        for rule in case["expected_fields"].values():
            assert rule["evidence"]["quote"] in text
            expected = rule["value"] if isinstance(rule["value"], list) else [rule["value"]]
            assert all(str(value) in text for value in expected)
        result = extract(html, case["url"], recipe=case["recipe"])
        assert result["validation_errors"] == []
        pages.append({"id": case["id"], "url": case["url"], "status": "success", "fields": result["data"]})

    scored = evaluate(signed, {"dataset_id": fixture_index["dataset_id"], "engine": "apex-local-extract",
                               "engine_version": "working-tree", "pages": pages})
    assert scored["page_reach"]["rate"] == 1
    assert scored["field_coverage"]["rate"] == 1
    assert scored["field_accuracy"]["rate"] == 1
    assert scored["page_reach"]["total"] == 6
    assert scored["field_accuracy"]["total"] == 23
    assert set(scored["by_category"]) == {"product", "article", "job", "event", "directory"}
