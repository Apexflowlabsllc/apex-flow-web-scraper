import pytest

from rainbo_scrape.extract import extract
from rainbo_scrape.parser import parse


HTML = b"""<!doctype html><html><head><title>Page</title><script>secret source script</script></head>
<body><nav>Navigation shell</nav><main><h1>Useful content</h1><p>Readable paragraph.</p></main>
<footer>Footer shell</footer></body></html>"""


def test_html_formats_are_opt_in_and_have_distinct_scope():
    default = extract(HTML, "https://example.com", "text/html")
    assert "html" not in default and "raw_html" not in default

    selected = extract(HTML, "https://example.com", "text/html", include_html=True, include_raw_html=True)
    assert "Useful content" in selected["html"]
    assert "Navigation shell" not in selected["html"]
    assert "Navigation shell" in selected["raw_html"]
    assert "secret source script" in selected["raw_html"]


@pytest.mark.asyncio
async def test_isolated_parser_emits_only_requested_html_formats():
    result = await parse(HTML, "https://example.com", "text/html", include_html=True, include_raw_html=True)
    assert "html" in result and "raw_html" in result
    assert "Navigation shell" not in result["html"]
    assert "Navigation shell" in result["raw_html"]
