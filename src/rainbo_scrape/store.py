"""Transactional queue, immutable page receipts and bounded response cache."""
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import time
import uuid


class Store:
    def __init__(self, settings):
        self.settings = settings.prepare()
        self.path = settings.data_dir / "atlas.sqlite3"
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, state TEXT, created REAL, updated REAL,
              request TEXT, error TEXT, cancel INTEGER DEFAULT 0,
              bytes INTEGER DEFAULT 0, deadline REAL);
            CREATE TABLE IF NOT EXISTS pages (
              job TEXT, url TEXT, depth INTEGER, state TEXT DEFAULT 'queued',
              tries INTEGER DEFAULT 0, result TEXT, error TEXT, updated REAL,
              PRIMARY KEY(job,url));
            CREATE INDEX IF NOT EXISTS frontier ON pages(job,state,depth);
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, job TEXT, ts REAL, message TEXT);
            CREATE INDEX IF NOT EXISTS events_job ON events(job,id);
            CREATE TABLE IF NOT EXISTS cache (
              key TEXT PRIMARY KEY, ts REAL, headers TEXT, body BLOB, url TEXT, status INTEGER);
            CREATE TABLE IF NOT EXISTS recipes (name TEXT PRIMARY KEY, updated REAL, recipe TEXT);
            """)
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, request):
        ident, now = uuid.uuid4().hex[:16], time.time()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            if active >= 100:
                raise ValueError("The queue is full (100 active jobs)")
            db.execute("INSERT INTO jobs(id,state,created,updated,request) VALUES(?,?,?,?,?)",
                       (ident, "queued", now, now, json.dumps(request)))
            for url in request["urls"]:
                db.execute("INSERT OR IGNORE INTO pages(job,url,depth,updated) VALUES(?,?,0,?)", (ident, url, now))
            self._event(db, ident, "Queued and saved to disk")
        return self.status(ident)

    def _event(self, db, ident, message):
        db.execute("INSERT INTO events(job,ts,message) VALUES(?,?,?)", (ident, time.time(), message))

    def event(self, ident, message):
        with self.db() as db:
            self._event(db, ident, message)
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), ident))

    def recover(self):
        with self.db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE state='running'").fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET state='queued',updated=? WHERE id=?", (time.time(), row[0]))
                db.execute("UPDATE pages SET state='queued' WHERE job=? AND state='running'", (row[0],))
                self._event(db, row[0], "Worker restarted; unfinished pages returned to queue")

    def claim_job(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            request = json.loads(row["request"])
            db.execute("UPDATE jobs SET state='running', updated=?,deadline=coalesce(deadline,?) WHERE id=?",
                       (time.time(), time.time()+request["max_seconds"], row["id"]))
            self._event(db, row["id"], "Worker started")
            return row["id"], request

    def claim_pages(self, ident, limit):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT url,depth FROM pages WHERE job=? AND state='queued' ORDER BY depth,url LIMIT ?",
                              (ident, limit)).fetchall()
            for row in rows:
                db.execute("UPDATE pages SET state='running',tries=tries+1,updated=? WHERE job=? AND url=?",
                           (time.time(), ident, row["url"]))
            return [dict(r) for r in rows]

    def enqueue(self, ident, links, depth, max_pages):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            room = max_pages - db.execute("SELECT count(*) FROM pages WHERE job=?", (ident,)).fetchone()[0]
            count = 0
            for link in links:
                if count >= room:
                    break
                count += db.execute("INSERT OR IGNORE INTO pages(job,url,depth,updated) VALUES(?,?,?,?)",
                                    (ident, link, depth, time.time())).rowcount
            return count

    def save_page(self, ident, url, result=None, error=None):
        with self.db() as db:
            db.execute("UPDATE pages SET state=?,result=?,error=?,updated=? WHERE job=? AND url=?",
                       ("failed" if error else "done", json.dumps(result) if result else None,
                        json.dumps(error) if error else None, time.time(), ident, url))
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), ident))
            self._event(db, ident, ("Failed: " if error else "Extracted: ") + url[:500])

    def charge(self, ident, size, maximum):
        from .policy import ScrapeError
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT bytes,cancel FROM jobs WHERE id=?", (ident,)).fetchone()
            if row[1]:
                raise ScrapeError("cancelled", "Job cancelled")
            if row[0] + size > maximum:
                raise ScrapeError("byte_budget", "Transfer budget exhausted")
            db.execute("UPDATE jobs SET bytes=bytes+? WHERE id=?", (size, ident))

    def finish(self, ident, state, error=None):
        with self.db() as db:
            db.execute("UPDATE jobs SET state=?,error=?,updated=? WHERE id=?", (state, error, time.time(), ident))
            if state in {"cancelled", "limited", "failed"}:
                db.execute("UPDATE pages SET state='skipped',error=? WHERE job=? AND state IN ('running','queued')",
                           (json.dumps({"code": state, "message": error or state}), ident))
            self._event(db, ident, state + (": " + error if error else ""))

    def status(self, ident):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
            if not row:
                raise ValueError("Unknown job ID")
            counts = dict(db.execute("SELECT state,count(*) FROM pages WHERE job=? GROUP BY state", (ident,)))
            events = [dict(r) for r in db.execute("SELECT ts,message FROM events WHERE job=? ORDER BY id DESC LIMIT 8", (ident,))]
        result = dict(row)
        result["request"] = json.loads(result["request"])
        result.update(counts=counts, events=events, total=sum(counts.values()),
                      age_seconds=round(time.time()-result["created"], 2),
                      heartbeat_age_seconds=round(time.time()-result["updated"], 2))
        return result

    def list_jobs(self, limit=20):
        with self.db() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY created DESC LIMIT ?", (min(100, max(1, limit)),))]
        return [self.status(i) for i in ids]

    def cancel(self, ident):
        status = self.status(ident)
        if status["state"] in {"queued", "running"}:
            with self.db() as db:
                db.execute("UPDATE jobs SET cancel=1,updated=? WHERE id=?", (time.time(), ident))
            if status["state"] == "queued":
                self.finish(ident, "cancelled")
        return self.status(ident)

    def results(self, ident, offset=0, limit=10):
        self.status(ident)
        with self.db() as db:
            rows = db.execute("SELECT url,depth,state,result,error FROM pages WHERE job=? ORDER BY depth,url LIMIT ? OFFSET ?",
                              (ident, min(50, max(1, limit)), max(0, offset))).fetchall()
        return [{**dict(r), "result": json.loads(r["result"]) if r["result"] else None,
                 "error": json.loads(r["error"]) if r["error"] else None} for r in rows]

    def cache_get(self, key):
        with self.db() as db:
            row = db.execute("SELECT * FROM cache WHERE key=?", (key,)).fetchone()
        if row:
            return {**dict(row), "headers": json.loads(row["headers"])}

    def cache_put(self, key, response):
        if len(response["body"]) > self.settings.max_body:
            return
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?)", (key, time.time(), json.dumps(response["headers"]),
                       response["body"], response["url"], response["status"]))
            total = db.execute("SELECT coalesce(sum(length(body)),0) FROM cache").fetchone()[0]
            if total > self.settings.cache_bytes:
                for row in db.execute("SELECT key,length(body) AS n FROM cache ORDER BY ts").fetchall():
                    db.execute("DELETE FROM cache WHERE key=?", (row["key"],))
                    total -= row["n"]
                    if total <= self.settings.cache_bytes:
                        break

    def recipes(self):
        with self.db() as db:
            return [{"name": r[0], "updated": r[1], "recipe": json.loads(r[2])}
                    for r in db.execute("SELECT * FROM recipes ORDER BY name")]

    def put_recipe(self, name, recipe):
        from .extract import validate_recipe
        import re
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
            raise ValueError("Recipe name must be 1–64 letters, digits, underscores or hyphens")
        validate_recipe(recipe)
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO recipes VALUES(?,?,?)", (name, time.time(), json.dumps(recipe)))
        return {"name": name, "saved": True}

    def search(self, query, limit=10):
        # Literal search over owned results, not an external search-engine claim.
        if not isinstance(query, str) or not 1 <= len(query) <= 200:
            raise ValueError("Query must contain 1–200 characters")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self.db() as db:
            rows = db.execute("SELECT job,url,result FROM pages WHERE state='done' AND result LIKE ? ESCAPE '\\' ORDER BY updated DESC LIMIT ?",
                              (pattern, min(50, max(1, limit)))).fetchall()
        return [{"job": r[0], "url": r[1], "title": json.loads(r[2]).get("title"),
                 "excerpt": json.loads(r[2]).get("text", "")[:1200]} for r in rows]
