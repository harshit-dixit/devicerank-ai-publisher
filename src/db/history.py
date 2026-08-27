"""SQLite-backed queue, history, caching, and deduplication tracking for DeviceRank AI Publisher."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from config.settings import settings


class StoryStatus:
    NEW = "NEW"
    SELECTED = "SELECTED"
    GENERATED = "GENERATED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"


class HistoryDB:
    """Manages story queue, deduplication, HTTP feed caching, and publishing history in SQLite."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _db_session(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager that opens and cleanly closes SQLite connections."""
        conn = sqlite3.connect(self.db_path, timeout=20.0)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            yield cursor
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        """Creates necessary tables if they do not exist."""
        with self._db_session() as cursor:
            # 1. Story Queue & State Machine Table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS story_queue (
                    url_hash TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    blogger_label TEXT,
                    title TEXT NOT NULL,
                    summary TEXT,
                    full_text TEXT,
                    image_url TEXT,
                    published_date TEXT,
                    raw_published_date TEXT,
                    tags TEXT,
                    score REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'NEW',
                    error_message TEXT,
                    blogger_post_id TEXT,
                    blogger_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 2. HTTP Feed Cache (Conditional Requests / ETag / Last-Modified)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS feed_cache (
                    feed_url TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    last_fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # 3. Processed Sources (Deduplication - backward compatible)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_sources (
                    url_hash TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'PROCESSED'
                )
                """
            )

            # 4. Published Posts (Publishing history & internal linking)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS published_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url TEXT,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    meta_description TEXT,
                    blogger_post_id TEXT,
                    blogger_url TEXT,
                    status TEXT DEFAULT 'DRAFT',
                    labels TEXT,
                    word_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    published_at TIMESTAMP
                )
                """
            )

            # Indexes for fast lookup
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_story_category_status ON story_queue (category, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_story_published_date ON story_queue (published_date)")

    @staticmethod
    def hash_url(url: str) -> str:
        """Computes SHA-256 hash of a normalized URL for fast lookup."""
        normalized = url.strip().rstrip("/").lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    # --- HTTP Feed Caching ---

    def get_feed_cache(self, feed_url: str) -> Dict[str, Optional[str]]:
        """Retrieves cached ETag and Last-Modified headers for a feed URL."""
        with self._db_session() as cursor:
            cursor.execute(
                "SELECT etag, last_modified FROM feed_cache WHERE feed_url = ?",
                (feed_url,),
            )
            row = cursor.fetchone()
            if row:
                return {"etag": row["etag"], "last_modified": row["last_modified"]}
            return {"etag": None, "last_modified": None}

    def set_feed_cache(self, feed_url: str, etag: Optional[str], last_modified: Optional[str]):
        """Saves ETag and Last-Modified headers for conditional GET requests."""
        now = datetime.now(timezone.utc).isoformat()
        with self._db_session() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO feed_cache (feed_url, etag, last_modified, last_fetched_at)
                VALUES (?, ?, ?, ?)
                """,
                (feed_url, etag, last_modified, now),
            )

    # --- Story Queue Operations ---

    def enqueue_story(
        self,
        source_url: str,
        source_name: str,
        category: str,
        blogger_label: str,
        title: str,
        summary: str = "",
        full_text: Optional[str] = None,
        image_url: Optional[str] = None,
        published_date: Optional[str] = None,
        raw_published_date: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """Enqueues a raw article if not already present or completed.

        Returns True if a new story was inserted into the queue.
        """
        url_hash = self.hash_url(source_url)
        tags_str = json.dumps(tags) if tags else "[]"
        now = datetime.now(timezone.utc).isoformat()

        with self._db_session() as cursor:
            cursor.execute(
                "SELECT status FROM story_queue WHERE url_hash = ?", (url_hash,)
            )
            existing = cursor.fetchone()

            if existing:
                # If existing is already published or generated or publishing, do not overwrite
                if existing["status"] in (StoryStatus.PUBLISHED, StoryStatus.PUBLISHING, StoryStatus.GENERATED):
                    return False
                # If in NEW status, update fields in case summary or full_text got enriched
                cursor.execute(
                    """
                    UPDATE story_queue
                    SET title = ?, summary = ?, full_text = COALESCE(?, full_text),
                        image_url = COALESCE(?, image_url), tags = ?, updated_at = ?
                    WHERE url_hash = ?
                    """,
                    (title, summary, full_text, image_url, tags_str, now, url_hash),
                )
                return False

            # Check if already processed in legacy table
            cursor.execute("SELECT 1 FROM processed_sources WHERE url_hash = ?", (url_hash,))
            if cursor.fetchone():
                return False

            # Insert as NEW
            cursor.execute(
                """
                INSERT INTO story_queue (
                    url_hash, source_url, source_name, category, blogger_label,
                    title, summary, full_text, image_url, published_date,
                    raw_published_date, tags, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url_hash,
                    source_url,
                    source_name,
                    category,
                    blogger_label,
                    title,
                    summary,
                    full_text,
                    image_url,
                    published_date,
                    raw_published_date,
                    tags_str,
                    StoryStatus.NEW,
                    now,
                    now,
                ),
            )
            return True

    def get_candidate_stories(
        self,
        category: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieves unhandled or candidate stories from the queue."""
        statuses = statuses or [StoryStatus.NEW]
        placeholders = ",".join("?" for _ in statuses)

        with self._db_session() as cursor:
            if category:
                query = f"""
                    SELECT * FROM story_queue 
                    WHERE category = ? AND status IN ({placeholders})
                    ORDER BY published_date DESC, created_at DESC
                    LIMIT ?
                """
                cursor.execute(query, (category, *statuses, limit))
            else:
                query = f"""
                    SELECT * FROM story_queue 
                    WHERE status IN ({placeholders})
                    ORDER BY published_date DESC, created_at DESC
                    LIMIT ?
                """
                cursor.execute(query, (*statuses, limit))

            return [dict(row) for row in cursor.fetchall()]

    def update_story_status(
        self,
        url_hash: str,
        status: str,
        score: Optional[float] = None,
        error_message: Optional[str] = None,
        blogger_post_id: Optional[str] = None,
        blogger_url: Optional[str] = None,
    ):
        """Updates a story's status and lifecycle attributes in the queue."""
        now = datetime.now(timezone.utc).isoformat()
        with self._db_session() as cursor:
            updates = ["status = ?", "updated_at = ?"]
            params = [status, now]

            if score is not None:
                updates.append("score = ?")
                params.append(score)

            if error_message is not None:
                updates.append("error_message = ?")
                params.append(error_message)

            if blogger_post_id is not None:
                updates.append("blogger_post_id = ?")
                params.append(blogger_post_id)

            if blogger_url is not None:
                updates.append("blogger_url = ?")
                params.append(blogger_url)

            params.append(url_hash)
            cursor.execute(
                f"UPDATE story_queue SET {', '.join(updates)} WHERE url_hash = ?",
                params,
            )

    def mark_story_selected(self, url_hash: str, score: float):
        """Transitions story status to SELECTED with its calculated ranking score."""
        self.update_story_status(url_hash, StoryStatus.SELECTED, score=score)

    def mark_story_publishing(self, url_hash: str):
        """Atomically sets story status to PUBLISHING for idempotency prior to network dispatch."""
        self.update_story_status(url_hash, StoryStatus.PUBLISHING)

    def mark_story_published(self, url_hash: str, blogger_post_id: str, blogger_url: str):
        """Transitions story status to PUBLISHED with its Blogger ID and URL."""
        self.update_story_status(
            url_hash,
            StoryStatus.PUBLISHED,
            blogger_post_id=blogger_post_id,
            blogger_url=blogger_url,
        )

    def mark_story_failed(self, url_hash: str, error_message: str):
        """Transitions story status to FAILED with error message."""
        self.update_story_status(url_hash, StoryStatus.FAILED, error_message=error_message)

    # --- Idempotency & Deduplication Checks ---

    def is_url_processed(self, url: str) -> bool:
        """Returns True if the source URL has already been processed or published."""
        url_hash = self.hash_url(url)
        with self._db_session() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM story_queue 
                WHERE url_hash = ? AND status IN (?, ?, ?)
                """,
                (url_hash, StoryStatus.PUBLISHED, StoryStatus.PUBLISHING, StoryStatus.GENERATED),
            )
            if cursor.fetchone():
                return True

            cursor.execute("SELECT 1 FROM processed_sources WHERE url_hash = ?", (url_hash,))
            return cursor.fetchone() is not None

    def is_url_published(self, url: str) -> bool:
        """Returns True if the source URL is recorded as published."""
        url_hash = self.hash_url(url)
        with self._db_session() as cursor:
            cursor.execute(
                "SELECT 1 FROM story_queue WHERE url_hash = ? AND status = ?",
                (url_hash, StoryStatus.PUBLISHED),
            )
            if cursor.fetchone():
                return True

            cursor.execute("SELECT 1 FROM published_posts WHERE source_url = ?", (url,))
            return cursor.fetchone() is not None

    def mark_source_processed(
        self, url: str, title: str, category: str, status: str = "PROCESSED"
    ):
        """Marks a source URL as processed to maintain backward compatibility."""
        url_hash = self.hash_url(url)
        with self._db_session() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO processed_sources (url_hash, source_url, title, category, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (url_hash, url, title, category, status),
            )

    # --- Publishing History & Statistics ---

    def record_published_post(
        self,
        category: str,
        title: str,
        source_url: Optional[str] = None,
        meta_description: Optional[str] = None,
        blogger_post_id: Optional[str] = None,
        blogger_url: Optional[str] = None,
        status: str = "DRAFT",
        labels: Optional[List[str]] = None,
        word_count: int = 0,
    ) -> int:
        """Records a newly generated/published post in the database."""
        labels_str = ",".join(labels) if labels else ""
        published_at = datetime.now(timezone.utc).isoformat() if status == "LIVE" else None

        with self._db_session() as cursor:
            cursor.execute(
                """
                INSERT INTO published_posts 
                (source_url, category, title, meta_description, blogger_post_id, blogger_url, status, labels, word_count, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_url,
                    category,
                    title,
                    meta_description,
                    blogger_post_id,
                    blogger_url,
                    status,
                    labels_str,
                    word_count,
                    published_at,
                ),
            )
            post_id = cursor.lastrowid

        if source_url:
            self.mark_source_processed(source_url, title, category, status="PUBLISHED")
            url_hash = self.hash_url(source_url)
            self.mark_story_published(
                url_hash=url_hash,
                blogger_post_id=blogger_post_id or "",
                blogger_url=blogger_url or "",
            )

        return post_id

    def get_stats(self) -> Dict[str, Any]:
        """Returns statistics on processed and published posts and queue states."""
        with self._db_session() as cursor:
            cursor.execute("SELECT COUNT(*) FROM processed_sources")
            total_sources = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM published_posts")
            total_posts = cursor.fetchone()[0]

            cursor.execute("SELECT status, COUNT(*) FROM published_posts GROUP BY status")
            status_breakdown = dict(cursor.fetchall())

            cursor.execute("SELECT category, COUNT(*) FROM published_posts GROUP BY category")
            category_breakdown = dict(cursor.fetchall())

            cursor.execute("SELECT status, COUNT(*) FROM story_queue GROUP BY status")
            queue_breakdown = dict(cursor.fetchall())

            cursor.execute("SELECT COUNT(*) FROM story_queue")
            total_in_queue = cursor.fetchone()[0]

            return {
                "total_sources_ingested": max(total_sources, total_in_queue),
                "total_posts_created": total_posts,
                "total_in_queue": total_in_queue,
                "queue_breakdown": queue_breakdown,
                "status_breakdown": status_breakdown,
                "category_breakdown": category_breakdown,
            }

    def get_recent_posts(self, limit: int = 10) -> List[Dict]:
        """Returns the most recent published posts."""
        with self._db_session() as cursor:
            cursor.execute(
                """
                SELECT id, category, title, status, blogger_url, created_at, word_count
                FROM published_posts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_published_articles_for_linking(
        self, category: Optional[str] = None, limit: int = 5
    ) -> List[Dict[str, str]]:
        """Returns past published articles with URLs for optional contextual internal linking."""
        with self._db_session() as cursor:
            if category:
                cursor.execute(
                    """
                    SELECT title, blogger_url, category
                    FROM published_posts
                    WHERE blogger_url IS NOT NULL AND blogger_url != '' AND category = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (category, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT title, blogger_url, category
                    FROM published_posts
                    WHERE blogger_url IS NOT NULL AND blogger_url != ''
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(row) for row in cursor.fetchall()]


# Global singleton instance
history_db = HistoryDB()
