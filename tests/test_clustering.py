"""Tests for semantic topic clustering and multi-source story aggregation."""

from src.fetchers.clustering import StoryCluster, TopicClusterer, extract_topic_tokens
from src.fetchers.rss_fetcher import RawArticle


def test_extract_topic_tokens():
    tokens = extract_topic_tokens("Samsung Galaxy S26 FE Launch Expected with Snapdragon 8 Gen 5", "Details on pricing and battery")
    assert "s26" in tokens
    assert "galaxy" in tokens
    assert "snapdragon" in tokens
    assert "the" not in tokens
    assert "and" not in tokens


def test_cluster_duplicate_topic_articles():
    # 3 articles about Galaxy S26 FE from different outlets
    art1 = RawArticle(
        title="Galaxy S26 FE Leaks Reveal 5,000mAh Battery and Specs",
        link="https://theverge.com/s26-fe-leaks",
        source_name="The Verge",
        category="gadgets",
        blogger_label="Gadgets",
        summary="The upcoming Galaxy S26 FE has leaked with Snapdragon 8 Gen 5.",
    )
    art2 = RawArticle(
        title="Samsung's Galaxy S26 FE Detailed in New Report",
        link="https://gsmarena.com/s26-fe-specs",
        source_name="GSMArena",
        category="gadgets",
        blogger_label="Gadgets",
        summary="A fresh leak gives us specs on the Galaxy S26 FE.",
    )
    art3 = RawArticle(
        title="Galaxy S26 FE Colors and Pricing Spotted",
        link="https://9to5google.com/s26-fe-pricing",
        source_name="9to5Google",
        category="gadgets",
        blogger_label="Gadgets",
        summary="Galaxy S26 FE will start at $599.",
    )

    # 1 separate article about OpenAI Search
    art4 = RawArticle(
        title="OpenAI Officially Unveils SearchGPT Prototype",
        link="https://techcrunch.com/searchgpt",
        source_name="TechCrunch",
        category="tech_news",
        blogger_label="Tech News",
        summary="OpenAI announced SearchGPT today to compete with Google Search.",
    )

    articles = [art1, art2, art3, art4]
    clusters = TopicClusterer.cluster_articles(articles)

    # Should form exactly 2 distinct clusters: one for S26 FE and one for SearchGPT!
    assert len(clusters) == 2

    s26_cluster = next(c for c in clusters if "s26" in c.tokens)
    assert len(s26_cluster.articles) == 3
    assert set(s26_cluster.source_names) == {"The Verge", "GSMArena", "9to5Google"}
    assert len(s26_cluster.source_urls) == 3

    search_cluster = next(c for c in clusters if "searchgpt" in c.tokens or "openai" in c.tokens)
    assert len(search_cluster.articles) == 1
    assert search_cluster.source_names == ["TechCrunch"]


def test_story_cluster_properties():
    art1 = RawArticle(
        title="Meta $18B Settlement Approved",
        link="https://reuters.com/meta-settlement",
        source_name="Reuters",
        category="tech_news",
        blogger_label="Tech News",
        summary="Reuters report on the $18B Meta settlement.",
        full_text="Long detailed text from Reuters.",
    )
    art2 = RawArticle(
        title="Meta to Pay $18 Billion in Landmark Privacy Case",
        link="https://theverge.com/meta-18b",
        source_name="The Verge",
        category="tech_news",
        blogger_label="Tech News",
        summary="The Verge analysis of Meta's $18B payout.",
        full_text="In-depth analysis from The Verge.",
    )

    cluster = StoryCluster(canonical_article=art1, articles=[art1, art2])
    assert set(cluster.source_names) == {"Reuters", "The Verge"}
    assert len(cluster.source_urls) == 2
    assert "Reuters report" in cluster.combined_summary
    assert "The Verge analysis" in cluster.combined_summary
    assert "Long detailed text" in cluster.combined_full_text
    assert "In-depth analysis" in cluster.combined_full_text
