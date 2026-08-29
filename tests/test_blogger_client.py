"""Tests for BloggerClient: separate status querying, draft promotion, metadata reconstruction, and error handling."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.seo_writer import GeneratedArticle
from src.db.history import HistoryDB
from src.publishers.blogger_client import BloggerClient


@pytest.fixture
def mock_blogger_service():
    with patch("src.publishers.blogger_client.BloggerClient._authenticate") as mock_auth:
        mock_service = MagicMock()
        posts_mock = MagicMock()
        mock_service.posts.return_value = posts_mock
        mock_auth.return_value = mock_service
        yield mock_service, posts_mock


def test_list_recent_posts_queries_statuses_separately(mock_blogger_service):
    mock_service, posts_mock = mock_blogger_service

    live_mock = MagicMock()
    live_mock.execute.return_value = {
        "items": [
            {"id": "post-1", "title": "Live Post 1", "status": "LIVE"},
            {"id": "post-2", "title": "Live Post 2", "status": "LIVE"},
        ]
    }
    draft_mock = MagicMock()
    draft_mock.execute.return_value = {
        "items": [
            {"id": "post-3", "title": "Draft Post 3", "status": "DRAFT"},
            {"id": "post-1", "title": "Duplicate Post 1", "status": "LIVE"},
        ]
    }

    def side_effect_list(blogId, maxResults, status):
        if status == "LIVE":
            return live_mock
        elif status == "DRAFT":
            return draft_mock
        raise ValueError(f"Unexpected status: {status}")

    posts_mock.list.side_effect = side_effect_list

    client = BloggerClient()
    posts = client.list_recent_posts(max_results=10, fetch_drafts=True)

    assert len(posts) == 3
    post_ids = {p["id"] for p in posts}
    assert post_ids == {"post-1", "post-2", "post-3"}

    calls = posts_mock.list.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["status"] == "LIVE"
    assert calls[1].kwargs["status"] == "DRAFT"


def test_publish_draft_post(mock_blogger_service):
    mock_service, posts_mock = mock_blogger_service

    mock_publish = MagicMock()
    mock_publish.execute.return_value = {
        "id": "draft-101",
        "url": "https://devicerank.blogspot.com/2026/08/promoted.html",
        "status": "LIVE",
    }
    posts_mock.publish.return_value = mock_publish

    client = BloggerClient()
    result = client.publish_draft_post("draft-101")

    assert result["id"] == "draft-101"
    assert result["status"] == "LIVE"
    posts_mock.publish.assert_called_once_with(blogId=client.blog_id, postId="draft-101")


def test_publish_draft_post_rejects_text_only_evergreen_draft(mock_blogger_service):
    _mock_service, posts_mock = mock_blogger_service
    get_request = MagicMock()
    get_request.execute.return_value = {"content": "<p>Text-only tutorial.</p>"}
    posts_mock.get.return_value = get_request
    client = BloggerClient()

    with pytest.raises(RuntimeError, match="found 0 images, but 3 are required"):
        client.publish_draft_post("draft-101", minimum_image_count=3)

    posts_mock.publish.assert_not_called()


def test_sync_remote_ledger_reconstructs_metadata():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = HistoryDB(db_path=Path(tmpdir) / "test_blogger_history.db")

        with patch("src.publishers.blogger_client.history_db", test_db), \
             patch("src.publishers.blogger_client.BloggerClient._authenticate") as mock_auth:
            mock_service = MagicMock()
            posts_mock = MagicMock()
            mock_service.posts.return_value = posts_mock
            mock_auth.return_value = mock_service

            meta_comment = (
                "<!-- devicerank:meta: "
                + json.dumps({
                    "slot_id": "2026-08-29-morning",
                    "sources": ["https://theverge.com/story-1", "https://gsmarena.com/story-2"],
                    "topics": ["Pixel 11", "DLSS 5", "iOS 27"],
                    "fingerprints": ["pixel-11", "dlss-5", "ios-27"],
                })
                + " -->"
            )

            mock_list = MagicMock()
            mock_list.execute.return_value = {
                "items": [
                    {
                        "id": "remote-post-123",
                        "title": "Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief",
                        "url": "https://devicerank.blogspot.com/2026/08/pixel11.html",
                        "content": f"<p>Main post body</p>{meta_comment}",
                        "status": "LIVE",
                        "labels": ["Tech News", "DeviceRank Brief", "Morning Brief", "Tech Digest"],
                        "published": "2026-08-29T06:00:00Z",
                    }
                ]
            }
            posts_mock.list.return_value = mock_list

            client = BloggerClient()
            synced_count = client.sync_remote_ledger(max_posts=10)

            assert synced_count == 1
            assert test_db.is_slot_published("2026-08-29-morning", live_only=True)
            assert test_db.is_url_published("https://theverge.com/story-1")
            assert test_db.is_url_published("https://gsmarena.com/story-2")
            recent_fps = test_db.get_recent_topic_fingerprints(hours=72)
            assert "pixel-11" in recent_fps
            assert "dlss-5" in recent_fps


def test_publish_post_promotes_existing_draft_when_live_mode():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = HistoryDB(db_path=Path(tmpdir) / "test_blogger_history.db")

        with patch("src.publishers.blogger_client.history_db", test_db), \
             patch("src.publishers.blogger_client.BloggerClient._authenticate") as mock_auth:
            mock_service = MagicMock()
            posts_mock = MagicMock()
            mock_service.posts.return_value = posts_mock
            mock_auth.return_value = mock_service

            test_db.record_published_post(
                category="news_digest",
                title="Morning Draft Post",
                blogger_post_id="draft-post-999",
                blogger_url="https://devicerank.blogspot.com/preview",
                status="DRAFT",
                slot_id="2026-08-29-morning",
            )

            mock_list = MagicMock()
            mock_list.execute.return_value = {"items": []}
            posts_mock.list.return_value = mock_list

            mock_publish = MagicMock()
            mock_publish.execute.return_value = {
                "id": "draft-post-999",
                "url": "https://devicerank.blogspot.com/2026/08/morning.html",
                "status": "LIVE",
            }
            posts_mock.publish.return_value = mock_publish

            client = BloggerClient()
            article = GeneratedArticle(
                title="Pixel 11, DLSS 5 & iOS 27 — DeviceRank Morning Brief",
                meta_description="Morning digest description",
                html_content="<p>Morning content</p>",
                labels=["Tech News", "DeviceRank Brief", "Morning Brief", "Tech Digest"],
                word_count=500,
                focus_keyword="pixel 11",
                secondary_keywords=[],
                key_takeaways=[],
                faq_items=[],
                source_url="https://theverge.com/1",
                source_urls=["https://theverge.com/1"],
                source_name="The Verge",
                source_names=["The Verge"],
                category="tech_news",
            )

            result = client.publish_post(article, is_draft=False, slot_id="2026-08-29-morning")

            assert result["id"] == "draft-post-999"
            assert result["status"] == "LIVE"
            posts_mock.publish.assert_called_once_with(blogId=client.blog_id, postId="draft-post-999")
            assert test_db.is_slot_published("2026-08-29-morning", live_only=True)


def test_publish_post_keeps_search_description_clean_and_records_category():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db = HistoryDB(db_path=Path(tmpdir) / "test_evergreen_publish.db")
        with patch("src.publishers.blogger_client.history_db", test_db), \
             patch("src.publishers.blogger_client.BloggerClient._authenticate") as mock_auth:
            mock_service = MagicMock()
            posts_mock = MagicMock()
            mock_service.posts.return_value = posts_mock
            mock_auth.return_value = mock_service

            empty_list = MagicMock()
            empty_list.execute.return_value = {"items": []}
            posts_mock.list.return_value = empty_list
            inserted = MagicMock()
            inserted.execute.return_value = {
                "id": "evergreen-1",
                "url": "https://devicerank.blogspot.com/evergreen-1.html",
                "status": "LIVE",
            }
            posts_mock.insert.return_value = inserted

            description = "A clean search description for one evergreen tutorial."
            article = GeneratedArticle(
                title="How to Submit a Sitemap in Google Search Console",
                meta_description=description,
                html_content="<p>Evergreen tutorial body.</p>",
                labels=["Google Search Console Tips", "How To Guides", "Evergreen"],
                word_count=1400,
                focus_keyword="submit sitemap Google Search Console",
                secondary_keywords=[],
                key_takeaways=[],
                faq_items=[],
                source_url="urn:devicerank:evergreen:gsc-submit-sitemap",
                source_urls=["urn:devicerank:evergreen:gsc-submit-sitemap"],
                source_name="DeviceRank Evergreen Topic Library",
                source_names=["DeviceRank Evergreen Topic Library"],
                category="gsc_tips",
            )

            client = BloggerClient()
            client.publish_post(article, is_draft=False, slot_id="2026-08-29-evergreen")

            body = posts_mock.insert.call_args.kwargs["body"]
            assert "customMetaData" not in body
            assert f'"meta_description": "{description}"' in body["content"]
            assert '"category": "gsc_tips"' in body["content"]
