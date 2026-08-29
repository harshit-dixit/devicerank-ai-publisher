"""Regression tests for evergreen topic selection and tutorial generation."""

from datetime import datetime, timezone
from pathlib import Path

from unittest.mock import patch

from src.agents.seo_writer import FAQItem, SEOArticleOutput, SEOWriter
from src.evergreen import (
    get_topic_by_id,
    load_evergreen_catalog,
    select_next_topic,
    select_relevant_internal_links,
)
from src.google_sources import (
    GoogleEvidence,
    get_category_google_sources,
    is_official_google_url,
    load_google_source_catalog,
)
from src.main import _resolve_evergreen_slot


def test_catalog_has_only_the_approved_categories_and_teaching_titles():
    catalog = load_evergreen_catalog()

    assert list(catalog.categories) == [
        "seo_tips",
        "adsense_tips",
        "digital_marketing_tips",
        "blogging_tips",
        "wordpress_tips",
        "shopify_tips",
        "gsc_tips",
        "ga4_tips",
    ]
    assert sum(len(category.topics) for category in catalog.categories.values()) == 40
    assert all("2026" not in topic.title for category in catalog.categories.values() for topic in category.topics)


def test_topic_rotation_balances_categories_and_never_reuses_a_source_id():
    catalog = load_evergreen_catalog()
    first = select_next_topic(catalog, set())
    assert first is not None
    assert first.category_key == "seo_tips"

    second = select_next_topic(catalog, {first.topic.source_id})
    assert second is not None
    assert second.category_key == "adsense_tips"
    assert second.topic.source_id != first.topic.source_id

    ga4 = select_next_topic(catalog, set(), category_key="ga4_tips")
    assert ga4 is not None
    assert ga4.topic.id == "ga4-key-events-leads"


def test_internal_links_are_relevant_and_limited_to_devicerank():
    selected = get_topic_by_id(load_evergreen_catalog(), "seo-topic-clusters")
    assert selected is not None
    posts = [
        {
            "title": "How to Find Keywords for a Topic Cluster",
            "blogger_url": "https://devicerank.blogspot.com/keywords.html",
            "category": "seo_tips",
        },
        {
            "title": "Unrelated Phone Launch",
            "blogger_url": "https://devicerank.blogspot.com/phone.html",
            "category": "tech_news",
        },
        {
            "title": "External SEO Guide",
            "blogger_url": "https://example.com/seo.html",
            "category": "seo_tips",
        },
        {
            "title": "Misleading Internal-Looking Link",
            "blogger_url": "https://example.com/devicerank.blogspot.com/seo.html",
            "category": "seo_tips",
        },
    ]

    links = select_relevant_internal_links(selected, posts)

    assert [link["title"] for link in links] == ["How to Find Keywords for a Topic Cluster"]


def test_google_source_catalog_matches_categories_and_rejects_lookalike_hosts():
    evergreen_catalog = load_evergreen_catalog()
    source_catalog = load_google_source_catalog()

    assert set(source_catalog.categories) == set(evergreen_catalog.categories)
    assert all(get_category_google_sources(key) for key in evergreen_catalog.categories)
    assert is_official_google_url("https://developers.google.com/search/docs/fundamentals/seo-starter-guide")
    assert not is_official_google_url("https://developers.google.com.example.com/fake")
    assert not is_official_google_url("http://support.google.com/analytics")


