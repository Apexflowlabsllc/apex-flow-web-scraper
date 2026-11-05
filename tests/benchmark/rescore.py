"""Re-score saved markdown for a run label without refetching: python tests/benchmark/rescore.py LABEL"""
import json
import sys
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from score import score  # noqa: E402

label = sys.argv[1]
refs = json.loads((HERE / "references.json").read_text(encoding="utf-8"))
path = HERE / "out" / label / "results.json"
rows = json.loads(path.read_text(encoding="utf-8"))
for r in rows:
    if r["ok"] and r["id"] in refs:
        r["fidelity"] = score((HERE / "out" / label / (r["id"] + ".md")).read_text(encoding="utf-8"), refs[r["id"]])
        f = r["fidelity"]
        if f["failed"]:
            print(r["id"], f"{f['passed']}/{f['total']}", [(c["type"], c.get("text") or c.get("cells")) for c in f["failed"]])
path.write_text(json.dumps(rows, indent=1), encoding="utf-8")
