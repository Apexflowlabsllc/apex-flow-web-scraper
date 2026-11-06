"""Markdown-fidelity scoring against hand-checked references.

A reference is a list of checks chosen by a human reading the live page:
  phrase   text must appear anywhere in the markdown (case/space-insensitive)
  heading  text must appear on a markdown heading line
  code     text must appear inside a fenced or indented code block
  table    all cells must appear on one markdown table row
  noise    text (cookie banner, nav, ads) must NOT appear
Link targets and emphasis marks are ignored when matching words.
fidelity = passed checks / total checks (None when there are no checks).
"""
import re

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _n(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def _plain(line):
    line = _LINK.sub(lambda m: m.group(1), line)
    return line.replace("**", "").replace("`", "")


def _code_text(md):
    blocks, cur, inside = [], [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            if inside:
                blocks.append("\n".join(cur))
                cur = []
            inside = not inside
        elif inside:
            cur.append(line)
        elif line.startswith("    ") and line.strip():
            blocks.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return _n("\n".join(blocks))


def score(markdown, checks):
    plain_lines = [_plain(l) for l in markdown.splitlines()]
    flat = _n("\n".join(plain_lines))
    heads = [_n(re.sub(r"^[\s>*+-]*(?:\d+\.\s+)?#+\s*", "", l)) for l in plain_lines if re.match(r"^[\s>*+-]*(?:\d+\.\s+)?#{1,6}\s", l)]
    rows = [_n(l) for l in plain_lines if l.count("|") >= 2]
    code = _code_text(markdown)
    results = []
    for c in checks:
        kind, text = c["type"], c.get("text", "")
        if kind == "phrase":
            ok = _n(text) in flat
        elif kind == "heading":
            ok = any(_n(text) in h for h in heads)
        elif kind == "code":
            ok = _n(text) in code
        elif kind == "table":
            cells = [_n(x) for x in c["cells"]]
            ok = any(all(x in r for x in cells) for r in rows)
        elif kind == "noise":
            ok = _n(text) not in flat
        else:
            raise ValueError(kind)
        results.append({"check": c, "ok": ok})
    passed = sum(r["ok"] for r in results)
    return {"passed": passed, "total": len(results),
            "fidelity": round(passed / len(results), 3) if results else None,
            "failed": [r["check"] for r in results if not r["ok"]]}
