"""DOCX to Markdown with zip-bomb limits (no external dependency)."""
import io
import zipfile
from defusedxml import ElementTree
from .policy import ScrapeError

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_UNCOMPRESSED = 40 * 1024 * 1024


def is_docx(body, content_type="", url=""):
    return body[:2] == b"PK" and ("wordprocessingml" in content_type or url.lower().split("?")[0].endswith(".docx"))


def _runs(paragraph):
    parts = []
    for run in paragraph.iter(W + "r"):
        text = "".join(t.text or "" for t in run.iter(W + "t"))
        if not text:
            continue
        props = run.find(W + "rPr")
        if props is not None and props.find(W + "b") is not None:
            text = "**" + text.strip() + "** "
        parts.append(text)
    return "".join(parts).strip()


def docx_to_markdown(body):
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        if len(archive.infolist()) > 500 or sum(i.file_size for i in archive.infolist()) > MAX_UNCOMPRESSED:
            raise ScrapeError("docx_limit", "DOCX exceeds the unpacked size budget")
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ScrapeError("docx_invalid", "Not a readable DOCX document") from exc
    blocks, headings, tables, plain = [], [], [], []
    for node in root.find(W + "body"):
        if node.tag == W + "p":
            text = _runs(node)
            if not text:
                continue
            plain.append(text.replace("**", ""))
            style = node.find(f"{W}pPr/{W}pStyle")
            name = (style.get(W + "val") if style is not None else "") or ""
            if name.lower().startswith("heading") or name == "Title":
                digits = "".join(c for c in name if c.isdigit())
                level = max(1, min(6, int(digits))) if digits else 1
                text = text.replace("**", "")
                headings.append({"level": level, "text": text})
                blocks.append("#" * level + " " + text)
            elif node.find(f"{W}pPr/{W}numPr") is not None:
                blocks.append("- " + text)
            else:
                blocks.append(text)
        elif node.tag == W + "tbl":
            rows = [[" ".join(_runs(p) for p in cell.iter(W + "p")).strip().replace("|", "/")
                     for cell in row.findall(W + "tc")] for row in node.findall(W + "tr")]
            rows = [r for r in rows if any(r)]
            if rows:
                tables.append(rows)
                width = max(len(r) for r in rows)
                grid = [r + [""] * (width - len(r)) for r in rows]
                lines = ["| " + " | ".join(grid[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
                lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
                blocks.append("\n".join(lines))
    markdown = "\n\n".join(blocks)[:500000]
    text = "\n".join(plain)[:500000]
    return {"title": headings[0]["text"] if headings else "", "text": text, "markdown": markdown,
            "headings": headings, "tables": tables}
