import pytest

from benchmark.goldset_evaluator import GoldSetError
from benchmark.compare_engines import _endpoint, _read_result, _request_body, main, schema_for


def case(fields):
    return {"id": "case-1", "url": "https://owned.example/page", "expected_fields": {
        path: {"value": value} for path, value in fields.items()
    }}


def test_request_schema_is_typed_nested_and_cache_bypassing():
    item = case({"product.title": "Desk", "product.price": 12.5, "available": True,
                 "tags": ["wood", "office"]})
    request = _request_body(item)
    assert request["url"] == item["url"]
    assert request["maxAge"] == 0
    schema = request["formats"][0]["schema"]
    assert schema["required"] == ["available", "product", "tags"]
    assert schema["properties"]["product"]["required"] == ["price", "title"]
    assert schema["properties"]["product"]["properties"]["title"]["anyOf"][0]["type"] == "string"
    assert schema["properties"]["product"]["properties"]["price"]["anyOf"][0]["type"] == "number"
    assert schema["properties"]["available"]["anyOf"][0]["type"] == "boolean"
    assert schema["properties"]["tags"]["anyOf"][0]["items"]["type"] == "string"
    assert schema["properties"]["available"]["anyOf"][1] == {"type": "null"}


def test_schema_rejects_conflicting_field_paths():
    with pytest.raises(GoldSetError, match="conflicting"):
        schema_for(case({"item": "whole value", "item.name": "nested value"}))


def test_endpoint_requires_https_except_loopback_and_never_embeds_credentials():
    assert _endpoint("apex", "https://webscraper.apexflowlabs.com") == "https://webscraper.apexflowlabs.com/v2/scrape"
    assert _endpoint("apex", "https://webscraper.apexflowlabs.com/v2") == "https://webscraper.apexflowlabs.com/v2/scrape"
    assert _endpoint("firecrawl", "https://api.firecrawl.dev/v2") == "https://api.firecrawl.dev/v2/scrape"
    assert _endpoint("apex", "http://localhost:8080") == "http://localhost:8080/v2/scrape"
    with pytest.raises(GoldSetError, match="custom API hosts"):
        _endpoint("apex", "https://untrusted.example")
    assert _endpoint("apex", "https://operator.example", allow_custom_host=True) == "https://operator.example/v2/scrape"
    with pytest.raises(GoldSetError, match="HTTPS"):
        _endpoint("apex", "http://scraper.example")
    with pytest.raises(GoldSetError, match="standard HTTPS port"):
        _endpoint("firecrawl", "https://api.firecrawl.dev:8443")
    with pytest.raises(GoldSetError, match="credentials"):
        _endpoint("apex", "https://name:secret@scraper.example", allow_custom_host=True)


def test_api_response_normalization_keeps_credits_and_does_not_infer_usd():
    result = _read_result(200, {"success": True, "creditsUsed": 151,
                                "data": {"json": {"title": "Owned page"}}})
    assert result == {"status": "success", "fields": {"title": "Owned page"},
                      "cost": {"credits": 151}}
    assert "usd" not in result["cost"]
    assert _read_result(200, {"success": True, "data": {"markdown": "no JSON"}})["status"] == "error"
    assert _read_result(429, {"error": "rate limited"})["status"] == "error"
    assert _read_result(200, {"success": False, "code": "access_challenge"})["status"] == "blocked"


def test_cli_refuses_to_send_billable_requests_without_explicit_flag(tmp_path, capsys):
    output = tmp_path / "run.json"
    with pytest.raises(SystemExit) as exc:
        main(["apex", str(tmp_path / "does-not-need-to-exist.json"), "--output", str(output)])
    assert exc.value.code == 2
    assert "no requests sent" in capsys.readouterr().err
    assert not output.exists()
