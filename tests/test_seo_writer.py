"""Tests for SEO writer, structured outputs, exponential backoff retries, and HTML assembly."""

from unittest.mock import MagicMock, patch
import pytest

from src.agents.seo_writer import (
    DigestStoryOutput,
    DeviceRankScorecardItem,
    FAQItem,
    GeneratedArticle,
    SEOArticleOutput,
    SEODigestOutput,
    SEOWriter,
)
from src.fetchers.clustering import StoryCluster
from src.fetchers.rss_fetcher import RawArticle
from src.utils.slots import SlotInfo, SlotType, get_current_slot


def test_assemble_html_content():
    writer = SEOWriter(api_key="test_dummy_key")
    raw_article = RawArticle(
        title="Google Releases Major Search Core Update",
        link="https://searchengineland.com/google-update-2026",
        source_name="Search Engine Land",
        category="seo_tips",
        blogger_label="SEO Tips",
        summary="A new core update has rolled out focusing on quality content.",
        image_url="https://images.unsplash.com/photo-1500534623283-312aade485b7",
    )

    takeaways = [
        "Focus on genuine user intent and E-E-A-T signals.",
        "Thin programmatic pages will see ranking drops.",
        "Semantic search indexing prioritizes original analysis.",
    ]
    faqs = [
        FAQItem(question="How long will the rollout take?", answer="Approximately two weeks across global indices."),
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

    with patch("src.agents.seo_writer.validate_image_url", return_value="https://images.unsplash.com/photo-1500534623283-312aade485b7"):
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

    # 2. Semantic Image Figure with HTTPS
    assert "<figure style=\"margin: 20px 0; text-align: center;\">" in html
    assert "https://images.unsplash.com/photo-1500534623283-312aade485b7" in html
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


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_article_mock(mock_call):
    mock_output = SEOArticleOutput(
        title="Google Search Update 2026: Complete Strategy Guide",
        meta_description="Comprehensive guide to Google's latest algorithm core update.",
        focus_keyword="Google search update",
        secondary_keywords=["SEO 2026", "Helpful content"],
        key_takeaways=["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        html_content="<p>Full in-depth analysis per <a href='https://techcrunch.com'>TechCrunch</a>.</p><h2>Why It Matters</h2><p>Crucial impact analysis.</p>",
        labels=["SEO Tips", "Google Update"],
        faq_items=[
            FAQItem(question="Is this update confirmed?", answer="Yes, confirmed by Google Search Central.")
        ],
        word_count=850,
    )
    mock_call.return_value = mock_output

    writer = SEOWriter(api_key="test_key")
    raw_article = RawArticle(
        title="Google Algorithm Update Announced",
        link="https://example.com/update",
        source_name="Search Engine Land",
        category="seo_tips",
        blogger_label="SEO Tips",
        summary="Summary of algorithm changes.",
    )

    with patch("src.agents.seo_writer.validate_image_url", return_value=None):
        generated = writer.write_article(raw_article)

    assert isinstance(generated, GeneratedArticle)
    assert generated.title == "Google Search Update 2026: Complete Strategy Guide"
    assert "SEO Tips" in generated.labels
    assert len(generated.faq_items) == 1
    assert len(generated.key_takeaways) == 3
    assert "application/ld+json" in generated.html_content
    # Outbound link replaced with bold attribution
    assert "href='https://techcrunch.com'" not in generated.html_content
    assert "<strong>TechCrunch</strong>" in generated.html_content


def test_prompts_contain_deslop_rules():
    from src.agents.prompts import ARTICLE_GENERATION_PROMPT, DIGEST_GENERATION_PROMPT, SEO_SYSTEM_PROMPT

    # Check that high-severity AI slop patterns are explicitly forbidden
    assert "delve into" in SEO_SYSTEM_PROMPT
    assert "game-changer" in SEO_SYSTEM_PROMPT
    assert "In today's fast-paced digital world" in SEO_SYSTEM_PROMPT
    assert "Furthermore" in SEO_SYSTEM_PROMPT
    assert "Not X, it is Y" in SEO_SYSTEM_PROMPT or "negation runways" in SEO_SYSTEM_PROMPT
    assert "contractions" in SEO_SYSTEM_PROMPT

    # Check story guidelines and untrusted source boundary in generation prompt
    assert "Lead with the story" in ARTICLE_GENERATION_PROMPT
    assert "Zero AI Slop" in ARTICLE_GENERATION_PROMPT
    assert "<untrusted_source_content>" in ARTICLE_GENERATION_PROMPT
    assert "exactly {story_count} entries" in DIGEST_GENERATION_PROMPT
    assert "<untrusted_source_content>" in DIGEST_GENERATION_PROMPT


@patch("src.agents.seo_writer.validate_image_url")
@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_digest_includes_deterministic_title_and_standardized_labels(mock_call, mock_img):
    mock_img.side_effect = lambda url, **kwargs: (
        "https://images.unsplash.com/photo-123" if "unsplash" in str(url) else None
    )

    articles = [
        RawArticle(
            title=f"Technology Story {index}",
            link=f"https://example.com/story-{index}",
            source_name=f"Newsroom {index}",
            category="tech_news",
            blogger_label="Tech News",
            published_date=f"2026-08-27T{18 - index:02d}:00:00+00:00",
            summary=f"Verified source context for story {index}.",
            image_url=(
                "https://example.com/digest.jpg"
                if index == 1
                else "https://images.unsplash.com/photo-123" if index == 2 else None
            ),
        )
        for index in range(1, 7)
    ]

    mock_call.return_value = SEODigestOutput(
        topic_phrases=["Pixel 11", "DLSS 5", "iOS 27"],
        meta_description="A concise digest of six recent technology stories and the practical details readers need to understand their impact.",
        focus_keyword="latest technology news",
        secondary_keywords=["tech digest", "technology roundup"],
        key_takeaways=["First trend", "Second trend", "Third trend"],
        stories=[
            DigestStoryOutput(
                summary=f"Factual generated summary for story {index}.",
                why_it_matters=f"Practical impact of story {index}.",
                key_metric_or_shift=f"Shift detail {index}",
            )
            for index in range(1, 7)
        ],
        labels=["Technology"],
    )

    slot_info = SlotInfo(
        slot_type=SlotType.MORNING,
        slot_id="2026-08-29-morning",
        slot_display="Morning Brief",
        description="Morning slot test",
        time_window_utc="00:00 - 07:59 UTC",
    )

    generated = SEOWriter(api_key="test_key").write_digest(articles, slot_info=slot_info)

    # 1. Deterministic Title Grammar Check
    assert generated.title == "Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief"

    # 2. Exactly 4 Standardized Labels
    assert len(generated.labels) == 4
    assert generated.labels == ["Tech News", "DeviceRank Brief", "Morning Brief", "Tech Digest"]

    # 3. HTML Content Verification
    assert generated.html_content.count('class="digest-story"') == 6
    for article in articles:
        assert generated.html_content.count(article.title) >= 1
        assert article.link in generated.source_urls
    assert generated.source_url == articles[0].link
    assert generated.category == "tech_news"
    assert '"@type": "NewsArticle"' in generated.html_content
    assert 'src="https://images.unsplash.com/photo-123"' in generated.html_content
    assert "example.com/digest.jpg" not in generated.html_content
    assert generated.featured_image == "https://images.unsplash.com/photo-123"
    assert generated.slot_id == "2026-08-29-morning"
    assert mock_call.call_args.args[1] is SEODigestOutput


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_digest_with_scorecard(mock_call):
    articles = [
        RawArticle(
            title=f"Hardware Story {index}",
            link=f"https://example.com/hardware-{index}",
            source_name=f"Outlet {index}",
            category="gadgets",
            blogger_label="Gadgets",
            published_date=f"2026-08-27T{18 - index:02d}:00:00+00:00",
            summary=f"Context for gadget {index}.",
        )
        for index in range(1, 7)
    ]

    mock_call.return_value = SEODigestOutput(
        topic_phrases=["Galaxy S26", "M5 Pro", "Quest 4"],
        meta_description="Evening buyer digest with DeviceRank upgrade scorecards.",
        focus_keyword="gadget buying guide",
        key_takeaways=["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        stories=[
            DigestStoryOutput(
                summary=f"Summary {index}.",
                why_it_matters=f"Buyer implication {index}.",
            )
            for index in range(1, 7)
        ],
        scorecards=[
            DeviceRankScorecardItem(
                device_name="Galaxy S26",
                value_score="8.5 / 10",
                longevity_score="7 Years OS",
                privacy_score="On-device NPU",
                repairability_score="6 / 10",
                buying_verdict="Essential upgrade for S22 users.",
            )
        ],
    )

    slot_info = SlotInfo(
        slot_type=SlotType.EVENING,
        slot_id="2026-08-29-evening",
        slot_display="Evening Brief",
        description="Evening slot test",
        time_window_utc="16:00 - 23:59 UTC",
    )

    generated = SEOWriter(api_key="test_key").write_digest(articles, slot_info=slot_info)

    assert generated.title == "Galaxy S26, M5 Pro & Quest 4 — DeviceRank Evening Brief"
    assert generated.labels == ["Gadgets", "DeviceRank Brief", "Evening Brief", "Tech Digest"]
    assert "DeviceRank Buyer Scorecard" in generated.html_content
    assert "Galaxy S26" in generated.html_content
    assert "Essential upgrade for S22 users." in generated.html_content


def test_write_digest_rejects_fewer_than_six_sources():
    article = RawArticle(
        title="Only Story",
        link="https://example.com/only-story",
        source_name="Example",
        category="tech_news",
        blogger_label="Tech News",
    )

    with pytest.raises(ValueError, match="between 6 and 8"):
        SEOWriter(api_key="test_key").write_digest([article] * 5)
