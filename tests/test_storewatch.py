"""Store watch: snapshots of a Shopify store's public catalog, events between them, and the ranking."""
import json
import pytest
from rainbo_scrape import storewatch
from rainbo_scrape.policy import ScrapeError
from rainbo_scrape.storewatch import Watch, collect, compare, parse_products, store_origin

DAY = 86400


def product(pid, title, price="20.00", available=True, stamp="2026-11-01T00:00:00Z", created="2026-01-01T00:00:00Z", variants=2):
    return {"id": pid, "handle": title.lower().replace(" ", "-"), "title": title, "vendor": "Acme", "product_type": "Toy",
            "created_at": created,
            "variants": [{"id": pid * 10 + n, "price": price, "available": available, "updated_at": stamp,
                          "compare_at_price": None} for n in range(variants)]}


def page(*products):
    return json.dumps({"products": list(products)})


def by_id(text):
    return {p["product"]: p for p in parse_products(text)}


def test_store_origin_accepts_a_bare_name_or_a_page_and_refuses_private_addresses():
    assert store_origin("Example.com") == "https://example.com"
    assert store_origin("https://shop.example.com/collections/all?x=1") == "https://shop.example.com"
    for bad in ("", "   ", None, 7, "localhost"):
        with pytest.raises((ValueError, ScrapeError)):
            store_origin(bad)
    with pytest.raises(ScrapeError):
        store_origin("http://127.0.0.1/")


def test_parse_products_reads_prices_stock_and_variants_and_rejects_other_files():
    found = parse_products(page(product(1, "Rope Toy", price="12.50"),
                                {"id": 2, "title": " Bare ", "variants": [{"id": 20, "price": "9", "available": False,
                                                                           "compare_at_price": "15.00", "updated_at": "t"}]},
                                {"title": "no id"}, "junk"))
    assert [p["product"] for p in found] == ["1", "2"]
    assert found[0]["price_min"] == 12.5 and found[0]["in_stock"] == 2 and found[0]["handle"] == "rope-toy"
    assert found[1]["title"] == "Bare" and found[1]["in_stock"] == 0 and found[1]["compare_at"] == 15.0
    for bad in ("<html>", "[]", '{"products": 3}', None):
        with pytest.raises(ValueError):
            parse_products(bad)


def test_compare_reports_new_removed_price_stock_and_activity():
    before = by_id(page(product(1, "Rope"), product(2, "Ball"), product(3, "Bed"), product(4, "Bowl", available=False),
                        product(5, "Leash")))
    after = by_id(page(product(1, "Rope", stamp="2026-11-02T00:00:00Z"),            # variants touched, same price
                       product(2, "Ball", price="15.00", stamp="2026-11-02T00:00:00Z"),  # price cut only
                       product(3, "Bed", available=False),                         # sold out
                       product(4, "Bowl"),                                         # back in stock
                       product(6, "Collar")))                                      # new; 5 is gone
    events = {(pid, kind): detail for pid, kind, detail in compare(before, after)}
    assert events[("1", "activity")] == "2 of 2 variants changed"
    assert events[("2", "price_drop")] == "20.00 -> 15.00" and ("2", "activity") not in events
    assert ("3", "sold_out") in events and ("4", "restocked") in events
    assert events[("6", "new")] == "Collar" and events[("5", "removed")] == "Leash"
    assert compare(before, before) == []


def test_watch_records_snapshots_and_ranks_what_moved(tmp_path):
    watch = Watch(tmp_path / "w.db")
    store, t0 = "https://shop.example.com", 1_000_000.0
    first = watch.record(store, parse_products(page(product(1, "Rope"), product(2, "Ball"), product(3, "Bed"))), now=t0)
    assert first["first_snapshot"] and first["events"] == [] and first["products"] == 3
    second = watch.record(store, parse_products(page(product(1, "Rope", stamp="s2"), product(2, "Ball"),
                                                     product(3, "Bed", available=False), product(4, "Collar"))), now=t0 + DAY)
    assert not second["first_snapshot"] and second["changes"] == {"activity": 1, "sold_out": 1, "new": 1}
    watch.record(store, parse_products(page(product(1, "Rope", stamp="s3"), product(2, "Ball"), product(4, "Collar"))), now=t0 + 2 * DAY)
    rising = watch.rising(store, days=7, now=t0 + 2 * DAY)
    assert rising["snapshots"] == 3 and rising["catalog_size"] == 3
    # Rope changed twice (2), Collar is new (2): tie broken by title. Bed sold out but was then removed, so it is not listed.
    assert [(p["title"], p["score"]) for p in rising["products"]] == [("Collar", 2), ("Rope", 2)]
    assert rising["products"][1]["signals"] == {"activity": 2} and rising["products"][1]["url"] == store + "/products/rope"
    assert "not a sales count" in rising["note"] and "Revenue is not known" in rising["note"]
    # A window that holds no events returns nothing rather than old news.
    assert watch.rising(store, days=1, now=t0 + 30 * DAY)["products"] == []
    assert watch.rising("https://other.example.com")["snapshots"] == 0
    assert watch.stores() == [{"origin": store, "snapshots": 3, "last": t0 + 2 * DAY, "products": 3}]
    for bad in ({"days": 0}, {"days": True}, {"limit": 0}, {"limit": 201}):
        with pytest.raises(ValueError):
            watch.rising(store, **bad)
    watch.close()


async def test_collect_pages_until_a_short_page_and_explains_a_closed_store(monkeypatch):
    monkeypatch.setattr(storewatch, "PAGE_SIZE", 2)
    pages = {1: page(product(1, "A"), product(2, "B")), 2: page(product(2, "B"), product(3, "C")), 3: page()}
    asked = []

    async def get(url):
        asked.append(url)
        return {"status": 200, "body": pages[int(url.rsplit("=", 1)[1])].encode()}
    found = await collect("https://shop.example.com", get)
    assert [p["title"] for p in found] == ["A", "B", "C"]          # the repeated product is kept once
    assert asked[0] == "https://shop.example.com/products.json?limit=2&page=1" and len(asked) == 3

    async def closed(url):
        return {"status": 404, "body": b""}
    with pytest.raises(ValueError, match="does not publish a catalog"):
        await collect("https://shop.example.com", closed)

    async def denied(url):
        raise ScrapeError("robots_denied", "Site robots.txt disallows this URL")
    with pytest.raises(ValueError, match="robots.txt"):
        await collect("https://shop.example.com", denied)

    async def broken(url):
        return {"status": 500, "body": b""}
    with pytest.raises(OSError):
        await collect("https://shop.example.com", broken)
