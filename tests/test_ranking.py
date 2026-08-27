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


def test_select_latest_orders_by_date_and_limits_each_source():
    now = datetime.now(timezone.utc)
    articles = [
        RawArticle(
            title=f"Story {index}",
            link=f"https://example.com/{index}",
            source_name="Wire A" if index < 3 else f"Wire {index}",
            category="tech_news",
            blogger_label="Tech News",
            published_date=(now - timedelta(hours=index)).isoformat(),
        )
        for index in range(6)
    ]

    selected = StoryRanker.select_latest(articles, limit=5, max_per_source=2)

    assert len(selected) == 5
    assert selected[0][0].title == "Story 0"
    assert selected[1][0].title == "Story 1"
    assert all(article.title != "Story 2" for article, _score in selected)


def test_select_latest_relaxes_source_limit_to_fill_batch():
    now = datetime.now(timezone.utc)
    articles = [
        RawArticle(
            title=f"Single-source Story {index}",
            link=f"https://example.com/single/{index}",
            source_name="Only Wire",
            category="tech_news",
            blogger_label="Tech News",
            published_date=(now - timedelta(minutes=index)).isoformat(),
        )
        for index in range(6)
    ]

    selected = StoryRanker.select_latest(articles, limit=6, max_per_source=2)

    assert len(selected) == 6
    assert [article.title for article, _score in selected] == [
        f"Single-source Story {index}" for index in range(6)
    ]


def test_select_latest_keeps_final_digest_in_chronological_order_after_fallback():
    now = datetime.now(timezone.utc)
    articles = [
        RawArticle(
            title=f"Wire A Story {index}",
            link=f"https://example.com/a/{index}",
            source_name="Wire A",
            category="tech_news",
            blogger_label="Tech News",
            published_date=(now - timedelta(minutes=index)).isoformat(),
        )
        for index in range(3)
    ] + [
        RawArticle(
            title=f"Wire B Story {index}",
            link=f"https://example.com/b/{index}",
            source_name="Wire B",
            category="tech_news",
            blogger_label="Tech News",
            published_date=(now - timedelta(hours=1, minutes=index)).isoformat(),
        )
        for index in range(3)
    ]

    selected = StoryRanker.select_latest(articles, limit=6, max_per_source=2)
    selected_dates = [
        datetime.fromisoformat(article.published_date) for article, _score in selected
    ]

    assert selected_dates == sorted(selected_dates, reverse=True)
