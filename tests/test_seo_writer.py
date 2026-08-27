"""Tests for SEO writer, link sanitization, and HTML output assembling."""

import json
from unittest.mock import MagicMock, patch
from src.agents.seo_writer import GeneratedArticle, SEOWriter
from src.fetchers.rss_fetcher import RawArticle


def test_sanitize_html_links():
    writer = SEOWriter(api_key="test_dummy_key")

    # Sample HTML containing external links, internal links, and relative links
    sample_html = (
        '<p>According to <a href="https://reuters.com/tech-news">Reuters</a>, '
        'NVIDIA announced a new GPU. For past coverage, see '
        '<a href="https://devicerank.blogspot.com/2026/01/nvidia-h200.html">NVIDIA H200 Analysis</a> '
        'or our <a href="/p/archive.html">archives</a>.</p>'
    )

    sanitized = writer._sanitize_html_links(sample_html)

    # Outbound link should be converted to bold text
    assert 'href="https://reuters.com/tech-news"' not in sanitized
    assert "<strong>Reuters</strong>" in sanitized

    # Internal links should be preserved
    assert 'href="https://devicerank.blogspot.com/2026/01/nvidia-h200.html"' in sanitized
    assert 'href="/p/archive.html"' in sanitized


def test_assemble_html_content():
    writer = SEOWriter(api_key="test_dummy_key")
    raw_article = RawArticle(
        title="Google Releases Major Search Core Update",
        link="https://searchengineland.com/google-update-2026",
        source_name="Search Engine Land",
        category="seo_tips",
        blogger_label="SEO Tips",
        summary="A new core update has rolled out focusing on quality content.",
        image_url="https://example.com/google-update.jpg",
    )

    takeaways = [
        "Focus on genuine user intent and E-E-A-T signals.",
        "Thin programmatic pages will see ranking drops.",
        "Semantic search indexing prioritizes original analysis.",
    ]
    faqs = [
        {"question": "How long will the rollout take?", "answer": "Approximately two weeks across global indices."},
    ]
    body = (
        "<h2>Understanding the Core Update</h2>"
        "<p>Google has announced a major shift in search indexing. "
        "Per <a href=\"https://searchengineland.com/article\">Search Engine Land</a>, the focus is on E-E-A-T.</p>"
        "<h2>Why It Matters</h2>"
        "<p>Publishers must audit content depth to maintain high search visibility.</p>"
    )
    title = "Google Core Update 2026: Helpful Content Strategy Guide"
    meta_desc = "Complete breakdown of the new Google search algorithm core update and E-E-A-T rankings."

    html = writer._assemble_html_content(
        raw_article=raw_article,
        title=title,
        meta_description=meta_desc,
        body_content=body,
        takeaways=takeaways,
        faqs=faqs,
    )

    # 1. Key Takeaways Callout Box
    assert "Key Takeaways" in html
    assert "Focus on genuine user intent" in html

    # 2. Semantic Image Figure
    assert "<figure style=\"margin: 20px 0; text-align: center;\">" in html
    assert "https://example.com/google-update.jpg" in html
    assert "loading=\"lazy\"" in html
    assert "<figcaption" in html

    # 3. FAQ Content & Schema
    assert "How long will the rollout take?" in html
    assert "FAQPage" in html
    assert "TechArticle" in html
    assert "application/ld+json" in html

    # 4. Zero Outbound Links
    assert "href=\"https://searchengineland.com" not in html
    assert "<strong>Search Engine Land</strong>" in html
    assert "Originally reported by <strong>Search Engine Land</strong>" in html


@patch.object(SEOWriter, "_call_gemini")
def test_write_article_mock(mock_call):
    mock_response = {
        "title": "Google Search Update 2026: Complete Strategy Guide",
        "meta_description": "Comprehensive guide to Google's latest algorithm core update.",
        "focus_keyword": "Google search update",
        "secondary_keywords": ["SEO 2026", "Helpful content"],
        "key_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        "html_content": "<p>Full in-depth analysis per <a href='https://techcrunch.com'>TechCrunch</a>.</p><h2>Why It Matters</h2><p>Crucial impact analysis.</p>",
        "labels": ["SEO Tips", "Google Update"],
        "faq_items": [
            {"question": "Is this update confirmed?", "answer": "Yes, confirmed by Google Search Central."}
        ],
        "word_count": 850,
    }
    mock_call.return_value = json.dumps(mock_response)

    writer = SEOWriter(api_key="test_key")
    raw_article = RawArticle(
        title="Google Algorithm Update Announced",
        link="https://example.com/update",
        source_name="Search Engine Land",
        category="seo_tips",
        blogger_label="SEO Tips",
        summary="Summary of algorithm changes.",
    )

    generated = writer.write_article(raw_article)

    assert isinstance(generated, GeneratedArticle)
    assert generated.title == "Google Search Update 2026: Complete Strategy Guide"
    assert "SEO Tips" in generated.labels
    assert len(generated.faq_items) == 1
    assert len(generated.key_takeaways) == 3
    assert "application/ld+json" in generated.html_content
    # Ensure zero outbound links in final HTML
    assert "href='https://techcrunch.com'" not in generated.html_content
    assert "href=\"https://techcrunch.com\"" not in generated.html_content


def test_prompts_contain_deslop_rules():
    from src.agents.prompts import ARTICLE_GENERATION_PROMPT, SEO_SYSTEM_PROMPT

    # Check that high-severity AI slop patterns are explicitly forbidden
    assert "delve into" in SEO_SYSTEM_PROMPT
    assert "game-changer" in SEO_SYSTEM_PROMPT
    assert "In today's fast-paced digital world" in SEO_SYSTEM_PROMPT
    assert "Furthermore" in SEO_SYSTEM_PROMPT
    assert "Not X, it is Y" in SEO_SYSTEM_PROMPT or "negation runways" in SEO_SYSTEM_PROMPT
    assert "contractions" in SEO_SYSTEM_PROMPT

    # Check story guidelines in generation prompt
    assert "Lead with the story" in ARTICLE_GENERATION_PROMPT
    assert "Zero AI Slop" in ARTICLE_GENERATION_PROMPT
