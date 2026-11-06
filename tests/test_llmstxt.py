import pytest
from rainbo_scrape.llmstxt import compose


PAGES = [{"url": "https://ex.com/", "title": "Acme Docs", "markdown": "# Acme Docs\n\nAcme makes widgets. More text here."},
         {"url": "https://ex.com/a", "title": "Install", "markdown": "## Install\n\nRun pip install acme."},
         None, {"url": "https://ex.com/empty", "title": "Empty", "markdown": ""}]


def test_compose_index():
    out = compose("https://ex.com/", PAGES)
    assert out.startswith("# Acme Docs\n\n> Acme Docs Acme makes widgets.")
    assert "- [Install](https://ex.com/a): Install Run pip install acme." in out
    assert "empty" not in out and "---" not in out


def test_compose_full_includes_sources():
    out = compose("https://ex.com/", PAGES, full=True)
    assert "<!-- source: https://ex.com/a -->" in out and "Run pip install acme." in out


def test_compose_requires_pages():
    with pytest.raises(ValueError):
        compose("https://ex.com/", [None])


def test_titles_are_flattened_and_duplicates_listed_once():
    pages = [{"url": "https://ex.com/", "title": "A
   B", "markdown": "x one.", "content_sha256": "h"},
             {"url": "https://ex.com/index.html", "title": "A B", "markdown": "x one.", "content_sha256": "h"}]
    out = compose("https://ex.com/", pages)
    assert out.count("](https://ex.com") == 1 and "[A B]" in out
