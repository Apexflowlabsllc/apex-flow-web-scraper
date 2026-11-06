"""Owned HTML to Markdown converter and main-content selector.

Goals measured by tests/benchmark: headings stay headings (even when the site
wraps them in links), tables become GFM tables, code keeps its language and
line breaks, lists nest, cookie/consent/navigation chrome is dropped, and link
targets are made absolute. No third-party converter is used.
"""
import re
from urllib.parse import urljoin
from bs4 import Comment, NavigableString, Tag

SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed", "audio",
        "video", "source", "track", "map", "title", "meta", "link", "button", "select", "option",
        "input", "textarea", "dialog", "datalist", "optgroup"}
BLOCK = {"p", "div", "section", "article", "main", "header", "footer", "aside", "nav", "ul", "ol", "li",
         "table", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "figure", "figcaption",
         "dl", "dt", "dd", "details", "summary", "form", "fieldset", "address", "body", "html", "tr", "td",
         "th", "thead", "tbody", "tfoot", "caption", "center", "hgroup"}
CHROME_TAGS = {"nav", "footer", "aside"}
CHROME_ROLES = {"navigation", "banner", "complementary", "contentinfo", "dialog", "alertdialog", "search"}
CHROME_CLASSES = {"sidebar", "side-bar", "breadcrumb", "breadcrumbs", "advertisement", "adsbygoogle", "ad-slot",
                  "skip-link", "skip-to-content", "share-buttons", "social-share", "newsletter", "newsletter-signup",
                  "popup", "modal", "cookie-banner", "cookie-notice", "cookie-consent", "cookiebanner", "cookie-bar",
                  "cc-window", "cc-banner", "consent-banner", "toc", "table-of-contents", "site-header", "site-footer",
                  "page-footer", "masthead", "navbar", "topbar", "top-bar", "pagination", "related-posts"}
CHROME_PREFIXES = ("onetrust", "ot-sdk", "didomi", "truste", "osano", "qc-cmp", "sp_message", "usercentrics",
                   "cmplz", "cookieconsent", "cookie-law", "gdpr")
PERMALINK = re.compile(r"^[#¶§🔗\s]*$")


def _tokens(node):
    classes = node.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    values = [c.lower() for c in classes]
    if node.get("id"):
        values.append(str(node["id"]).lower())
    return values


def is_chrome(node):
    if node.name in ("html", "body", "main", "article"):
        return False
    if node.name in CHROME_TAGS:
        return True
    if node.get("role") in CHROME_ROLES or node.get("aria-modal") == "true":
        return True
    if node.has_attr("hidden") or "display:none" in (node.get("style") or "").replace(" ", "").lower():
        return True
    for token in _tokens(node):
        if token in CHROME_CLASSES or token.startswith(CHROME_PREFIXES):
            return True
    return False


def strip_chrome(root):
    """Remove scripts, hidden nodes and site chrome. Page headers stay when they hold the page title."""
    for node in root.find_all(SKIP):
        node.decompose()
    for node in root.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()
    for node in list(root.find_all(True)):
        if node.decomposed:
            continue
        if node.name == "header" and not node.find_parent(("article", "main")) and not node.find(("h1", "h2")):
            node.decompose()
        elif is_chrome(node) and not node.find("h1"):
            node.decompose()


def _text_len(node):
    return len(node.get_text(" ", strip=True))


def _link_density(node):
    total = _text_len(node) or 1
    return sum(_text_len(a) for a in node.find_all("a")) / total


def pick_main(soup):
    """Choose the content region: explicit <main>, one dominant <article>, else the cleaned body."""
    body = soup.body or soup
    total = _text_len(body)
    mains = soup.select("main, [role=main]")
    if mains:
        best = max(mains, key=_text_len)
        if _text_len(best) >= 0.2 * total or _text_len(best) > 1500:
            return best
    articles = [a for a in soup.find_all("article") if not a.find_parent("article")]
    if len(articles) == 1 and _text_len(articles[0]) >= 0.4 * total:
        return articles[0]
    if len(articles) == 1 and _text_len(articles[0]) >= 800 and _link_density(articles[0]) < 0.5:
        return articles[0]
    return body


