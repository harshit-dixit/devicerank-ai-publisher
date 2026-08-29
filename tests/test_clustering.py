"""Tests for anti-chaining semantic topic clustering, entity extraction, and 72h fingerprinting."""

from src.fetchers.clustering import (
    StoryCluster,
    TopicClusterer,
    extract_specific_entity_tokens,
    generate_topic_fingerprint,
)
from src.fetchers.rss_fetcher import RawArticle


def test_extract_specific_entity_tokens():
    tokens = extract_specific_entity_tokens(
        "Samsung Galaxy S26 FE Launch Expected with Snapdragon 8 Gen 5",
        "Details on pricing and battery capacity",
    )
    assert "s26" in tokens
    assert "fe" in tokens
    assert "snapdragon" in tokens
    # Generic tech words must be filtered out
    assert "samsung" not in tokens
    assert "launch" not in tokens
    assert "expected" not in tokens
    assert "details" not in tokens


def test_anti_chaining_unrelated_tech_stories_stay_separated():
    """Verifies that diverse stories sharing generic words (Google, AI, search, ads, documentation)
    do NOT chain or merge into a single massive cluster.
    """
    stories = [
        RawArticle(
            title="Google Publisher Dispute Over European News Licensing",
            link="https://reuters.com/google-eu-publishers",
            source_name="Reuters",
            category="tech_news",
            blogger_label="Tech News",
            summary="Publishers across the EU file antitrust complaint against Google search snippets.",
        ),
        RawArticle(
            title="AI Mode Advertising Formats Revealed for Digital Marketers",
            link="https://adweek.com/ai-mode-ads",
            source_name="Adweek",
            category="tech_news",
            blogger_label="Tech News",
            summary="New advertising formats are coming to generative search interfaces.",
        ),
        RawArticle(
            title="Wix Launches Autonomous AI Agents for Web Designers",
            link="https://techcrunch.com/wix-ai-agents",
            source_name="TechCrunch",
            category="tech_news",
            blogger_label="Tech News",
            summary="Wix announced autonomous coding agents for building customer storefronts.",
        ),
        RawArticle(
            title="Global .org Ecommerce Sales Statistics for Q3 2026",
            link="https://wsj.com/ecommerce-stats",
            source_name="WSJ",
            category="tech_news",
            blogger_label="Tech News",
            summary="Ecommerce transaction data reveals shift to mobile checkout.",
        ),
        RawArticle(
            title="Google Search Desktop Redesign Tests Centered Navigation",
            link="https://9to5google.com/search-desktop-redesign",
            source_name="9to5Google",
            category="tech_news",
            blogger_label="Tech News",
            summary="A visual interface test centers the search bar and navigation pills.",
        ),
        RawArticle(
            title="Google Generative AI Documentation Updated for Python Developers",
            link="https://developers.googleblog.com/genai-python-sdk",
            source_name="Google Blog",
            category="tech_news",
            blogger_label="Tech News",
            summary="Updated API reference documentation for the Google GenAI SDK.",
        ),
    ]

    clusters = TopicClusterer.cluster_articles(stories)

    # Every one of these 6 stories is distinct and must remain in its own distinct cluster!
    assert len(clusters) == 6


def test_cluster_same_event_across_multiple_outlets():
    """Verifies that articles covering the exact same event or product across different feeds
    merge into 1 authoritative cluster.
    """
    art1 = RawArticle(
        title="Galaxy S26 FE Leaks Reveal 5,000mAh Battery and Specs",
        link="https://theverge.com/s26-fe-leaks",
        source_name="The Verge",
        category="gadgets",
        blogger_label="Gadgets",
        summary="The upcoming Galaxy S26 FE has leaked with Snapdragon 8 Gen 5.",
    )
    art2 = RawArticle(
        title="Samsung Galaxy S26 FE Detailed in Benchmark Leak",
        link="https://gsmarena.com/s26-fe-specs",
        source_name="GSMArena",
        category="gadgets",
        blogger_label="Gadgets",
        summary="A fresh leak gives us specs and benchmark numbers on the Galaxy S26 FE.",
    )
    art3 = RawArticle(
        title="Galaxy S26 FE Colors and Pricing Spotted",
        link="https://9to5google.com/s26-fe-pricing",
        source_name="9to5Google",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Galaxy S26 FE will start at $599.",
    )

    art4 = RawArticle(
        title="OpenAI Officially Unveils SearchGPT Prototype",
        link="https://techcrunch.com/searchgpt",
        source_name="TechCrunch",
        category="tech_news",
        blogger_label="Tech News",
        summary="OpenAI announced SearchGPT today to compete with traditional search.",
    )

    articles = [art1, art2, art3, art4]
    clusters = TopicClusterer.cluster_articles(articles)

    # Should form exactly 2 clusters: Galaxy S26 FE and SearchGPT
    assert len(clusters) == 2

    s26_cluster = next(c for c in clusters if "s26" in c.canonical_tokens or "s26" in c.canonical_title_entities)
    assert len(s26_cluster.articles) == 3
    assert set(s26_cluster.source_names) == {"The Verge", "GSMArena", "9to5Google"}


def test_filter_by_recent_fingerprints():
    art1 = RawArticle(
        title="Galaxy S26 FE Announcement",
        link="https://theverge.com/s26",
        source_name="The Verge",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Galaxy S26 FE launch.",
    )
    art2 = RawArticle(
        title="RTX 5090 Benchmark Record",
        link="https://tomshardware.com/rtx-5090",
        source_name="Tom's Hardware",
        category="gadgets",
        blogger_label="Gadgets",
        summary="RTX 5090 breaks power records.",
    )

    clusters = TopicClusterer.cluster_articles([art1, art2])
    assert len(clusters) == 2

    # Simulate that Galaxy S26 FE fingerprint was published yesterday
    s26_fp = clusters[0].fingerprint
    recent_fps = {s26_fp.lower()}

    filtered = TopicClusterer.filter_by_recent_fingerprints(clusters, recent_fps)
    assert len(filtered) == 1
    assert "5090" in filtered[0].fingerprint or "rtx" in filtered[0].canonical_title_entities
