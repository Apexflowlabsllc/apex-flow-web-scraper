"""Owned extraction logic: deterministic data with source evidence, never invented values."""
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from jsonschema import Draft202012Validator
from markdownify import markdownify
from pypdf import PdfReader
import soupsieve
from .policy import ScrapeError, canonical_url


def validate_recipe(recipe):
    if not isinstance(recipe, dict) or len(json.dumps(recipe)) > 30000:
        raise ValueError("Recipe must be an object smaller than 30 KB")
    if set(recipe) - {"fields", "schema", "name"}:
        raise ValueError("Recipe only supports fields, schema and name")
    fields = recipe.get("fields", {})
    if not isinstance(fields, dict) or len(fields) > 50:
        raise ValueError("A recipe supports at most 50 fields")
    for name, spec in fields.items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or not isinstance(spec, dict):
            raise ValueError("Invalid recipe field")
        if set(spec)-{"selector", "attribute", "all", "required"}:
            raise ValueError("Unsupported recipe action; scripts and arbitrary browser methods are not accepted")
        selector = spec.get("selector")
        if not isinstance(selector, str) or not 1 <= len(selector) <= 500:
            raise ValueError("Each field needs a CSS selector")
        try:
            soupsieve.compile(selector)
        except Exception as exc:
            raise ValueError("Invalid CSS selector") from exc
        if "attribute" in spec and not re.fullmatch(r"[a-zA-Z0-9_:-]{1,64}", str(spec["attribute"])):
            raise ValueError("Invalid attribute name")
        for key in ("all", "required"):
            if key in spec and not isinstance(spec[key], bool):
                raise ValueError(key+" must be boolean")
    schema = recipe.get("schema")
    if schema is not None:
        # No references: prevents remote resolution and recursive schema bombs.
        if any(k in json.dumps(schema) for k in ('"$ref"', '"$dynamicRef"', '"pattern"', '"patternProperties"')):
            raise ValueError("Schema references and regex patterns are not supported")
        Draft202012Validator.check_schema(schema)
    return recipe


def _text(node):
    return node.get_text(" ", strip=True) if node else ""


def _links(soup, url):
    base = soup.find("base", href=True)
    base_url = urljoin(url, base["href"]) if base else url
    output, seen = [], set()
    for a in soup.select("a[href]")[:5000]:
        try:
            target = canonical_url(urljoin(base_url, a["href"]))
        except ScrapeError:
            continue
        if target not in seen:
            seen.add(target)
            output.append({"url": target, "text": _text(a)[:300], "rel": a.get("rel", [])})
    return output


def _recipe(soup, recipe, url):
    data, evidence, errors = {}, {}, []
    for name, spec in recipe.get("fields", {}).items():
        elements = soup.select(spec["selector"], limit=1000)
        values = []
        for node in elements:
            value = node.get(spec["attribute"]) if spec.get("attribute") else _text(node)
            if isinstance(value, list):
                value = " ".join(value)
            if value is not None and spec.get("attribute") in {"href", "src"}:
                value = urljoin(url, str(value))
            values.append(value)
        data[name] = values if spec.get("all") else (values[0] if values else None)
        evidence[name] = {"source_url": url, "selector": spec["selector"], "attribute": spec.get("attribute"),
                          "matches": len(elements), "method": "css", "observed": bool(values)}
        if spec.get("required") and (data[name] is None or data[name] == [] or data[name] == ""):
            errors.append({"field": name, "error": "required field missing"})
    if recipe.get("schema"):
        for error in Draft202012Validator(recipe["schema"]).iter_errors(data):
            errors.append({"field": ".".join(map(str, error.path)), "error": error.message[:500]})
    return data, evidence, errors


