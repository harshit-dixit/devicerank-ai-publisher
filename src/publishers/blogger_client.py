"""Blogger API v3 client for posting articles to devicerank.blogspot.com."""

from typing import Dict, List, Optional
from googleapiclient.discovery import build
from config.settings import settings
from src.agents.seo_writer import GeneratedArticle
from src.db.history import history_db
from src.publishers.oauth_helper import get_blogger_credentials
from src.utils.logger import logger


class BloggerClient:
    """Client for publishing articles to Google Blogger via API v3."""

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

    def publish_post(
        self,
        article: GeneratedArticle,
        is_draft: Optional[bool] = None,
    ) -> Dict:
        """
        Publishes a GeneratedArticle to Blogger.
        - is_draft: If True, saved as Draft; if False, published Live.
        """
        if is_draft is None:
            is_draft = settings.default_publish_status != "LIVE"

        status_str = "DRAFT" if is_draft else "LIVE"
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
            )

            return response

        except Exception as e:
            logger.error(f"Error publishing post to Blogger: {e}")
            raise

    def list_recent_posts(self, max_results: int = 10, fetch_drafts: bool = True) -> List[Dict]:
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