def test_scheduled_slots_use_the_ist_date_and_two_unique_names():
    morning_date, morning_slot = _resolve_evergreen_slot(
        "auto", datetime(2026, 8, 29, 3, 57, tzinfo=timezone.utc)
    )
    evening_date, evening_slot = _resolve_evergreen_slot(
        "auto", datetime(2026, 8, 29, 12, 57, tzinfo=timezone.utc)
    )

    assert (morning_date, morning_slot) == ("2026-08-29", "morning")
    assert (evening_date, evening_slot) == ("2026-08-29", "evening")
    workflow = Path(".github/workflows/publisher.yml").read_text(encoding="utf-8")
    assert "cron: '57 3 * * *'" in workflow
    assert "cron: '57 12 * * *'" in workflow


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_evergreen_enforces_metadata_and_preserves_only_internal_links(mock_call):
    selected = get_topic_by_id(load_evergreen_catalog(), "gsc-submit-sitemap")
    assert selected is not None
    filler = " ".join(["useful practical instruction"] * 430)
    body = (
        "<p>Submit the correct XML sitemap and then confirm the status.</p>"
        "<h2>What you need before starting</h2><p>Check ownership and sitemap access.</p>"
        "<h2>Step-by-step instructions</h2><p>Follow these steps carefully. [[INTERNAL_LINK_1]]</p>"
        "<h2>Illustrative example</h2><p>This is an example, not a measured result.</p>"
        "<h2>Common mistakes</h2><p>A common mistake is submitting a page URL.</p>"
        "<h2>How to verify your result</h2><p>Verify the submitted status and inspect a sample URL. "
        "[[GOOGLE_CITATION_1]]</p>"
        "<h2>Limitations and next action</h2>"
        "<p>Interface labels can change. <a href='https://example.com'>External source</a>.</p>"
        f"<p>{filler}</p>"
    )
    meta = (
        "Learn how to submit a sitemap in Google Search Console, check its status, fix common errors, "
        "and verify that Google can discover your pages."
    )
    assert 135 <= len(meta) <= 160
    mock_call.return_value = SEOArticleOutput(
        title=selected.topic.title,
        meta_description=meta,
        focus_keyword=selected.topic.primary_keyword,
        secondary_keywords=["XML sitemap", "Search Console sitemap"],
        key_takeaways=["Use the correct sitemap URL.", "Read the status carefully.", "Verify sample pages."],
        html_content=body,
        labels=["Ignore This Model Label"],
        faq_items=[
            FAQItem(question="Is a sitemap required?", answer="It helps discovery but does not guarantee indexing."),
            FAQItem(question="Can I submit more than one?", answer="Yes, when the site structure needs multiple sitemap files."),
            FAQItem(question="How quickly will it work?", answer="Processing time varies, so check the report again later."),
        ],
        word_count=1400,
    )

    generated = SEOWriter(api_key="test-key").write_evergreen(
        selected,
        internal_links=[
            {
                "title": "How to Fix Indexing Errors in Search Console",
                "blogger_url": "https://devicerank.blogspot.com/indexing-errors.html",
                "category": "gsc_tips",
            }
        ],
        google_sources=[
            GoogleEvidence(
                title="Search Console Sitemaps Report",
                url="https://support.google.com/webmasters/answer/7451001?hl=en",
                excerpt="Use the Sitemaps report to submit a sitemap and review its status.",
            )
        ],
    )

    assert generated.title == selected.topic.title
    assert generated.focus_keyword == selected.topic.primary_keyword
    assert generated.labels == ["Google Search Console Tips", "How To Guides", "Evergreen"]
    assert 'href="https://devicerank.blogspot.com/indexing-errors.html"' in generated.html_content
    assert "href='https://example.com'" not in generated.html_content
    assert "<strong>External source</strong>" in generated.html_content
    assert 'href="https://support.google.com/webmasters/answer/7451001?hl=en"' in generated.html_content
    assert "FAQPage" not in generated.html_content
    assert "BlogPosting" in generated.html_content
    assert '"url": ""' not in generated.html_content
    assert mock_call.call_args.kwargs["system_prompt"].startswith("You are the senior tutorial editor")


def test_meta_description_normalizer_shortens_at_a_word_boundary():
    description = (
        "Learn a practical workflow for checking important SEO details, fixing common errors, "
        "and confirming that every change works correctly without guesswork or confusion."
    )
    normalized = SEOWriter._normalize_meta_description(description)

    assert 140 <= len(normalized) <= 155
    assert normalized.endswith(".")


@patch.object(SEOWriter, "_call_gemini_structured")
def test_write_evergreen_rewrites_a_draft_that_fails_quality_gates(mock_call):
    selected = get_topic_by_id(load_evergreen_catalog(), "seo-topic-clusters")
    assert selected is not None
    valid_body = (
        "<p>Build a clear topic cluster by connecting one pillar page to focused guides.</p>"
        "<h2>What you need before starting</h2><p>Choose one audience problem.</p>"
        "<h2>Step-by-step instructions</h2><p>Map the pillar and supporting pages.</p>"
        "<h2>Illustrative example</h2><p>This example uses a small tutorial site.</p>"
        "<h2>Common mistakes</h2><p>A common mistake is overlapping search intent.</p>"
        "<h2>How to verify your result</h2><p>Verify every supporting page has a useful link.</p>"
        "<h2>Limitations and next action</h2><p>Review the cluster when content changes.</p>"
        f"<p>{' '.join(['clear practical instruction'] * 430)}</p>"
    )
    common = {
        "title": selected.topic.title,
        "meta_description": (
            "Learn how to build an SEO topic cluster with clear search intent, useful internal links, "
            "and a simple method to check every supporting page."
        ),
        "focus_keyword": selected.topic.primary_keyword,
        "secondary_keywords": ["pillar page", "internal linking"],
        "key_takeaways": ["Map intent first.", "Connect useful pages.", "Verify every link."],
        "labels": ["SEO Tips"],
        "faq_items": [
            FAQItem(question="What is a pillar page?", answer="It is the main guide for a broad subject."),
            FAQItem(question="How many pages are needed?", answer="Use only the pages needed to cover distinct intent."),
            FAQItem(question="Should every page link back?", answer="Link when it genuinely helps the reader continue."),
        ],
        "word_count": 1400,
    }
    rejected = SEOArticleOutput(html_content="<p>Too short.</p>", **common)
    accepted = SEOArticleOutput(html_content=valid_body, **common)
    mock_call.side_effect = [rejected, accepted]

    generated = SEOWriter(api_key="test-key").write_evergreen(selected)

    assert generated.word_count >= 1200
    assert mock_call.call_count == 2
    assert "<quality_feedback>" in mock_call.call_args_list[1].args[0]
