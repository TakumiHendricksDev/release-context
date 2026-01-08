import pytest

from sanitize import (
    _strip_html_preserve_links,
    _safe_truncate,
    summarize_text,
    extract_breaking_changes,
    extract_key_info,
)


def test_strip_html_preserve_links_basic():
    html = "<p>Hello <a href=\"https://example.com\">world</a><br>Line2</p>"
    assert _strip_html_preserve_links(html) == "Hello world (https://example.com)\nLine2"


def test_strip_html_preserve_links_entities_and_tags():
    html = "A&nbsp;&amp;&lt;&gt;B <strong>bold</strong>"
    assert _strip_html_preserve_links(html) == "A &<>B bold"


def test_safe_truncate_balances_backticks_and_fences():
    text = """```
code
``` extra"""
    snip = _safe_truncate(text, 6)
    assert "```" in snip and snip.count("```") % 2 == 0
    assert snip.endswith("…(truncated)…")


def test_safe_truncate_prefers_newline_or_sentence():
    text = "Line1\nLine2 is long"
    snip = _safe_truncate(text, 8)
    assert snip.startswith("Line1")


def test_summarize_text_sanitizes_and_truncates():
    html = "<div>Hi<br>There</div> more text"
    snip = summarize_text(html, 10)
    assert "Hi" in snip and "There" in snip
    assert snip.endswith("…(truncated)…")


def test_extract_breaking_changes():
    text = """
    ## Breaking Changes
    
    This PR removes the old API endpoint.
    The `getUser()` method is now deprecated.
    """
    breaking = extract_breaking_changes(text)
    assert len(breaking) > 0
    assert "removes" in breaking[0].lower() or "deprecated" in breaking[0].lower()


def test_extract_key_info():
    text = "Fixes #123 and closes #456. Related to PR #789. This deprecates the old API."
    info = extract_key_info(text)
    assert "tickets" in info or "related_prs" in info or info.get("has_deprecation")
