"""Store watch: track a Shopify store's public catalog over time and rank what is moving.

Reads only the catalog file a Shopify store publishes for itself (/products.json), through the same
guarded fetcher every scrape uses: robots.txt is obeyed, requests are paced, nothing is disguised.
Each run saves a snapshot; consecutive snapshots are compared into events (new product, price change,
sold out, restocked, variant activity). The ranking is built from those events.

What this cannot know: a store's revenue or order count. "activity" means a variant record changed
between two snapshots, which happens on a sale but also on any edit. Every ranking says so.
"""
import json
import sqlite3
import time
from urllib.parse import urlsplit

from .policy import ScrapeError, check_url

PAGE_SIZE = 250
MAX_PAGES = 40
WEIGHTS = {"sold_out": 3, "restocked": 2, "new": 2, "activity": 1, "price_drop": 1, "price_rise": 1}
NOTE = ("Ranked by changes seen between snapshots of the store's public catalog. Activity is a signal, "
        "not a sales count: a variant record changes on a sale and on any edit. Revenue is not known.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores(origin TEXT PRIMARY KEY, added REAL NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, origin TEXT NOT NULL, ts REAL NOT NULL, products INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS items(snapshot INTEGER NOT NULL, product TEXT NOT NULL, handle TEXT, title TEXT, vendor TEXT,
    kind TEXT, created TEXT, price_min REAL, price_max REAL, compare_at REAL, in_stock INTEGER, variants TEXT,
    PRIMARY KEY(snapshot, product));
CREATE TABLE IF NOT EXISTS events(origin TEXT NOT NULL, ts REAL NOT NULL, product TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS events_by_store ON events(origin, ts);
"""


def store_origin(value):
    """"shop.example.com", "example.com/collections/all" or a full URL -> "https://shop.example.com"."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Name a store, for example example.com")
    text = value.strip()
    if "://" not in text:
        text = "https://" + text
    host = urlsplit(check_url(text)).hostname
    if not host or "." not in host:
        raise ValueError("Name a store by its public address, for example example.com")
    return "https://" + host


