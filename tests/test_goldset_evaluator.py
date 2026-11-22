from datetime import datetime, timezone

import pytest

from benchmark.goldset_evaluator import (
    GoldSetError,
    evaluate,
    sign_manifest,
    verify_manifest,
    wilson,
)


KEY = b"local-test-signing-key-that-is-32-bytes-minimum"
NOW = datetime(2026, 11, 22, tzinfo=timezone.utc)


def manifest():
    value = {
        "schema_version": 1,
        "dataset_id": "synthetic-fixture-v1",
        "version": "1.0.0",
        "created_at": "2026-11-01T00:00:00Z",
        "expires_at": "2027-11-01T00:00:00Z",
        "permission": {
            "explicit_owner_consent": True,
            "owner": "local test fixture operator",
            "evidence_ref": "test://owned-fixture/permission",
            "allowed_domains": ["localhost"],
        },
        "cases": [
            {"id": "one", "category": "fixture", "url": "http://localhost:8080/one",
             "expected_fields": {
                 "title": {"value": "Good page", "normalize": "casefold_whitespace", "evidence": {"quote": "Good page"}},
                 "price": {"value": "$10", "normalize": "exact", "evidence": {"quote": "$10"}},
             }},
            {"id": "two", "category": "fixture", "url": "http://localhost:8080/two",
             "expected_fields": {
                 "title": {"value": "Second page", "normalize": "exact", "evidence": {"quote": "Second page"}},
             }},
        ],
    }
    return sign_manifest(value, KEY)


def test_signed_permissioned_gold_set_scores_metrics_and_separates_failures():
    signed = verify_manifest(manifest(), KEY, now=NOW)
    result = evaluate(signed, {
        "dataset_id": "synthetic-fixture-v1", "label": "run-a", "engine": "apex",
        "engine_version": "test", "pages": [
            {"id": "one", "url": "http://localhost:8080/one", "status": "success",
             "fields": {"title": " GOOD   PAGE "}, "latency_ms": 120, "cost": {"usd": 0.01, "credits": 1}},
            {"id": "two", "url": "http://localhost:8080/two", "status": "blocked", "error_code": "robots_denied"},
        ]})

    assert result["page_reach"]["rate"] == 0.5
    assert result["field_coverage"]["rate"] == pytest.approx(0.3333)
    assert result["field_accuracy"]["rate"] == 1
    assert result["by_field"]["fixture.title"]["coverage"]["total"] == 2
    assert result["by_field"]["fixture.price"]["coverage"]["rate"] == 0
    assert result["failure_causes"] == {"robots_denied": 1}
    assert result["cost"]["usd_per_correct_field"] == 0.01


def test_signature_and_scope_tampering_are_rejected():
    signed = manifest()
    signed["cases"][0]["url"] = "http://other.example/one"
    with pytest.raises(GoldSetError, match="signature"):
        verify_manifest(signed, KEY, now=NOW)

    unsigned = manifest()
    unsigned["signature"]["value"] = "0" * 64
    with pytest.raises(GoldSetError, match="signature"):
        verify_manifest(unsigned, KEY, now=NOW)


def test_invalid_permission_domain_and_expiry_are_rejected():
    value = manifest()
    value["cases"][0]["url"] = "https://example.org/page"
    value = sign_manifest({k: v for k, v in value.items() if k != "signature"}, KEY)
    with pytest.raises(GoldSetError, match="outside the signed permission scope"):
        verify_manifest(value, KEY, now=NOW)

    value = manifest()
    value["expires_at"] = "2026-11-21T00:00:00Z"
    value = sign_manifest({k: v for k, v in value.items() if k != "signature"}, KEY)
    with pytest.raises(GoldSetError, match="not currently valid"):
        verify_manifest(value, KEY, now=NOW)


def test_run_rejects_unknown_duplicate_and_mismatched_observations():
    signed = verify_manifest(manifest(), KEY, now=NOW)
    record = {"id": "one", "url": "http://localhost:8080/one", "status": "success", "fields": {}}
    base = {"dataset_id": "synthetic-fixture-v1", "pages": [record]}
    with pytest.raises(GoldSetError, match="does not match"):
        evaluate(signed, {**base, "pages": [{**record, "url": "http://localhost:8080/wrong"}]})
    with pytest.raises(GoldSetError, match="duplicate"):
        evaluate(signed, {**base, "pages": [record, record]})


def test_wilson_interval_handles_empty_and_extreme_samples():
    assert wilson(0, 0) is None
    assert wilson(10, 10)[0] < 1 and wilson(10, 10)[1] == 1
    assert wilson(0, 10)[0] == 0 and wilson(0, 10)[1] > 0