def extract(body, url, content_type="text/html", recipe=None):
    if recipe:
        validate_recipe(recipe)
    sha = hashlib.sha256(body).hexdigest()
    common = {"source_url": url, "fetched_at": datetime.now(timezone.utc).isoformat(),
              "source_sha256": sha, "bytes": len(body), "warnings": [], "untrusted_source": True}
    if "pdf" in content_type or body.startswith(b"%PDF-"):
        reader = PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            raise ScrapeError("encrypted_pdf", "PDF requires a password")
        if len(reader.pages) > 100:
            raise ScrapeError("pdf_page_limit", "PDF exceeds 100 pages")
        pages = []
        for index, page in enumerate(reader.pages):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 10 * 1024 * 1024:
                raise ScrapeError("pdf_stream_limit", "PDF page exceeds decoded stream budget")
            pages.append({"page": index+1, "text": (page.extract_text() or "")[:100000]})
        text = "\n\n".join(p["text"] for p in pages)[:500000]
        return {**common, "kind": "pdf", "title": str((reader.metadata or {}).get("/Title", "")),
                "text": text, "markdown": text, "pages": pages, "links": [], "data": {}, "evidence": {},
                "quality": {"text_chars": len(text), "needs_ocr": not bool(text.strip())}}
    decoded = body.decode("utf-8", errors="replace")
    if "json" in content_type:
        data = json.loads(decoded)
        return {**common, "kind": "json", "title": "", "text": decoded[:500000], "markdown": "",
                "data": data, "links": [], "evidence": {"method": "native-json", "source_url": url}}
    if "xml" in content_type or decoded.lstrip().startswith(("<?xml", "<rss", "<urlset", "<sitemapindex")):
        root = ElementTree.fromstring(body)
        entries, links = [], []
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag in {"item", "entry"}:
                entries.append({child.tag.rsplit("}", 1)[-1]: " ".join(child.itertext()).strip() for child in element})
            if tag in {"loc", "link"}:
                target = element.get("href") or (element.text or "").strip()
                try:
                    links.append({"url": canonical_url(urljoin(url, target)), "text": "", "rel": []})
                except ScrapeError:
                    pass
        text = "\n".join(" ".join(e.values()) for e in entries)[:500000]
        return {**common, "kind": "feed" if entries else "sitemap", "title": "", "text": text,
                "markdown": text, "data": entries[:5000], "links": links[:5000], "evidence": {"method": "xml"}}
    if content_type.startswith("text/plain"):
        return {**common, "kind": "text", "title": "", "text": decoded[:500000], "markdown": decoded[:500000],
                "data": {}, "links": [], "evidence": {"method": "plain-text"}}
    if not ("html" in content_type or decoded.lstrip().startswith(("<!DOCTYPE", "<!doctype", "<html"))):
        raise ScrapeError("unsupported_content", "Supported: HTML, JSON, XML/RSS, text and text-based PDF")
    soup = BeautifulSoup(body, "lxml")
    title, links = _text(soup.title), _links(soup, url)
    metadata = {m.get("property") or m.get("name"): m.get("content") for m in soup.select("meta[content]")
                if m.get("property") or m.get("name")}
    data, evidence, errors = _recipe(soup, recipe or {}, url)
    structured = []
    for node in soup.select('script[type="application/ld+json"]')[:100]:
        try:
            structured.append(json.loads(node.string or node.get_text()))
        except (ValueError, RecursionError):
            common["warnings"].append("Malformed JSON-LD block omitted")
    tables = [[[cell.get_text(" ", strip=True) for cell in row.select("th,td")]
               for row in table.select("tr")[:1000]] for table in soup.select("table")[:50]]
    headings = [{"level": int(h.name[1]), "text": _text(h)} for h in soup.select("h1,h2,h3,h4,h5,h6")[:500]]
    for node in soup.select("script,style,noscript,template,svg,nav,footer,header,aside,form"):
        node.decompose()
    candidates = soup.select("article, main, [role=main]")
    main = max(candidates, key=lambda n: len(_text(n)), default=soup.body or soup)
    text = main.get_text("\n", strip=True)[:500000]
    markdown = markdownify(str(main), heading_style="ATX", strip=["img"])[:500000]
    challenge_markers = (
        "verify you are human", "just a moment...", "checking your browser", "access denied",
        "captcha verification", "complete the security check", "unusual traffic", "security challenge",
        "enable javascript and cookies to continue", "robot or human", "are you a robot")
    challenge = any(x in (title+" "+text[:2000]).lower() for x in challenge_markers)
    if challenge and len(text) < 3000:
        raise ScrapeError("access_challenge", "access challenge detected; extraction paused. An authorized account owner must resolve it in the target service.")
    if len(text) < 80:
        common["warnings"].append("Low text yield; page may need browser rendering")
    if errors:
        common["warnings"].append("Extraction did not satisfy all requested fields")
    return {**common, "kind": "html", "title": title, "text": text, "markdown": markdown,
            "metadata": metadata, "links": links, "headings": headings, "tables": tables,
            "structured_data": structured, "data": data, "evidence": evidence, "validation_errors": errors,
            "quality": {"text_chars": len(text), "field_count": len(data), "validation_passed": not errors}}
