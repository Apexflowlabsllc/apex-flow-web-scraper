"""Score a saved Markdown file (any scraper's output) with the same references and metrics.

  python tests/benchmark/score_file.py LABEL ID PATH            score PATH as the scraper's output for URL ID
  python tests/benchmark/score_file.py LABEL ID --error "text"  record a failure (blocked, error, empty)

PATH may be a raw .md file or a saved MCP tool result (JSON with a "text"/"markdown" field, or a list of
{"type": "text", "text": ...} blocks). Rows accumulate in tests/benchmark/out/LABEL/results.json.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from score import score  # noqa: E402


def load_markdown(path):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    def dig(node):
        if isinstance(node, str):
            return node
        if isinstance(node, list):
            return "\n".join(filter(None, (dig(x) for x in node)))
        if isinstance(node, dict):
            for key in ("markdown", "text"):
                if key in node:
                    inner = dig(node[key])
                    try:
                        return dig(json.loads(inner)) if inner.lstrip().startswith(("{", "[")) else inner
                    except ValueError:
                        return inner
            for value in node.values():
                found = dig(value)
                if found:
                    return found
        return ""
    return dig(data) or raw


def main():
    label, ident = sys.argv[1], sys.argv[2]
    manifest = {m["id"]: m for m in json.loads((HERE / "urls.json").read_text(encoding="utf-8"))}
    refs = json.loads((HERE / "references.json").read_text(encoding="utf-8"))
    out = HERE / "out" / label
    out.mkdir(parents=True, exist_ok=True)
    row = {"id": ident, "cat": manifest[ident]["cat"], "url": manifest[ident]["url"]}
    if sys.argv[3] == "--error":
        row.update(ok=False, error=sys.argv[4][:200])
    else:
        markdown = load_markdown(sys.argv[3])
        (out / (ident + ".md")).write_text(markdown, encoding="utf-8")
        lines = markdown.splitlines()
        row.update(ok=bool(markdown.strip()), md_chars=len(markdown),
                   headings=sum(bool(re.match(r"^[\s>*+-]*(?:\d+\.\s+)?#{1,6}\s", l)) for l in lines),
                   tables=sum(bool(re.match(r"^\s*\|?\s*:?-{3,}", l)) and "|" in l for l in lines),
                   links=len(re.findall(r"\]\(https?://", markdown)))
        if row["ok"] and ident in refs:
            row["fidelity"] = score(markdown, refs[ident])
    path = out / "results.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows = [r for r in rows if r["id"] != ident] + [row]
    path.write_text(json.dumps(sorted(rows, key=lambda r: r["id"]), indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in row.items() if k != "fidelity"}),
          (row.get("fidelity") or {}).get("fidelity"), [c.get("text") or c.get("cells") for c in (row.get("fidelity") or {}).get("failed", [])])


main()
