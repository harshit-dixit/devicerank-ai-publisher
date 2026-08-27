"""Tests for candidate story ranking, freshness decay, and diversity scoring."""

from datetime import datetime, timedelta, timezone
from src.fetchers.ranking import StoryRanker
from src.fetchers.rss_fetcher import RawArticle


def test_freshness_decay():
    now = datetime.now(timezone.utc)

    # 1 hour ago -> 1.0
    score_1h = StoryRanker.calculate_freshness_score(now - timedelta(hours=1))
    assert score_1h == 1.0

    # 5 hours ago -> 0.9
    score_5h = StoryRanker.calculate_freshness_score(now - timedelta(hours=5))
    assert score_5h == 0.90

    # 20 hours ago -> 0.55
    score_20h = StoryRanker.calculate_freshness_score(now - timedelta(hours=20))
    assert score_20h == 0.55

    # 72 hours ago -> 0.15
    score_72h = StoryRanker.calculate_freshness_score(now - timedelta(hours=72))
    assert score_72h == 0.15


def test_rank_and_select_diversity_and_freshness():
    now = datetime.now(timezone.utc)

    art1 = RawArticle(
        title="Breaking OpenAI Launch",
        link="https://techcrunch.com/openai-launch",
        source_name="TechCrunch",
        category="tech_news",
        blogger_label="Tech News",
        published_date=(now - timedelta(hours=1)).isoformat(),
        summary="A major announcement from OpenAI with extensive benchmark data.",
        image_url="https://example.com/openai.jpg",
    )

    art2 = RawArticle(
        title="Another OpenAI Article",
        link="https://techcrunch.com/openai-followup",
        source_name="TechCrunch",
        category="tech_news",
        blogger_label="Tech News",
        published_date=(now - timedelta(hours=2)).isoformat(),
        summary="Follow up thoughts on the OpenAI launch.",
        image_url="https://example.com/openai2.jpg",
    )

    art3 = RawArticle(
        title="Google Announces Quantum Milestone",
        link="https://theverge.com/google-quantum",
        source_name="The Verge",
        category="tech_news",
        blogger_label="Tech News",
        published_date=(now - timedelta(hours=2)).isoformat(),
        summary="Google's quantum research lab achieved major breakthrough.",
        image_url="https://example.com/quantum.jpg",
    )

    # Select top 2 articles
    selected = StoryRanker.rank_and_select([art1, art2, art3], limit=2, max_per_source=1)

    assert len(selected) == 2
    # First should be the freshest (art1)
    assert selected[0][0].title == "Breaking OpenAI Launch"
    # Second should be art3 from The Verge due to diversity balancing rather than art2 from TechCrunch
    assert selected[1][0].source_name == "The Verge"
    assert selected[1][0].title == "Google Announces Quantum Milestone"
