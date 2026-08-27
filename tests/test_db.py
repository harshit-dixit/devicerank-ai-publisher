"""Tests for SQLite history database and deduplication."""

import tempfile
from pathlib import Path
from src.db.history import HistoryDB


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
