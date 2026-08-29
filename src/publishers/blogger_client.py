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

    def sync_remote_ledger(self, max_posts: int = 25) -> int:
        """Synchronizes remote Blogger posts into local SQLite history database to ensure unified state."""
        try:
            recent_posts = self.list_recent_posts(max_results=max_posts, fetch_drafts=True)
            synced_count = 0
            for post in recent_posts:
                post_id = str(post.get("id", ""))
                title = post.get("title", "")
                post_url = post.get("url", "")
                custom_meta = post.get("customMetaData", "") or ""
                labels = post.get("labels", []) or []
                published_str = post.get("published", "") or ""

                # Extract slot_id from customMetaData: [slot_id:YYYY-MM-DD-slot]
                slot_id = None
                if "[slot_id:" in custom_meta:
                    import re
                    match = re.search(r"\[slot_id:([a-zA-Z0-9\-_]+)\]", custom_meta)
                    if match:
                        slot_id = match.group(1)

                # Fallback extraction from title and published date if slot_id not in meta
                if not slot_id and "— DeviceRank" in title and published_str:
                    date_prefix = published_str[:10]  # YYYY-MM-DD
                    if "Morning Brief" in title:
                        slot_id = f"{date_prefix}-morning"
                    elif "Midday Brief" in title:
                        slot_id = f"{date_prefix}-midday"
                    elif "Evening Brief" in title or "Night Brief" in title:
                        slot_id = f"{date_prefix}-evening"

                history_db.sync_remote_post(
                    blogger_post_id=post_id,
                    title=title,
                    category="news_digest" if "Digest" in labels or "Brief" in title else "tech_news",
                    slot_id=slot_id,
                    blogger_url=post_url,
                    status=post.get("status", "LIVE"),
                    meta_description=custom_meta,
                    labels=labels,
                )
                synced_count += 1

            logger.info(f"Synchronized {synced_count} remote posts from Blogger ledger.")
            return synced_count
        except Exception as e:
            logger.debug(f"Failed to sync remote Blogger ledger: {e}")
            return 0

    def is_slot_published_remotely(self, slot_id: str) -> bool:
        """Checks directly against Blogger API if a post matching the slot ID exists."""
        if not slot_id:
            return False

        # First check local database (which may already be synced)
        if history_db.is_slot_published(slot_id):
            return True

        try:
            recent_posts = self.list_recent_posts(max_results=20)
            for p in recent_posts:
                meta = p.get("customMetaData", "") or ""
                if f"[slot_id:{slot_id}]" in meta:
                    return True
                # Also check title match
                title = p.get("title", "")
                parts = slot_id.split("-")
                if len(parts) >= 4:  # YYYY-MM-DD-slot
                    slot_type = parts[3].lower()
                    date_part = "-".join(parts[:3])
                    pub_date = (p.get("published") or "")[:10]
                    if pub_date == date_part:
                        if slot_type == "morning" and "Morning Brief" in title:
                            return True
                        if slot_type == "midday" and "Midday Brief" in title:
                            return True
                        if slot_type == "evening" and ("Evening Brief" in title or "Night Brief" in title):
                            return True
        except Exception as e:
            logger.debug(f"Error checking remote slot publication: {e}")

        return False

    def publish_post(
        self,
        article: GeneratedArticle,
        is_draft: Optional[bool] = None,
        slot_id: Optional[str] = None,
    ) -> Dict:
        """Publishes a GeneratedArticle to Blogger with publication idempotency."""
        if is_draft is None:
            is_draft = settings.default_publish_status != "LIVE"

        status_str = "DRAFT" if is_draft else "LIVE"
        source_urls = list(dict.fromkeys(
            url for url in ([article.source_url] + article.source_urls) if url
        ))
        url_hashes = [history_db.hash_url(url) for url in source_urls]

        # 1. Slot Idempotency Check: Local and Remote
        if slot_id and (history_db.is_slot_published(slot_id) or self.is_slot_published_remotely(slot_id)):
            logger.warning(f"Slot '{slot_id}' is already published. Skipping duplicate post.")
            return {"status": "SKIPPED_ALREADY_PUBLISHED", "title": article.title, "slot_id": slot_id}

        # 2. Source Idempotency Check: Local SQLite Database
        already_published = [url for url in source_urls if history_db.is_url_published(url)]
        if already_published and len(already_published) == len(source_urls):
            logger.warning(
                f"All {len(already_published)} source URL(s) are already recorded as PUBLISHED. "
                "Skipping duplicate post."
            )
            return {"status": "SKIPPED_ALREADY_PUBLISHED", "title": article.title}

        # 3. Title Idempotency Check: Reconcile with Blogger API
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
                slot_id=slot_id,
            )
            return existing_blogger_post

        # 4. Mark state as PUBLISHING before calling API
        for url_hash in url_hashes:
            history_db.mark_story_publishing(url_hash)

        logger.info(
            f"🚀 Publishing to Blogger ({status_str}): [bold]{article.title}[/bold] (Blog ID: {self.blog_id})"
        )

        # Encode slot_id into customMetaData for unambiguous remote reconciliation
        custom_metadata = article.meta_description or ""
        if slot_id:
            custom_metadata = f"[slot_id:{slot_id}] {custom_metadata}".strip()[:200]

        body = {
            "kind": "blogger#post",
            "blog": {"id": self.blog_id},
            "title": article.title,
            "content": article.html_content,
            "labels": article.labels,
            "customMetaData": custom_metadata,
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
                slot_id=slot_id,
            )

            return response

        except Exception as e:
            logger.error(f"Error publishing post to Blogger: {e}")
            for url_hash in url_hashes:
                history_db.mark_story_failed(url_hash, error_message=str(e))
            raise
