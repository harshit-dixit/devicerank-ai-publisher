"""Tests for SQLite history database, story queue, HTTP caching, and deduplication."""

import tempfile
from pathlib import Path
from src.db.history import HistoryDB, StoryStatus


def test_history_db_deduplication():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_history.db"
        db = HistoryDB(db_path=db_path)

        test_url = "https://techcrunch.com/2026/08/27/new-ai-chip/"
        test_title = "New AI Chip Unveiled"

        # Initially should not be processed
        assert not db.is_url_processed(test_url)

        # Mark processed
        db.mark_source_processed(test_url, test_title, "tech_news", status="PROCESSED")
        assert db.is_url_processed(test_url)

        # URLs with trailing slash or minor case differences should also be recognized
        assert db.is_url_processed(test_url + "/")
        assert db.is_url_processed(test_url.upper())


def test_story_queue_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_history.db"
        db = HistoryDB(db_path=db_path)

        test_url = "https://theverge.com/2026/apple-m5"
        test_title = "Apple Announces M5 Processor"
        url_hash = db.hash_url(test_url)

        # 1. Enqueue story
        inserted = db.enqueue_story(
            source_url=test_url,
            source_name="The Verge",
            category="tech_news",
            blogger_label="Tech News",
            title=test_title,
            summary="New M5 chip details and benchmark scores.",
        )
        assert inserted is True

        # Re-enqueueing should return False
        assert db.enqueue_story(
            source_url=test_url,
            source_name="The Verge",
            category="tech_news",
            blogger_label="Tech News",
            title=test_title,
        ) is False

        # 2. Check candidate retrieval
        candidates = db.get_candidate_stories(category="tech_news", statuses=[StoryStatus.NEW])
        assert len(candidates) == 1
        assert candidates[0]["title"] == test_title
        assert candidates[0]["status"] == StoryStatus.NEW

        # 3. Mark selected
        db.mark_story_selected(url_hash, score=0.85)
        candidates_selected = db.get_candidate_stories(category="tech_news", statuses=[StoryStatus.SELECTED])
        assert len(candidates_selected) == 1
        assert candidates_selected[0]["score"] == 0.85

        # 4. Mark publishing (idempotency lock)
        db.mark_story_publishing(url_hash)
        assert db.is_url_processed(test_url)

        # 5. Mark published
        db.mark_story_published(url_hash, blogger_post_id="123456", blogger_url="https://devicerank.blogspot.com/post")
        assert db.is_url_published(test_url)


def test_feed_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_history.db"
        db = HistoryDB(db_path=db_path)

        feed_url = "https://techcrunch.com/feed/"
        assert db.get_feed_cache(feed_url) == {"etag": None, "last_modified": None}

        db.set_feed_cache(feed_url, etag="W/'xyz'", last_modified="Thu, 27 Aug 2026 10:00:00 GMT")
        cache = db.get_feed_cache(feed_url)
        assert cache["etag"] == "W/'xyz'"
        assert cache["last_modified"] == "Thu, 27 Aug 2026 10:00:00 GMT"


def test_record_published_post():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_history.db"
        db = HistoryDB(db_path=db_path)

        post_id = db.record_published_post(
            category="seo_tips",
            title="10 Google Search Console Tactics for 2026",
            source_url="https://searchengineland.com/gsc-tactics",
            meta_description="Learn how to optimize your GSC queries.",
            blogger_post_id="987654321",
            blogger_url="https://devicerank.blogspot.com/2026/08/gsc-tactics.html",
            status="DRAFT",
            labels=["SEO Tips", "Google"],
            word_count=1250,
        )

        assert post_id == 1
        stats = db.get_stats()
        assert stats["total_posts_created"] == 1
        assert stats["status_breakdown"].get("DRAFT") == 1
        assert stats["category_breakdown"].get("seo_tips") == 1
