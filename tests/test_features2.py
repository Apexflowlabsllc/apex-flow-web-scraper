import asyncio
import pytest
from rainbo_scrape.chunking import chunk_markdown
from rainbo_scrape import engine
from rainbo_scrape.settings import Settings

MD = "# Guide\n\nIntro text.\n\n## Install\n\nRun it.\n\n```sh\npip install x\n\n# not a heading\n```\n\n## Use\n\nCall it."


def test_chunks_keep_heading_path_and_never_split_code():
    chunks = chunk_markdown(MD, 200)
    assert any(c["heading_path"] == ["Guide", "Install"] and "# not a heading" in c["text"] and "pip install x" in c["text"] for c in chunks)
    assert all(c["chars"] <= 400 for c in chunks)
    use = [c for c in chunks if c["heading_path"] == ["Guide", "Use"]]
    assert use and "Call it." in use[0]["text"]


def test_chunk_size_is_validated():
    for bad in (10, 9000, True, "x"):
        with pytest.raises(ValueError):
            chunk_markdown("# a", bad)


class Fake:
    settings = Settings()

    def __init__(self, table):
        self.table, self.calls = table, []

    async def get(self, url, **kw):
        self.calls.append(url)
        for key, value in self.table.items():
            if url.startswith(key):
                return {"url": url, "headers": {"content-type": value[1]}, "cache": "miss", **value[0]}
        raise AssertionError("unexpected fetch " + url)


def html(text):
    return {"status": 200, "body": f"<html><body><main><h1>{text}</h1><p>{'word ' * 60}</p></main></body></html>".encode()}


def run(fetcher, **options):
    return asyncio.run(engine.scrape(fetcher, "https://blocked.example/p", {"render": "never", **options}))


def test_receipt_is_always_present():
    r = run(Fake({"https://blocked.example/p": (html("Live"), "text/html")}))
    assert r["receipt"]["status"] == 200 and len(r["receipt"]["content_sha256"]) == 64


def test_403_fails_and_nothing_else_is_fetched():
    f = Fake({"https://blocked.example/p": ({"status": 403, "body": b"no"}, "text/html")})
    with pytest.raises(Exception, match="HTTP 403"):
        run(f)
    assert f.calls == ["https://blocked.example/p"]


def test_chunks_option_adds_chunks_to_result():
    r = run(Fake({"https://blocked.example/p": (html("Live"), "text/html")}), chunks=300)
    assert r["chunks"] and r["chunks"][0]["heading_path"] == ["Live"]


def test_validate_request_checks_new_options():
    s = Settings()
    assert engine.validate_request({"urls": ["https://example.com"], "chunks": 500}, s)["chunks"] == 500
    for bad in ({"chunks": 5}, {"chunks": True}):
        with pytest.raises(ValueError):
            engine.validate_request({"urls": ["https://example.com"], **bad}, s)