def _money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def parse_products(text):
    """One page of a Shopify /products.json file -> a list of plain product records."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("This address did not return a Shopify catalog file") from exc
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        raise ValueError("This address did not return a Shopify catalog file")
    out = []
    for raw in data["products"]:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        variants = {}
        for v in raw.get("variants") or []:
            if isinstance(v, dict) and v.get("id") is not None:
                variants[str(v["id"])] = [v.get("updated_at"), _money(v.get("price")), bool(v.get("available")),
                                          _money(v.get("compare_at_price"))]
        prices = [v[1] for v in variants.values() if v[1] is not None]
        compare = [v[3] for v in variants.values() if v[3]]
        out.append({"product": str(raw["id"]), "handle": raw.get("handle") or "", "title": (raw.get("title") or "").strip(),
                    "vendor": raw.get("vendor") or "", "kind": raw.get("product_type") or "",
                    "created": raw.get("created_at") or raw.get("published_at") or "",
                    "price_min": min(prices) if prices else None, "price_max": max(prices) if prices else None,
                    "compare_at": max(compare) if compare else None,
                    "in_stock": sum(1 for v in variants.values() if v[2]), "variants": variants})
    return out


def compare(before, after):
    """Events between two snapshots of one store. Both are {product id: record}."""
    events = []
    for pid, now in after.items():
        was = before.get(pid)
        if was is None:
            events.append((pid, "new", now["title"]))
            continue
        if was["price_min"] is not None and now["price_min"] is not None and was["price_min"] != now["price_min"]:
            kind = "price_drop" if now["price_min"] < was["price_min"] else "price_rise"
            events.append((pid, kind, "%.2f -> %.2f" % (was["price_min"], now["price_min"])))
        if was["in_stock"] and not now["in_stock"]:
            events.append((pid, "sold_out", "all variants unavailable"))
        elif not was["in_stock"] and now["in_stock"]:
            events.append((pid, "restocked", "%d variants available" % now["in_stock"]))
        touched = sum(1 for vid, v in now["variants"].items()
                      if vid in was["variants"] and was["variants"][vid][0] != v[0] and was["variants"][vid][1] == v[1])
        if touched:
            events.append((pid, "activity", "%d of %d variants changed" % (touched, len(now["variants"]))))
    for pid, was in before.items():
        if pid not in after:
            events.append((pid, "removed", was["title"]))
    return events


class Watch:
    """Snapshots and events in one SQLite file."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def stores(self):
        rows = self.db.execute(
            "SELECT s.origin, COUNT(n.id) AS snapshots, MAX(n.ts) AS last, "
            "(SELECT products FROM snapshots WHERE origin=s.origin ORDER BY id DESC LIMIT 1) AS products "
            "FROM stores s LEFT JOIN snapshots n ON n.origin=s.origin GROUP BY s.origin ORDER BY s.origin").fetchall()
        return [dict(r) for r in rows]

    def _load(self, snapshot):
        out = {}
        for r in self.db.execute("SELECT * FROM items WHERE snapshot=?", (snapshot,)):
            record = dict(r)
            record["variants"] = json.loads(record["variants"])
            out[record["product"]] = record
        return out

    def latest(self, origin):
        row = self.db.execute("SELECT id, ts FROM snapshots WHERE origin=? ORDER BY id DESC LIMIT 1", (origin,)).fetchone()
        return (row["id"], row["ts"], self._load(row["id"])) if row else (None, None, {})

    def record(self, origin, products, now=None):
        """Save one snapshot and the events since the previous one. The first snapshot of a store has no events."""
        now = time.time() if now is None else now
        previous, previous_ts, before = self.latest(origin)
        after = {p["product"]: p for p in products}
        self.db.execute("BEGIN")
        try:
            self.db.execute("INSERT OR IGNORE INTO stores(origin, added) VALUES(?,?)", (origin, now))
            snapshot = self.db.execute("INSERT INTO snapshots(origin, ts, products) VALUES(?,?,?)",
                                       (origin, now, len(after))).lastrowid
            self.db.executemany(
                "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [(snapshot, p["product"], p["handle"], p["title"], p["vendor"], p["kind"], p["created"], p["price_min"],
                  p["price_max"], p["compare_at"], p["in_stock"], json.dumps(p["variants"])) for p in after.values()])
            events = compare(before, after) if previous else []
            self.db.executemany("INSERT INTO events VALUES(?,?,?,?,?)", [(origin, now, pid, kind, detail) for pid, kind, detail in events])
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        counts = {}
        for _, kind, _ in events:
            counts[kind] = counts.get(kind, 0) + 1
        return {"store": origin, "snapshot": snapshot, "products": len(after), "first_snapshot": previous is None,
                "since": previous_ts, "changes": counts,
                "events": [{"product": pid, "title": after.get(pid, before.get(pid, {})).get("title", ""), "kind": kind, "detail": detail}
                           for pid, kind, detail in events]}

    def rising(self, origin, days=7, limit=20, now=None):
        """Products ranked by the changes seen in the window. See NOTE for what the ranking does and does not mean."""
        if isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 < days <= 365:
            raise ValueError("days must be a number from 1 to 365")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be a whole number from 1 to 200")
        now = time.time() if now is None else now
        snapshot, last_ts, current = self.latest(origin)
        if snapshot is None:
            return {"store": origin, "snapshots": 0, "products": [], "note": "No snapshot yet. Track this store first."}
        window = self.db.execute("SELECT COUNT(*) AS n, MIN(ts) AS first FROM snapshots WHERE origin=? AND ts>=?",
                                 (origin, now - days * 86400)).fetchone()
        tally = {}
        for r in self.db.execute("SELECT product, kind FROM events WHERE origin=? AND ts>=?", (origin, now - days * 86400)):
            tally.setdefault(r["product"], {}).setdefault(r["kind"], 0)
            tally[r["product"]][r["kind"]] += 1
        ranked = []
        for pid, kinds in tally.items():
            item = current.get(pid)
            if item is None:      # removed from the catalog: not a product to copy
                continue
            score = sum(WEIGHTS.get(kind, 0) * count for kind, count in kinds.items())
            if score:
                ranked.append({"product": pid, "title": item["title"], "url": "%s/products/%s" % (origin, item["handle"]),
                               "price": item["price_min"], "compare_at": item["compare_at"], "in_stock": bool(item["in_stock"]),
                               "created": item["created"], "score": score, "signals": kinds})
        ranked.sort(key=lambda p: (-p["score"], p["title"]))
        note = NOTE if window["n"] >= 2 else "Only one snapshot in this window: track the store again later to see changes. " + NOTE
        return {"store": origin, "window_days": days, "snapshots": window["n"], "catalog_size": len(current),
                "last_snapshot": last_ts, "products": ranked[:limit], "note": note}


async def collect(origin, get, max_pages=MAX_PAGES):
    """Every product in a store's public catalog. `get(url)` returns {"status", "body"} like Fetcher.get."""
    products, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            response = await get("%s/products.json?limit=%d&page=%d" % (origin, PAGE_SIZE, page))
        except ScrapeError as exc:
            if exc.code == "robots_denied":
                raise ValueError("This store's robots.txt does not allow its catalog file to be read") from exc
            raise
        if response["status"] in (401, 403, 404):
            raise ValueError("This store does not publish a catalog file (HTTP %s). It may not be a Shopify store, "
                             "or its owner has closed it." % response["status"])
        if response["status"] != 200:
            raise OSError("store returned HTTP %s" % response["status"])
        batch = parse_products(response["body"].decode("utf-8", errors="replace"))
        fresh = [p for p in batch if p["product"] not in seen]
        seen.update(p["product"] for p in fresh)
        products.extend(fresh)
        if len(batch) < PAGE_SIZE or not fresh:
            break
    return products


async def track(value, path=None):
    """Read one store's catalog now, save the snapshot, and return what changed since the last one."""
    from .network import Fetcher
    from .settings import Settings
    from .store import Store
    origin = store_origin(value)
    settings = Settings().prepare()
    async with Fetcher(settings, Store(settings)) as fetcher:
        products = await collect(origin, lambda url: fetcher.get(url, max_age=0))
    watch = Watch(path or settings.data_dir / "storewatch.db")
    try:
        return watch.record(origin, products)
    finally:
        watch.close()


def open_watch(path=None):
    from .settings import Settings
    return Watch(path or Settings().prepare().data_dir / "storewatch.db")
