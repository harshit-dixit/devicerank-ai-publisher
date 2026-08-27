"""SQLite-backed history and deduplication tracking for articles and published posts."""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional
from config.settings import settings


class HistoryDB:
    """Manages deduplication and post history in SQLite."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _db_session(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager that opens and cleanly closes SQLite connections."""
        conn = sqlite3.connect(self.db_path)
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
            # Table 1: Processed Sources (Deduplication)
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

            # Table 2: Published Posts
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

    @staticmethod
    def hash_url(url: str) -> str:
        """Computes SHA-256 hash of a normalized URL for fast lookup."""
        normalized = url.strip().rstrip("/").lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def is_url_processed(self, url: str) -> bool:
        """Returns True if the source URL has already been processed or published."""
        url_hash = self.hash_url(url)
        with self._db_session() as cursor:
            cursor.execute("SELECT 1 FROM processed_sources WHERE url_hash = ?", (url_hash,))
            return cursor.fetchone() is not None

    def mark_source_processed(self, url: str, title: str, category: str, status: str = "PROCESSED"):
        """Marks a source URL as processed to prevent re-processing in future runs."""
        url_hash = self.hash_url(url)
        with self._db_session() as cursor:
            cursor.execute(
                """
                INSERT OR REPLACE INTO processed_sources (url_hash, source_url, title, category, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (url_hash, url, title, category, status),
            )

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
        published_at = datetime.utcnow() if status == "LIVE" else None

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

        return post_id

    def get_stats(self) -> Dict[str, any]:
        """Returns statistics on processed and published posts."""
        with self._db_session() as cursor:
            cursor.execute("SELECT COUNT(*) FROM processed_sources")
            total_sources = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM published_posts")
            total_posts = cursor.fetchone()[0]

            cursor.execute("SELECT status, COUNT(*) FROM published_posts GROUP BY status")
            status_breakdown = dict(cursor.fetchall())

            cursor.execute("SELECT category, COUNT(*) FROM published_posts GROUP BY category")
            category_breakdown = dict(cursor.fetchall())

            return {
                "total_sources_ingested": total_sources,
                "total_posts_created": total_posts,
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


# Singleton instance
history_db = HistoryDB()
