from rainbo_scrape.billing import _hash
from rainbo_scrape.hosted_firecrawl_compat import INTERACT_SERVICE_KEY
from rainbo_scrape.interact import origin_of, validate_origins
from rainbo_scrape.policy import ScrapeError
from test_hosted import hosted


class _Fetcher:
    async def __aexit__(self, *_):
        pass


class _Locator:
    async def inner_text(self, **_):
        return "Session fixture content"


class _Page:
    url = "about:blank"

    async def goto(self, url, **_):
        self.url = url

    async def title(self):
        return "Session fixture"

    def locator(self, _):
        return _Locator()


class _Context:
    async def close(self):
        pass


async def test_interact_session_lifecycle_is_bounded_and_account_scoped(hosted):
    h, client, session, _, nonce, _ = hosted
    await h.fulfill(session["id"])
    key = h.key_for(session["id"], _hash(nonce))
    headers = {"Authorization": f"Bearer {key}"}
    service = client.server.app[INTERACT_SERVICE_KEY]

    async def opener(origins, usage):
        return _Fetcher(), _Context(), _Page()

    async def run_actions(page, actions):
        return []

    service.opener = opener
    service.action_runner = run_actions
    created = await client.post("/v2/interact", headers=headers, json={
        "ttl": 120, "activityTtl": 60, "allowedOrigins": ["https://example.com"],
        "url": "https://example.com/start",
    })
    payload = await created.json()
    assert created.status == 200 and payload["success"] is True
    assert payload["creditCost"] == 3 and payload["expiresAt"].endswith("Z")
    ident = payload["id"]
    listed = await (await client.get("/v2/interact?status=active", headers=headers)).json()
    assert listed["sessions"][0]["id"] == ident

    denied = await client.post(f"/v2/interact/{ident}/execute", headers=headers,
                               json={"url": "https://example.org/other"})
    assert denied.status == 400
    result = await client.post(f"/v2/interact/{ident}/execute", headers=headers,
                               json={"url": "https://example.com/next", "actions": [{"type": "scroll", "amount": 1}]})
    result_payload = await result.json()
    assert result.status == 200 and result_payload["url"] == "https://example.com/next"
    assert result_payload["title"] == "Session fixture" and result_payload["text"] == "Session fixture content"

    deleted = await client.delete(f"/v2/interact/{ident}", headers=headers)
    assert deleted.status == 200 and (await deleted.json())["status"] == "destroyed"
    history = await (await client.get("/v2/interact?status=destroyed", headers=headers)).json()
    assert history["sessions"][0]["id"] == ident
    absent = await client.post(f"/v2/interact/{ident}/execute", headers=headers, json={})
    assert absent.status == 404


def test_interact_origin_policy_and_input_bounds():
    assert origin_of("https://Example.com/a") == "https://example.com"
    assert validate_origins(["https://example.com", "https://example.com/"]) == {"https://example.com"}
    for value in ([], ["http://127.0.0.1"], ["https://example.com/path"], ["https://user@example.com"]):
        try:
            validate_origins(value)
        except (ValueError, ScrapeError):
            pass
        else:
            raise AssertionError(f"unsafe origin accepted: {value!r}")
