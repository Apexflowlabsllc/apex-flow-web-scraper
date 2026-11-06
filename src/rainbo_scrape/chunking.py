"""Heading-aware Markdown chunks for RAG: each chunk keeps its heading path; fenced code is never split."""
import re

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def chunk_markdown(markdown, max_chars=1500):
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 200 <= max_chars <= 8000:
        raise ValueError("chunks must be an integer from 200 to 8000")
    path, blocks, current, fenced = [], [], [], False

    def flush():
        text = "\n".join(current).strip()
        if text:
            blocks.append((list(path), text))
        current.clear()

    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            del path[level - 1:]
            path.append(match.group(2))
            current.append(line)
        elif not fenced and not line.strip():
            flush()  # paragraph boundary
        else:
            current.append(line)
    flush()
    chunks, buffer, owner = [], "", []
    for heading, text in blocks:
        if buffer and (heading != owner or len(buffer) + len(text) + 2 > max_chars):
            chunks.append((owner, buffer))
            buffer = ""
        if not buffer:
            owner = heading
        buffer = (buffer + "\n\n" + text) if buffer else text
    if buffer:
        chunks.append((owner, buffer))
    return [{"index": i, "heading_path": h, "text": t, "chars": len(t)} for i, (h, t) in enumerate(chunks)]
