"""Minimal Reddit OAuth client for ephemeral weekly topic discovery.

Only a post ID, subreddit, title, creation time, and aggregate engagement are
read. Post bodies, comments, usernames, and profile data are intentionally not
collected or persisted.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import requests

from src.utils.sanitizer import strip_html


REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_ROOT = "https://oauth.reddit.com"
SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,21}$")
USER_AGENT_PATTERN = re.compile(r"\(by\s+/u/[A-Za-z0-9_-]+\)", re.IGNORECASE)


@dataclass(frozen=True)
class RedditTopicSignal:
    post_id: str
    subreddit: str
    title: str
    score: int
    comment_count: int
    created_utc: datetime

    @property
    def source_id(self) -> str:
        """Opaque provenance key that does not retain user content."""
        return f"reddit-topic:{self.post_id}"

    @property
    def ranking_score(self) -> float:
        age_hours = max(
            0.0,
            (datetime.now(timezone.utc) - self.created_utc).total_seconds() / 3600,
        )
        freshness = max(0.0, 1.0 - (age_hours / (14 * 24)))
        return math.log1p(max(self.score, 0)) + 1.5 * math.log1p(
            max(self.comment_count, 0)
        ) + freshness


def parse_subreddits(value: str | Iterable[str]) -> List[str]:
    """Normalize and validate a comma/space-separated subreddit allowlist."""
    raw_values = re.split(r"[,\s]+", value) if isinstance(value, str) else value
    normalized: List[str] = []
    seen = set()
    for raw in raw_values:
        name = str(raw).strip()
        if name.lower().startswith("r/"):
            name = name[2:]
        if not name:
            continue
        if not SUBREDDIT_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid subreddit name: {name!r}")
        key = name.lower()
        if key not in seen:
            seen.add(key)
            normalized.append(name)
    if not normalized:
        raise ValueError("At least one subreddit must be configured")
    return normalized


class RedditTopicFetcher:
    """Fetch top weekly post titles using application-only OAuth."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        timeout_seconds: float = 10.0,
        session: Optional[requests.Session] = None,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()
        self._access_token: Optional[str] = None

        if not self.client_id or not self.client_secret:
            raise ValueError("Reddit client ID and client secret are required")
        if not USER_AGENT_PATTERN.search(self.user_agent):
            raise ValueError(
                "REDDIT_USER_AGENT must be unique and identify a contact, for example "
                "'python:devicerank-weekly:1.0 (by /u/yourname)'"
            )

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token
        response = self.session.post(
            REDDIT_TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            headers={"User-Agent": self.user_agent},
            data={"grant_type": "client_credentials"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Reddit OAuth response did not contain an access token")
        self._access_token = token
        return token

    def fetch_weekly_topics(
        self,
        subreddits: str | Iterable[str],
        limit_per_subreddit: int = 25,
    ) -> List[RedditTopicSignal]:
        """Fetch and rank safe, non-stickied top posts from the previous week."""
        if not 1 <= limit_per_subreddit <= 100:
            raise ValueError("limit_per_subreddit must be between 1 and 100")
        token = self._get_access_token()
        topics: List[RedditTopicSignal] = []
        seen_titles = set()
        oldest_allowed = datetime.now(timezone.utc) - timedelta(days=14)

        for subreddit in parse_subreddits(subreddits):
            response = self.session.get(
                f"{REDDIT_API_ROOT}/r/{subreddit}/top",
                headers={
                    "Authorization": f"bearer {token}",
                    "User-Agent": self.user_agent,
                },
                params={"t": "week", "limit": limit_per_subreddit, "raw_json": 1},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            children = payload.get("data", {}).get("children", [])
            for child in children:
                data = child.get("data", {}) if isinstance(child, dict) else {}
                topic = self._topic_from_api_item(data, oldest_allowed=oldest_allowed)
                if not topic:
                    continue
                title_key = re.sub(r"\W+", " ", topic.title.lower()).strip()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                topics.append(topic)

        return sorted(topics, key=lambda item: item.ranking_score, reverse=True)

    @staticmethod
    def _topic_from_api_item(
        data: dict,
        oldest_allowed: datetime,
    ) -> Optional[RedditTopicSignal]:
        """Extract only the fields permitted by the topic-signal design."""
        if data.get("over_18") or data.get("stickied") or data.get("spoiler"):
            return None
        post_id = str(data.get("id") or "").strip()
        subreddit = str(data.get("subreddit") or "").strip()
        title = " ".join(strip_html(str(data.get("title") or "")).split())[:300]
        if not post_id or not SUBREDDIT_PATTERN.fullmatch(subreddit) or len(title) < 20:
            return None
        if title.lower() in {"[deleted]", "[removed]"}:
            return None
        try:
            created_utc = datetime.fromtimestamp(float(data.get("created_utc")), timezone.utc)
            score = max(0, int(data.get("score") or 0))
            comment_count = max(0, int(data.get("num_comments") or 0))
        except (TypeError, ValueError, OSError):
            return None
        if created_utc < oldest_allowed:
            return None
        return RedditTopicSignal(
            post_id=post_id,
            subreddit=subreddit,
            title=title,
            score=score,
            comment_count=comment_count,
            created_utc=created_utc,
        )
