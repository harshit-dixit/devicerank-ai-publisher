"""Tests for HTML, URL, and entity sanitization."""

from src.utils.sanitizer import escape_feed_text, sanitize_html, sanitize_url


def test_sanitize_url():
    # HTTPS URLs should pass
    assert sanitize_url("https://example.com/image.jpg") == "https://example.com/image.jpg"

    # HTTP should be upgraded to HTTPS
    assert sanitize_url("http://example.com/image.png") == "https://example.com/image.png"

    # Malicious schemes should be rejected
    assert sanitize_url("javascript:alert(1)") is None
    assert sanitize_url("data:text/html;base64,PHNjcmlwdD4=") is None
    assert sanitize_url("file:///etc/passwd") is None
    assert sanitize_url("") is None
    assert sanitize_url(None) is None


def test_escape_feed_text():
    raw = '<script>alert("xss")</script> & "quotes"'
    escaped = escape_feed_text(raw)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
    assert "&amp;" in escaped


def test_sanitize_html_tags_and_scripts():
    html_input = (
        '<div>'
        '<h2>Safe Heading</h2>'
        '<p onclick="alert(1)">Safe text <script>alert("hack")</script></p>'
        '<iframe src="https://evil.com"></iframe>'
        '<img src="https://example.com/pic.jpg" onerror="alert(2)" />'
        '<img src="javascript:alert(3)" />'
        '</div>'
    )

    clean = sanitize_html(html_input)

    assert "<h2>Safe Heading</h2>" in clean
    assert "<script>" not in clean
    assert "<iframe>" not in clean
    assert 'onclick="alert(1)"' not in clean
    assert 'onerror="alert(2)"' not in clean
    assert 'src="https://example.com/pic.jpg"' in clean
    assert 'src="javascript:alert(3)"' not in clean


def test_sanitize_html_outbound_links():
    html_input = (
        '<p>According to <a href="https://techcrunch.com/article">TechCrunch</a>, '
        'Google announced Gemini 3. See our '
        '<a href="https://devicerank.blogspot.com/2026/01/gemini.html">past guide</a> '
        'or <a href="/p/about.html">about us</a>.</p>'
    )

    clean = sanitize_html(html_input, enforce_zero_outbound_links=True)

    # External link should be converted to bold attribution
    assert 'href="https://techcrunch.com/article"' not in clean
    assert "<strong>TechCrunch</strong>" in clean

    # Internal links should be preserved
    assert 'href="https://devicerank.blogspot.com/2026/01/gemini.html"' in clean
    assert 'href="/p/about.html"' in clean
