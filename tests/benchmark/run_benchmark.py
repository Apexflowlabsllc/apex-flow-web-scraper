"""Run the 30-URL benchmark.

  python tests/benchmark/run_benchmark.py LABEL [--render auto] [--only id,id]

Each URL runs in a fresh process, sequentially, with caching off. Writes
tests/benchmark/out/LABEL/results.json and one markdown file per URL.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from score import score  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label", nargs="?", default=time.strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--render", default="auto")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    manifest = json.loads((HERE / "urls.json").read_text(encoding="utf-8"))
    ref_path = HERE / "references.json"
    refs = json.loads(ref_path.read_text(encoding="utf-8")) if ref_path.exists() else {}
    out = HERE / "out" / a.label
    out.mkdir(parents=True, exist_ok=True)
    only = set(filter(None, a.only.split(",")))
    rows = []
    for item in manifest:
        if only and item["id"] not in only:
            continue
        md = out / (item["id"] + ".md")
        try:
            p = subprocess.run([sys.executable, str(HERE / "one.py"), item["url"], a.render, str(md)],
                               capture_output=True, text=True, encoding="utf-8", timeout=150)
            line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
            row = json.loads(line[7:]) if line else {"url": item["url"], "ok": False, "error": "harness_crash",
                                                   "message": p.stderr[-200:], "elapsed_ms": 0}
        except subprocess.TimeoutExpired:
            row = {"url": item["url"], "ok": False, "error": "harness_timeout", "elapsed_ms": 150000}
        row.update(id=item["id"], cat=item["cat"])
        if row["ok"] and item["id"] in refs:
            row["fidelity"] = score(md.read_text(encoding="utf-8"), refs[item["id"]])
        rows.append(row)
        f = row.get("fidelity") or {}
        print(f'{row["id"]:10} ok={row["ok"]!s:5} {row["elapsed_ms"]:6}ms text={row.get("text_chars", "-"):>7} '
              f'h={row.get("headings", "-"):>3} t={row.get("tables", "-"):>2} l={row.get("links", "-"):>4} '
              f'fid={f.get("fidelity", "-")} {row.get("error", "")}', flush=True)
    (out / "results.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


main()
