"""Blogger API v3 client with publication idempotency and reconciliation for devicerank.blogspot.com."""

from typing import Dict, List, Optional
from googleapiclient.discovery import build
from config.settings import settings
from src.agents.seo_writer import GeneratedArticle
from src.db.history import history_db
from src.publishers.oauth_helper import get_blogger_credentials
from src.utils.logger import logger


class BloggerClient:
    """Client for publishing articles to Google Blogger via API v3 with idempotency guarantees."""

    def __init__(self, blog_id: Optional[str] = None):
        self.blog_id = blog_id or settings.blogger_blog_id
        if not self.blog_id:
            raise ValueError(
                "BLOGGER_BLOG_ID is not configured. Please add it to your .env file."
            )
        self.credentials = get_blogger_credentials()
        self.service = build("blogger", "v3", credentials=self.credentials)

    def get_blog_info(self) -> Dict:
        """Fetches blog metadata (name, URL, post count)."""
        try:
            return self.service.blogs().get(blogId=self.blog_id).execute()
        except Exception as e:
            logger.error(f"Failed to fetch blog info for blog ID {self.blog_id}: {e}")
            raise

    def list_recent_posts(self, max_results: int = 15, fetch_drafts: bool = True) -> List[Dict]:
        """Lists recent posts from the blog."""
        try:
            statuses = ["LIVE", "DRAFT"] if fetch_drafts else ["LIVE"]
            result = (
                self.service.posts()
                .list(
                    blogId=self.blog_id,
                    maxResults=max_results,
                    status=",".join(statuses),
                )
                .execute()
            )
            return result.get("items", [])
        except Exception as e:
            logger.error(f"Failed to list posts: {e}")
            return []

    def check_existing_post_by_title(self, title: str) -> Optional[Dict]:
        """Reconciles with Blogger to check if a post with the exact title already exists."""
        try:
            recent_posts = self.list_recent_posts(max_results=20)
            norm_title = title.strip().lower()
            for p in recent_posts:
                if p.get("title", "").strip().lower() == norm_title:
                    return p
        except Exception as e:
            logger.debug(f"Blogger reconciliation check failed: {e}")
        return None

    def publish_post(
        self,
        article: GeneratedArticle,
        is_draft: Optional[bool] = None,
    ) -> Dict:
        """Publishes a GeneratedArticle to Blogger with publication idempotency."""
        if is_draft is None:
            is_draft = settings.default_publish_status != "LIVE"

        status_str = "DRAFT" if is_draft else "LIVE"
        source_urls = list(dict.fromkeys(
            url for url in ([article.source_url] + article.source_urls) if url
        ))
        url_hashes = [history_db.hash_url(url) for url in source_urls]

        # 1. Idempotency Check: Local SQLite Database
        already_published = [url for url in source_urls if history_db.is_url_published(url)]
        if already_published:
            logger.warning(
                f"{len(already_published)} source URL(s) are already recorded as PUBLISHED. "
                "Skipping duplicate post."
            )
            return {"status": "SKIPPED_ALREADY_PUBLISHED", "title": article.title}

        # 2. Idempotency Check: Reconcile with Blogger API (in case of prior crash during recording)
        existing_blogger_post = self.check_existing_post_by_title(article.title)
        if existing_blogger_post:
            post_id = existing_blogger_post.get("id")
            post_url = existing_blogger_post.get("url", f"https://devicerank.blogspot.com/?post_id={post_id}")
            logger.warning(
                f"Post '{article.title}' already exists on Blogger (ID: {post_id}). Reconciling local state."
            )
            history_db.record_published_post(
                category=article.category or "general",
                title=article.title,
                source_url=article.source_url,
                meta_description=article.meta_description,
                blogger_post_id=post_id,
                blogger_url=post_url,
                status=status_str,
                labels=article.labels,
                word_count=article.word_count,
                source_urls=source_urls,
            )
            return existing_blogger_post

        # 3. Mark state as PUBLISHING before calling API
        for url_hash in url_hashes:
            history_db.mark_story_publishing(url_hash)

        logger.info(
            f"🚀 Publishing to Blogger ({status_str}): [bold]{article.title}[/bold] (Blog ID: {self.blog_id})"
        )

        body = {
            "kind": "blogger#post",
            "blog": {"id": self.blog_id},
            "title": article.title,
            "content": article.html_content,
            "labels": article.labels,
            "customMetaData": article.meta_description,
        }

        try:
            response = (
                self.service.posts()
                .insert(
                    blogId=self.blog_id,
                    isDraft=is_draft,
                    body=body,
                )
                .execute()
            )

            post_id = response.get("id")
            post_url = response.get("url", f"https://devicerank.blogspot.com/?post_id={post_id}")

            logger.info(f"✅ Successfully published! Post ID: {post_id} | URL: {post_url}")

            # Record in SQLite history database
            history_db.record_published_post(
                category=article.category or "general",
                title=article.title,
                source_url=article.source_url,
                meta_description=article.meta_description,
                blogger_post_id=post_id,
                blogger_url=post_url,
                status=status_str,
                labels=article.labels,
                word_count=article.word_count,
                source_urls=source_urls,
            )

            return response

        except Exception as e:
            logger.error(f"Error publishing post to Blogger: {e}")
            for url_hash in url_hashes:
                history_db.mark_story_failed(url_hash, error_message=str(e))
            raise
