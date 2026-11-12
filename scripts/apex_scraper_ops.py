#!/usr/bin/env python3
"""Hands-off operations for the hosted scraper. Runs as root from systemd timers on the API host.

  health  every 5 minutes: check the service locally and through the public site, restart it if it is
          down, and raise a RAINBO alert when something needs a human.
  daily   once a day: back up the customer database, write the daily report RAINBO reads, and tell
          RAINBO about new paid orders.
  report  print the current report as JSON (read-only; safe to run any time).

No secrets are read or printed. Standard library only.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DB = "/var/lib/apex-webscraper/billing.db"
BACKUPS = Path("/opt/apex-webscraper/backups")
STATE = Path("/opt/apex-webscraper/ops/state.json")
REPORTS = Path("/home/rainbo/rainbo-reports/apex-scraper")
ALERTS = Path("/home/rainbo/rainbo-alerts")
SITE = "https://webscraper.apexflowlabs.com"
PRICES = {"pass": 5, "starter": 15, "growth": 79, "scale": 299}


def owned_by_rainbo(path):
    try:
        shutil.chown(path, "rainbo", "rainbo")
    except Exception:
        pass


def alert(ref, kind, message, severity="error"):
    """The file format RAINBO's Human Queue already watches."""
    ALERTS.mkdir(parents=True, exist_ok=True)
    owned_by_rainbo(ALERTS)
    path = ALERTS / ("err-apex-scraper-%s.json" % ref)
    path.write_text(json.dumps({"ts": int(time.time()), "ref": "apex-scraper-" + ref, "where": "apex-webscraper",
                                "kind": kind, "msg": message[:300], "severity": severity}), encoding="utf-8")
    owned_by_rainbo(path)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def get(url, timeout=15):
    request = urllib.request.Request(url, headers={"User-Agent": "apex-scraper-ops/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def healthy(url):
    try:
        return get(url).get("ok") is True
    except Exception:
        return False


def health():
    state = load_state()
    local = healthy("http://127.0.0.1:8850/api/status")
    if not local:
        subprocess.run(["systemctl", "restart", "apex-webscraper"], check=False)
        time.sleep(12)
        local = healthy("http://127.0.0.1:8850/api/status")
        if local:
            alert("restarted", "auto_recovered", "The scraper API was down and was restarted automatically. It is healthy again. No action needed.", "info")
        else:
            alert("down", "service_down", "The scraper API is down and an automatic restart did not fix it. Follow the apex-scraper-operator skill, section Rollback.")
    public = healthy(SITE + "/api/status")
    if local and not public:
        fails = state.get("public_fails", 0) + 1
        state["public_fails"] = fails
        if fails == 3:       # 15 minutes: not a blip
            alert("public", "site_unreachable", "The API is healthy on the server but %s/api/status fails. Check the Vercel deployment and the Caddy proxy." % SITE)
    else:
        state["public_fails"] = 0
    usage = shutil.disk_usage("/")
    if usage.used / usage.total > 0.93 and time.time() - state.get("disk_alert", 0) > 86400:
        state["disk_alert"] = time.time()
        alert("disk", "disk_nearly_full", "The server disk is %d%% full. Old scraper releases and backups in /opt/apex-webscraper can be removed." % (100 * usage.used / usage.total))
    save_state(state)
    print(json.dumps({"local": local, "public": public, "disk_percent": round(100 * usage.used / usage.total)}))
    return 0 if local else 1


def report():
    db = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    now = time.time()

    def funnel(days):
        rows = db.execute("SELECT event,plan,SUM(count) FROM funnel WHERE day>=date('now',?) GROUP BY 1,2", ("-%d day" % days,)).fetchall()
        out = {}
        for event, plan, count in rows:
            out[event + (":" + plan if plan else "")] = count
        return out

    real = "status NOT LIKE 'qa%' AND COALESCE(email,'') NOT LIKE '%internal%'"
    paid = db.execute("SELECT plan,COUNT(*) FROM orders WHERE plan NOT IN ('free') AND status='active' AND %s GROUP BY 1" % real).fetchall()
    trials = db.execute("SELECT COUNT(*) FROM orders WHERE plan='free' AND %s" % real).fetchone()[0]
    trials_week = db.execute("SELECT COUNT(*) FROM orders WHERE plan='free' AND created>? AND %s" % real, (now - 7 * 86400,)).fetchone()[0]
    credits = {label: db.execute("SELECT COALESCE(SUM(credits),0),COALESCE(SUM(kind!='refund'),0) FROM usage WHERE ts>?", (now - seconds,)).fetchone()
               for label, seconds in (("24h", 86400), ("7d", 7 * 86400))}
    crawls = db.execute("SELECT status,COUNT(*) FROM crawl_jobs WHERE created>? GROUP BY 1", (now - 7 * 86400,)).fetchall() \
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='crawl_jobs'").fetchone() else []
    newest_backup = max((p.stat().st_mtime for p in BACKUPS.glob("*.db")), default=0)
    paid_orders = db.execute("SELECT session,plan,created FROM orders WHERE plan NOT IN ('free') AND %s ORDER BY created" % real).fetchall()
    try:
        version = get("http://127.0.0.1:8850/api/status").get("version")
    except Exception:
        version = None
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "version": version,
        "healthy": healthy("http://127.0.0.1:8850/api/status"), "public_healthy": healthy(SITE + "/api/status"),
        "paid_accounts_active": dict(paid), "monthly_recurring_usd": sum(PRICES.get(p, 0) * n for p, n in paid if p != "pass"),
        "paid_orders_total": len(paid_orders), "trials_total": trials, "trials_last_7_days": trials_week,
        "funnel_last_7_days": funnel(7), "funnel_last_24_hours": funnel(1),
        "credits_used": {k: {"credits": max(0, v[0]), "requests": v[1]} for k, v in credits.items()},
        "crawls_last_7_days": dict(crawls),
        "backup_age_hours": round((now - newest_backup) / 3600, 1) if newest_backup else None,
        "_paid_order_ids": [o[0] for o in paid_orders],
        "note": "Counts exclude internal QA accounts. A checkout_created event is a started checkout, not a sale; a sale is a paid order.",
    }


def daily():
    BACKUPS.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    target = BACKUPS / ("daily-%s.db" % time.strftime("%Y%m%d", time.gmtime()))
    with sqlite3.connect("file:%s?mode=ro" % DB, uri=True) as source, sqlite3.connect(target) as dest:
        source.backup(dest)
        intact = dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    os.chmod(target, 0o600)
    if not intact:
        alert("backup", "backup_failed", "Today's scraper database backup failed its integrity check.")
    for old in sorted(BACKUPS.glob("daily-*.db"))[:-14]:
        old.unlink()
    data = report()
    state = load_state()
    known = set(state.get("paid_orders", []))
    fresh = [o for o in data.pop("_paid_order_ids") if o not in known]
    if fresh and "paid_orders" in state:
        alert("sale-%s" % time.strftime("%Y%m%d"), "new_paid_order",
              "%d new paid scraper order(s). Monthly recurring revenue is now $%d. Tell Brian." % (len(fresh), data["monthly_recurring_usd"]), "info")
    state["paid_orders"] = sorted(known | set(fresh))
    save_state(state)
    REPORTS.mkdir(parents=True, exist_ok=True)
    lines = ["# Apex Flow 3D Scraper daily report, %s" % data["generated"], "",
             "- Service: %s (version %s); public site: %s" % ("healthy" if data["healthy"] else "DOWN", data["version"], "healthy" if data["public_healthy"] else "FAILING"),
             "- Paid accounts active: %s; monthly recurring revenue: $%d; paid orders ever: %d" % (data["paid_accounts_active"] or "none", data["monthly_recurring_usd"], data["paid_orders_total"]),
             "- Trials: %d total, %d in the last 7 days" % (data["trials_total"], data["trials_last_7_days"]),
             "- Last 7 days funnel: %s" % (data["funnel_last_7_days"] or "no activity"),
             "- Credits used: %s" % data["credits_used"],
             "- Crawls in the last 7 days: %s" % (data["crawls_last_7_days"] or "none"),
             "- Newest backup: %s hours old" % data["backup_age_hours"], "", data["note"], ""]
    for name, text in (("latest.md", "\n".join(lines)), ("latest.json", json.dumps(data, indent=2)),
                       ("report-%s.md" % time.strftime("%Y%m%d", time.gmtime()), "\n".join(lines))):
        (REPORTS / name).write_text(text, encoding="utf-8")
        owned_by_rainbo(REPORTS / name)
    owned_by_rainbo(REPORTS)
    owned_by_rainbo(REPORTS.parent)
    print(json.dumps(data))
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "report"
    if command == "health":
        sys.exit(health())
    if command == "daily":
        sys.exit(daily())
    output = report()
    output.pop("_paid_order_ids")
    print(json.dumps(output, indent=2))
