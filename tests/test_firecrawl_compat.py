import asyncio

from rainbo_scrape.billing import _hash
from test_hosted import hosted


async def _auth(hosted):
    h, client, session, _, nonce, _ = hosted
    await h.fulfill(session["id"])
    key = h.key_for(session["id"], _hash(nonce))
    return h, client, {"Authorization": f"Bearer {key}"}


async def test_v2_scrape_uses_firecrawl_envelope_and_apex_metering(hosted):
    h, client, headers = await _auth(hosted)
    response = await client.post("/v2/scrape", headers=headers, json={
        "url": "https://example.com/compat", "formats": ["markdown", "links"],
        "actions": [{"type": "wait", "milliseconds": 10}],
    })
    payload = await response.json()
    assert response.status == 200
    assert payload["success"] is True
    assert payload["data"]["markdown"] == "# Fixture"
    assert payload["data"]["metadata"]["apexCreditsUsed"] == payload["creditsUsed"] == 1
    assert h.billing.remaining(h.billing.account_for(headers["Authorization"][7:])) == 109999
    unsupported = await client.post("/v2/batch/scrape", headers=headers,
                                    json={"urls": ["https://example.com"], "maxConcurrency": 1})
    assert unsupported.status == 400
    assert (await unsupported.json())["code"] == "unsupported_option"


async def test_v2_requires_auth_and_rejects_unimplemented_formats(hosted):
    _, client, *_ = hosted
    assert (await client.post("/v2/scrape", json={"url": "https://example.com"})).status == 401
    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/scrape", headers=headers,
                                json={"url": "https://example.com", "formats": ["rawHtml"]})
    assert response.status == 400
    payload = await response.json()
    assert payload["success"] is False and payload["code"] == "unsupported_option"


async def test_v2_map_search_and_batch_expose_exact_supported_subset(hosted, monkeypatch):
    import rainbo_scrape.hosted_features as hosted_features

    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/map", headers=headers,
                                json={"url": "https://example.com", "sitemap": "skip"})
    assert response.status == 400 and (await response.json())["success"] is False

    async def fixture_map(fetcher, url, **kwargs):
        return {"urls": ["https://example.com/a", {"url": "https://example.com/b", "title": "B"}]}

    monkeypatch.setattr(hosted_features, "site_map", fixture_map)
    response = await client.post("/v2/map", headers=headers,
                                 json={"url": "https://example.com", "limit": 10})
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    assert payload["links"] == [{"url": "https://example.com/a"}, {"url": "https://example.com/b", "title": "B"}]

    response = await client.post("/v2/search", headers=headers, json={"query": "example"})
    assert response.status == 503
    assert (await response.json())["code"] == "not_configured"

    response = await client.post("/v2/batch/scrape", headers=headers,
                                json={"urls": ["https://example.com"]})
    assert response.status == 200
    job = (await response.json())["id"]
    for _ in range(20):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["completed"] == status_payload["total"] == 1
    assert status_payload["data"][0]["markdown"] == "# Fixture"


async def test_v2_batch_reports_per_url_errors_and_supports_cancel(hosted):
    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/ok", "https://example.com/failure"],
        "formats": ["markdown"],
    })
    job = (await response.json())["id"]
    for _ in range(30):
        status = await client.get(f"/v2/batch/scrape/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status_payload["status"] == "completed"
    assert status_payload["completed"] == 2
    assert "links" not in status_payload["data"][0]
    response = await client.get(f"/v2/batch/scrape/{job}/errors", headers=headers)
    errors = await response.json()
    assert len(errors["errors"]) == 1
    assert errors["errors"][0]["url"] == "https://example.com/failure"

    response = await client.post("/v2/batch/scrape", headers=headers, json={
        "urls": ["https://example.com/slow"] * 5,
    })
    job = (await response.json())["id"]
    response = await client.delete(f"/v2/batch/scrape/{job}", headers=headers)
    assert response.status == 200 and (await response.json())["status"] == "cancelled"


async def test_v2_crawl_lifecycle_reuses_durable_apex_job(hosted):
    _, client, headers = await _auth(hosted)
    response = await client.post("/v2/crawl", headers=headers,
                                json={"url": "https://example.com/start", "limit": 1,
                                      "maxDiscoveryDepth": 0,
                                      "scrapeOptions": {"formats": [{"type": "markdown"}]}})
    payload = await response.json()
    assert response.status == 200 and payload["success"] is True
    job = payload["id"]
    for _ in range(20):
        status = await client.get(f"/v2/crawl/{job}", headers=headers)
        status_payload = await status.json()
        if status_payload["status"] != "scraping":
            break
        await asyncio.sleep(0.01)
    assert status.status == 200
    assert status_payload["status"] == "completed"
    assert status_payload["completed"] == status_payload["total"] == 1
    assert status_payload["data"][0]["markdown"] == "# Fixture"


async def test_v2_crawl_does_not_expand_access_scope_or_ignore_robots(hosted):
    _, client, headers = await _auth(hosted)
    for options in ({"ignoreRobotsTxt": True}, {"allowExternalLinks": True}, {"includePaths": ["/blog/.*"]}):
        response = await client.post("/v2/crawl", headers=headers,
                                    json={"url": "https://example.com", **options})
        payload = await response.json()
        assert response.status == 400 and payload["success"] is False