def lead_h1(soup, main):
    """The page title heading, when it sits in a header outside the chosen content region."""
    if main is (soup.body or soup) or main.find("h1"):
        return None
    heading = soup.find("h1")
    return heading if heading is not None and heading.get_text(strip=True) else None


class Markdown:
    def __init__(self, base_url, images=True):
        self.base, self.images, self.hrefs = base_url, images, []

    # ----- public -----
    def convert(self, root):
        text = "\n\n".join(b for b in self.blocks(root) if b.strip())
        text = re.sub(r"[ \t]+\n", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    # ----- helpers -----
    def url(self, value):
        value = (value or "").strip()
        if not value or value.startswith(("javascript:", "data:", "mailto:", "tel:", "#")):
            return None
        return urljoin(self.base, value)

    def inline_text(self, nodes):
        out = [self.inline(n) for n in nodes]
        return re.sub(r"[ \t\r\n\f]+", " ", "".join(out))

    def finish(self, text):
        text = text.replace("\x00", "\n")
        text = re.sub(r" *\n *", "\n", text)
        return text.strip()

    def inline(self, node):
        if isinstance(node, Comment):
            return ""
        if isinstance(node, NavigableString):
            return str(node)
        if node.name in SKIP:
            return ""
        name = node.name
        if name == "br":
            return "  \x00"
        if name == "img":
            src = self.url(node.get("src") or node.get("data-src"))
            alt = (node.get("alt") or "").strip()
            return f"![{alt}]({src})" if self.images and src and not src.startswith("data:") else alt
        inner = self.inline_text(node.children)
        if name == "a":
            stripped = inner.strip()
            href = self.url(node.get("href"))
            if PERMALINK.match(stripped) and not node.find("img"):
                return ""
            return f"[{stripped}]({href})" if href and stripped else inner
        if not inner.strip():
            return inner
        if name in ("strong", "b"):
            return self.wrap(inner, "**")
        if name in ("em", "i"):
            return self.wrap(inner, "*")
        if name in ("del", "s", "strike"):
            return self.wrap(inner, "~~")
        if name in ("code", "kbd", "samp", "tt"):
            return "`" + inner.strip().replace("`", "'") + "`"
        if name == "q":
            return '"' + inner.strip() + '"'
        return inner

    @staticmethod
    def wrap(inner, mark):
        lead = inner[:len(inner) - len(inner.lstrip())]
        trail = inner[len(inner.rstrip()):]
        return f"{lead}{mark}{inner.strip()}{mark}{trail}"

    def has_block(self, node):
        return any(isinstance(c, Tag) and (c.name in BLOCK or self.has_block(c)) for c in node.children
                   if isinstance(c, Tag) and c.name not in SKIP)

    def link_wrap(self, text):
        if not text or not self.hrefs or not self.hrefs[-1]:
            return text
        return text if text.startswith("[") and text.endswith(")") else f"[{text}]({self.hrefs[-1]})"

    # ----- block walk -----
    def blocks(self, node):
        out, buf = [], []

        def flush():
            if buf:
                text = self.finish(self.inline_text(buf))
                if text:
                    out.append(self.link_wrap(text))
                buf.clear()

        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                buf.append(child)
            elif child.name in SKIP:
                continue
            elif child.name == "a" and self.has_block(child):
                flush()
                self.hrefs.append(self.url(child.get("href")))
                out.extend(self.blocks(child))
                self.hrefs.pop()
            elif child.name in BLOCK:
                flush()
                out.extend(self.block(child))
            else:
                buf.append(child)
        flush()
        return out

    def block(self, node):
        name = node.name
        if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            parts = [self.inline_text([c]) for c in node.children]
            text = self.finish(" ".join(p.strip() for p in parts if p.strip()) if any(
                isinstance(c, Tag) and c.name in ("span", "small", "div") for c in node.children) else "".join(parts))
            return [f"{'#' * int(name[1])} {self.link_wrap(text)}"] if text else []
        if name == "hr":
            return ["---"]
        if name == "pre":
            return [self.code_block(node)]
        if name == "table":
            return [self.table(node)]
        if name in ("ul", "ol"):
            return [self.list(node)]
        if name == "blockquote":
            inner = "\n\n".join(self.blocks(node))
            return ["\n".join("> " + l if l else ">" for l in inner.splitlines())] if inner.strip() else []
        if name == "dt":
            text = self.finish(self.inline_text(node.children))
            return [f"**{text}**"] if text else []
        if name == "summary":
            text = self.finish(self.inline_text(node.children))
            return [f"**{text}**"] if text else []
        if name == "figcaption":
            text = self.finish(self.inline_text(node.children))
            return [f"*{text}*"] if text else []
        return self.blocks(node)

    def code_block(self, node):
        code = node.find("code") or node
        lang = ""
        for tag in (code, node, node.parent):
            if not isinstance(tag, Tag):
                continue
            classes = " ".join(tag.get("class") or [])
            found = re.search(r"(?:language|lang|highlight-source|highlight|brush:?)[-_ ]([a-zA-Z0-9+#_-]+)", classes)
            lang = (tag.get("data-language") or tag.get("data-lang") or (found.group(1) if found else "")).lower()
            if lang and lang not in ("none", "text", "plain", "notranslate", "source"):
                break
            lang = ""
        for br in code.find_all("br"):
            br.replace_with("\n")
        for line in code.select(".line, .token-line, [data-line]"):
            if not line.get_text().endswith("\n"):
                line.append("\n")
        text = code.get_text("").replace("\r\n", "\n").strip("\n").rstrip()
        fence = "````" if "```" in text else "```"
        return f"{fence}{lang}\n{text}\n{fence}"

    def list(self, node):
        ordered = node.name == "ol"
        try:
            number = int(node.get("start", 1))
        except ValueError:
            number = 1
        lines = []
        for item in node.find_all("li", recursive=False):
            parts = [p for p in self.blocks(item) if p.strip()]
            if not parts:
                continue
            marker = f"{number}. " if ordered else "- "
            number += 1
            pad = " " * len(marker)
            body = "\n".join(p if i == 0 else "\n" + p for i, p in enumerate(parts)) if False else "\n".join(parts)
            first, *rest = body.split("\n")
            lines.append(marker + first)
            lines.extend((pad + l) if l else "" for l in rest)
        return "\n".join(lines)

    def cell(self, cell):
        text = self.finish(self.inline_text(cell.children)).replace("\n", " ").replace("|", "\\|")
        return re.sub(r"\s+", " ", text)

    def table(self, node):
        if node.find("table", recursive=True) and node.find("table") is not node:
            inner = node.find("table")
            if inner is not node and inner.find_parent("table") is node:
                return "\n\n".join(self.blocks(node))
        rows = []
        for tr in node.find_all("tr"):
            if tr.find_parent("table") is not node:
                continue
            cells, header = [], bool(tr.find("th")) and not tr.find("td")
            for c in tr.find_all(["th", "td"], recursive=False):
                cells.append(self.cell(c))
                try:
                    span = int(c.get("colspan", 1))
                except ValueError:
                    span = 1
                cells.extend([""] * max(0, min(span, 20) - 1))
            if any(cells):
                rows.append((cells, header))
        if not rows:
            return ""
        width = max(len(r[0]) for r in rows)
        grid = [r[0] + [""] * (width - len(r[0])) for r in rows]
        if len(grid) == 1 and width == 1:
            return grid[0][0]
        head, body = grid[0], grid[1:]
        lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * width) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        caption = node.find("caption")
        title = f"**{self.cell(caption)}**\n\n" if caption and self.cell(caption) else ""
        return title + "\n".join(lines)


def html_to_markdown(root, base_url, images=True, soup=None):
    body = Markdown(base_url, images).convert(root)
    lead = lead_h1(soup, root) if soup is not None else None
    if lead is not None:
        return "# " + lead.get_text(" ", strip=True) + "\n\n" + body
    return body
