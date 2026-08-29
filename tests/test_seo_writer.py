"""Tests for SEO writer, slot-specific typed outputs, quality gates, and HTML assembly."""

from unittest.mock import MagicMock, patch
import pytest

from src.agents.seo_writer import (
    DeviceRankScorecardItem,
    DigestStoryOutput,
    EveningDigestOutput,
    EveningStoryOutput,
    FAQItem,
    GeneratedArticle,
    MiddayDigestOutput,
    MiddayLeadStoryOutput,
    MorningDigestOutput,
    MorningStoryOutput,
    SEOArticleOutput,
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
    assert "href='https://techcrunch.com'" not in generated.html_content
    assert "<strong>TechCrunch</strong>" in generated.html_content


def test_prompts_contain_deslop_rules():
    from src.agents.prompts import MORNING_DIGEST_PROMPT, SEO_SYSTEM_PROMPT

    prompt_lower = SEO_SYSTEM_PROMPT.lower()
    assert "delve into" in prompt_lower
    assert "game-changer" in prompt_lower
    assert "in today's fast-paced digital world" in prompt_lower
    assert "furthermore" in prompt_lower
    assert "not only x, but also y" in prompt_lower or "negation runways" in prompt_lower
    assert "contractions" in prompt_lower
    assert "<untrusted_source_content>" in MORNING_DIGEST_PROMPT


@patch("src.agents.seo_writer.validate_image_url")
@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_morning_digest(mock_call, mock_img):
    mock_img.side_effect = lambda url, **kwargs: (
        "https://images.unsplash.com/photo-123" if "unsplash" in str(url) else None
    )

    articles = [
        RawArticle(
            title=f"Technology Story {index}",
            link=f"https://newsoutlet{index}.com/story-{index}",
            source_name=f"Newsroom {index}",
            category="tech_news",
            blogger_label="Tech News",
            published_date=f"2026-08-27T{18 - index:02d}:00:00+00:00",
            summary=f"Verified source context for story {index}.",
            image_url="https://images.unsplash.com/photo-123" if index == 1 else None,
        )
        for index in range(1, 7)
    ]

    mock_call.return_value = MorningDigestOutput(
        topic_phrases=["Pixel 11", "DLSS 5", "iOS 27"],
        meta_description="Morning digest of six key technology developments.",
        focus_keyword="tech morning brief",
        secondary_keywords=["morning tech digest"],
        key_takeaways=["Highlight 1", "Highlight 2", "Highlight 3"],
        stories=[
            MorningStoryOutput(
                summary=f"Overnight update for story {index}.",
                why_it_matters=f"Consumer impact for story {index}.",
                key_metric_delta=f"15% IPC gain in benchmark #{index}",
            )
            for index in range(1, 7)
        ],
    )

    slot_info = SlotInfo(
        slot_type=SlotType.MORNING,
        slot_id="2026-08-29-morning",
        slot_display="Morning Brief",
        description="Morning slot test",
        time_window_utc="00:00 - 07:59 UTC",
    )

    generated = SEOWriter(api_key="test_key").write_digest(articles, slot_info=slot_info)

    assert generated.title == "Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief"
    assert generated.labels == ["Tech News", "DeviceRank Brief", "Morning Brief", "Tech Digest"]
    assert generated.html_content.count('class="digest-story"') == 6
    assert "15% IPC gain in benchmark #1" in generated.html_content
    assert "⚡ Key Metric / Delta:" in generated.html_content
    assert mock_call.call_args.args[1] is MorningDigestOutput


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_midday_digest_with_comparison_matrix(mock_call):
    # Cluster 1: Multi-source lead story
    art1 = RawArticle(
        title="Snapdragon 8 Gen 5 Benchmark Revealed",
        link="https://theverge.com/snapdragon-8-gen-5",
        source_name="The Verge",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Qualcomm reveals 3nm flagship processor.",
    )
    art2 = RawArticle(
        title="Qualcomm Snapdragon 8 Gen 5 Tested Across 10 Games",
        link="https://gsmarena.com/sd-8-gen-5-gaming",
        source_name="GSMArena",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Gaming benchmark scores for Snapdragon 8 Gen 5.",
    )
    lead_cluster = StoryCluster(canonical_article=art1, articles=[art1, art2])

    art3 = RawArticle(
        title="M5 Mac Studio Available for Preorder",
        link="https://9to5mac.com/m5-mac-studio",
        source_name="9to5Mac",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Apple opens preorders for M5 Mac Studio.",
    )
    art4 = RawArticle(
        title="Sony Announces DualSense Pro Wireless Controller",
        link="https://ign.com/dualsense-pro",
        source_name="IGN",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Sony unveils modular controller with hall effect sticks.",
    )

    clusters = [lead_cluster, art3, art4]

    mock_call.return_value = MiddayDigestOutput(
        topic_phrases=["Snapdragon 8 Gen 5", "M5 Mac Studio", "DualSense Pro"],
        meta_description="Midday deep synthesis and hardware comparison matrix.",
        focus_keyword="snapdragon 8 gen 5 vs m5",
        key_takeaways=["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        lead_story=MiddayLeadStoryOutput(
            headline="Snapdragon 8 Gen 5 Architecture & Benchmark Breakdown",
            summary="Comprehensive synthesis of Qualcomm's 3nm chip.",
            core_conflict_and_engineering="Oryon CPU cores operate at 4.4GHz with 30W peak power draw.",
            market_implications="Sets new flagship performance bar against Apple M-series.",
        ),
        supporting_stories=[
            DigestStoryOutput(summary="M5 Mac Studio preorders open.", why_it_matters="Pro workstation upgrade."),
            DigestStoryOutput(summary="DualSense Pro features modular sticks.", why_it_matters="Eliminates stick drift."),
        ],
        comparison_table_html=(
            "<table><thead><tr><th>Chip</th><th>Node</th><th>Peak Clock</th></tr></thead>"
            "<tbody><tr><td>Snapdragon 8 Gen 5</td><td>3nm</td><td>4.4GHz</td></tr></tbody></table>"
        ),
    )

    slot_info = SlotInfo(
        slot_type=SlotType.MIDDAY,
        slot_id="2026-08-29-midday",
        slot_display="Midday Brief",
        description="Midday slot test",
        time_window_utc="08:00 - 15:59 UTC",
    )

    generated = SEOWriter(api_key="test_key").write_digest(clusters, slot_info=slot_info)

    assert generated.title == "Snapdragon 8 Gen 5, M5 Mac Studio & DualSense Pro — DeviceRank Midday Brief"
    assert "Corroborated by: <strong>The Verge, GSMArena</strong>" in generated.html_content
    assert "DeviceRank Technical Comparison Matrix" in generated.html_content
    assert "Snapdragon 8 Gen 5 Architecture & Benchmark Breakdown" in generated.html_content
    assert mock_call.call_args.args[1] is MiddayDigestOutput


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_evening_digest_with_scorecards(mock_call):
    articles = [
        RawArticle(
            title=f"Hardware Story {index}",
            link=f"https://outlet{index}.com/hardware-{index}",
            source_name=f"Outlet {index}",
            category="gadgets",
            blogger_label="Gadgets",
            published_date=f"2026-08-27T{18 - index:02d}:00:00+00:00",
            summary=f"Context for gadget {index}.",
        )
        for index in range(1, 5)
    ]

    mock_call.return_value = EveningDigestOutput(
        topic_phrases=["Galaxy S26", "M5 Pro", "Quest 4"],
        meta_description="Evening buyer digest with DeviceRank upgrade scorecards.",
        focus_keyword="gadget buying guide",
        key_takeaways=["Takeaway 1", "Takeaway 2", "Takeaway 3"],
        stories=[
            EveningStoryOutput(
                summary=f"Summary {index}.",
                buyer_privacy_implications=f"Buyer implication {index}.",
            )
            for index in range(1, 5)
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
    assert "DeviceRank Buyer Scorecards" in generated.html_content
    assert "Galaxy S26" in generated.html_content
    assert "Essential upgrade for S22 users." in generated.html_content
    assert mock_call.call_args.args[1] is EveningDigestOutput
