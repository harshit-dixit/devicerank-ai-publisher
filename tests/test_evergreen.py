"""Regression tests for evergreen topic selection and tutorial generation."""

from unittest.mock import patch

from src.agents.seo_writer import FAQItem, SEOArticleOutput, SEOWriter
from src.evergreen import (
    get_topic_by_id,
    load_evergreen_catalog,
    select_next_topic,
    select_relevant_internal_links,
)


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
        "<h2>How to verify your result</h2><p>Verify the submitted status and inspect a sample URL.</p>"
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
    )

    assert generated.title == selected.topic.title
    assert generated.focus_keyword == selected.topic.primary_keyword
    assert generated.labels == ["Google Search Console Tips", "How To Guides", "Evergreen"]
    assert 'href="https://devicerank.blogspot.com/indexing-errors.html"' in generated.html_content
    assert "href='https://example.com'" not in generated.html_content
    assert "<strong>External source</strong>" in generated.html_content
    assert "FAQPage" in generated.html_content
    assert '"url": ""' not in generated.html_content
    assert mock_call.call_args.kwargs["system_prompt"].startswith("You are the senior tutorial editor")
