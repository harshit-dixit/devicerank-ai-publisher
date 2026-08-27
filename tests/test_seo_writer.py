"""Tests for SEO writer and HTML output assembling."""

import json
from unittest.mock import MagicMock, patch
from src.agents.seo_writer import GeneratedArticle, SEOWriter
from src.fetchers.rss_fetcher import RawArticle


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
    ]
    faqs = [
        {"question": "How long will the rollout take?", "answer": "Approximately two weeks."},
    ]
    body = "<h2>Understanding the Core Update</h2><p>Google has announced a major shift...</p>"
    title = "Google Core Update 2026: Strategy Guide"
    meta_desc = "Complete breakdown of the new Google search algorithm core update."

    html = writer._assemble_html_content(
        raw_article=raw_article,
        title=title,
        meta_description=meta_desc,
        body_content=body,
        takeaways=takeaways,
        faqs=faqs,
    )

    assert "Key Takeaways & Highlights" in html
    assert "https://example.com/google-update.jpg" in html
    assert "How long will the rollout take?" in html
    assert "Search Engine Land" in html
    assert "application/ld+json" in html
    assert "FAQPage" in html
    assert "TechArticle" in html


@patch.object(SEOWriter, "_call_gemini")
def test_write_article_mock(mock_call):
    mock_response = {
        "title": "Google Search Update 2026: Complete Strategy Guide",
        "meta_description": "Comprehensive guide to Google's latest algorithm core update.",
        "focus_keyword": "Google search update",
        "secondary_keywords": ["SEO 2026", "Helpful content"],
        "key_takeaways": ["Takeaway 1", "Takeaway 2"],
        "html_content": "<p>Full in-depth analysis of the algorithm changes.</p>",
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
    assert "Takeaway 1" in generated.key_takeaways
    assert "application/ld+json" in generated.html_content
