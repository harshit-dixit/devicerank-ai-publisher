from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from src.fetchers.reddit_fetcher import (
    RedditTopicFetcher,
    parse_subreddits,
)


def test_parse_subreddits_normalizes_prefixes_and_duplicates():
    assert parse_subreddits("r/SEO, blogging seo") == ["SEO", "blogging"]


def test_parse_subreddits_rejects_paths_and_empty_values():
    with pytest.raises(ValueError, match="Invalid subreddit"):
        parse_subreddits("SEO/top")
    with pytest.raises(ValueError, match="At least one"):
        parse_subreddits(" , ")


def test_reddit_user_agent_must_identify_contact():
    with pytest.raises(ValueError, match="REDDIT_USER_AGENT"):
        RedditTopicFetcher("client", "secret", "python-requests")


def test_fetcher_collects_only_ephemeral_topic_signal_fields():
    session = Mock()
    token_response = Mock()
    token_response.json.return_value = {"access_token": "oauth-token"}
    token_response.raise_for_status.return_value = None
    session.post.return_value = token_response

    listing_response = Mock()
    listing_response.raise_for_status.return_value = None
    listing_response.json.return_value = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "abc123",
                        "subreddit": "SEO",
                        "title": "How should a beginner organize a useful site audit?",
                        "score": 120,
                        "num_comments": 45,
                        "created_utc": (
                            datetime.now(timezone.utc) - timedelta(days=1)
                        ).timestamp(),
                        "over_18": False,
                        "stickied": False,
                        "spoiler": False,
                        "selftext": "This body must never be collected.",
                        "author": "private-user-name",
                    }
                },
                {
                    "data": {
                        "id": "nsfw1",
                        "subreddit": "SEO",
                        "title": "This title is long enough but should be filtered out",
                        "score": 500,
                        "num_comments": 80,
                        "created_utc": datetime.now(timezone.utc).timestamp(),
                        "over_18": True,
                        "stickied": False,
                        "spoiler": False,
                    }
                },
            ]
        }
    }
    session.get.return_value = listing_response

    fetcher = RedditTopicFetcher(
        "client",
        "secret",
        "python:devicerank-weekly:1.0 (by /u/device_owner)",
        session=session,
    )
    topics = fetcher.fetch_weekly_topics("r/SEO", limit_per_subreddit=10)

    assert len(topics) == 1
    assert topics[0].source_id == "reddit-topic:abc123"
    assert topics[0].title == "How should a beginner organize a useful site audit?"
    assert not hasattr(topics[0], "selftext")
    assert not hasattr(topics[0], "author")
    session.get.assert_called_once()
    assert session.get.call_args.kwargs["params"]["t"] == "week"


def test_fetcher_deduplicates_matching_titles_across_subreddits():
    session = Mock()
    token_response = Mock()
    token_response.json.return_value = {"access_token": "oauth-token"}
    token_response.raise_for_status.return_value = None
    session.post.return_value = token_response

    listing_response = Mock()
    listing_response.raise_for_status.return_value = None
    listing_response.json.return_value = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "one",
                        "subreddit": "blogging",
                        "title": "How can beginners plan a clear tutorial structure?",
                        "score": 20,
                        "num_comments": 5,
                        "created_utc": datetime.now(timezone.utc).timestamp(),
                    }
                }
            ]
        }
    }
    session.get.return_value = listing_response
    fetcher = RedditTopicFetcher(
        "client",
        "secret",
        "python:devicerank-weekly:1.0 (by /u/device_owner)",
        session=session,
    )

    topics = fetcher.fetch_weekly_topics("blogging,SEO", limit_per_subreddit=10)

    assert len(topics) == 1
