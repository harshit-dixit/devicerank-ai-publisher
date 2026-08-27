"""Tests for RSS fetcher and article normalization."""

from unittest.mock import MagicMock, patch
from src.fetchers.rss_fetcher import RSSFetcher, RawArticle


def test_clean_summary():
    fetcher = RSSFetcher()
    html_summary = "<p>This is a <strong>major</strong> breakthrough in AI. <a href='#'>Read more</a></p>"
    cleaned = fetcher._clean_summary(html_summary)
    assert cleaned == "This is a major breakthrough in AI. Read more"


def test_extract_image_from_entry():
    fetcher = RSSFetcher()

    # Entry with media_content
    mock_entry = MagicMock()
    mock_entry.media_content = [{"url": "https://example.com/featured.jpg"}]
    del mock_entry.media_thumbnail
    del mock_entry.enclosures
    mock_entry.summary = ""
    mock_entry.content = []

    img = fetcher._extract_image_from_entry(mock_entry)
    assert img == "https://example.com/featured.jpg"


@patch("feedparser.parse")
def test_fetch_feed_mock(mock_parse):
    mock_feed = MagicMock()
    mock_feed.bozo = 0
    mock_feed.entries = [
        {
            "title": "Exciting AI Launch",
            "link": "https://example.com/ai-launch",
            "summary": "Full overview of the product announcement.",
            "published": "Thu, 27 Aug 2026 12:00:00 GMT",
            "media_content": [{"url": "https://example.com/image.png"}],
            "tags": [{"term": "AI"}],
        }
    ]
    mock_parse.return_value = mock_feed

    fetcher = RSSFetcher()
    articles = fetcher.fetch_feed(
        feed_url="https://example.com/rss",
        feed_name="Mock Tech",
        category="tech_news",
        blogger_label="Tech News",
        max_items=1,
        deduplicate=False,
        enrich_content=False,
    )

    assert len(articles) == 1
    assert articles[0].title == "Exciting AI Launch"
    assert articles[0].image_url == "https://example.com/image.png"
    assert articles[0].blogger_label == "Tech News"
