"""Google Blogger API client for publication, draft promotion, and remote ledger reconciliation."""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Set

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from config.settings import settings
from src.agents.seo_writer import GeneratedArticle
from src.db.history import history_db
from src.utils.logger import logger

SCOPES = ["https://www.googleapis.com/auth/blogger"]


class BloggerClient:
    """Client for authenticating, publishing, and synchronizing posts with Google Blogger API."""

    def __init__(self):
        self.blog_id = settings.blogger_blog_id or "test-blog-id"
        self.credentials_path = settings.blogger_token_file
        self.service = self._authenticate()

    def _authenticate(self) -> Resource:
        """Authenticates using environment secrets or local token.json."""
        creds = None

        if settings.blogger_refresh_token and settings.blogger_client_id and settings.blogger_client_secret:
            creds = Credentials(
                token=None,
                refresh_token=settings.blogger_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.blogger_client_id,
                client_secret=settings.blogger_client_secret,
                scopes=SCOPES,
            )
            creds.refresh(Request())
        elif Path(self.credentials_path).exists():
            creds = Credentials.from_authorized_user_file(self.credentials_path, SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())

        if not creds or not creds.valid:
            raise ValueError(
                "Blogger API credentials invalid or missing. Run 'python -m src.main auth' or set environment secrets."
            )

        return build("blogger", "v3", credentials=creds, cache_discovery=False)

    def list_recent_posts(self, max_results: int = 10, fetch_drafts: bool = False) -> List[Dict]:
        """Lists recent posts from the blog.
        
        Blogger API rejects comma-separated status values (e.g. status='LIVE,DRAFT').
        When fetch_drafts is True, separate requests are made for 'LIVE' and 'DRAFT'
        and the resulting items are merged and deduplicated by post ID.
        """
        items_by_id: Dict[str, Dict] = {}
        statuses_to_query = ["LIVE"]
        if fetch_drafts:
            statuses_to_query.append("DRAFT")

        for st in statuses_to_query:
            try:
                result = (
                    self.service.posts()
                    .list(
                        blogId=self.blog_id,
                        maxResults=max_results,
                        status=st,
                    )
                    .execute()
                )
                for item in result.get("items", []):
                    pid = str(item.get("id", ""))
                    if pid and pid not in items_by_id:
                        if "status" not in item:
                            item["status"] = st
                        items_by_id[pid] = item
            except Exception as e:
                logger.error(f"Failed to query Blogger posts for status='{st}': {e}")
                raise

        return list(items_by_id.values())

    def publish_draft_post(self, post_id: str) -> Dict:
        """Publishes (promotes) an existing Blogger DRAFT post to LIVE status."""
        logger.info(f"Promoting Blogger draft post {post_id} to LIVE status...")
        try:
            result = (
                self.service.posts()
                .publish(
                    blogId=self.blog_id,
                    postId=post_id,
                )
                .execute()
            )
            logger.info(f"Successfully promoted draft {post_id} to LIVE. URL: {result.get('url')}")
            return result
        except Exception as e:
            logger.error(f"Failed to promote draft post {post_id} to LIVE: {e}")
            raise

    def check_existing_post_by_title(self, title: str) -> Optional[Dict]:
        """Reconciles with Blogger to check if a post with the exact title already exists."""
        try:
            recent_posts = self.list_recent_posts(max_results=20, fetch_drafts=True)
            norm_title = title.strip().lower()
            for p in recent_posts:
                if p.get("title", "").strip().lower() == norm_title:
                    return p
        except Exception as e:
            logger.debug(f"Blogger reconciliation check failed: {e}")
        return None

    def sync_remote_ledger(self, max_posts: int = 25) -> int:
        """Synchronizes remote Blogger posts into local SQLite history database to ensure authoritative state.
        
        Extracts embedded metadata comment `<!-- devicerank:meta: {...} -->` containing:
        - slot_id
        - source_urls
        - topic_fingerprints
        """
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
                content = post.get("content", "") or ""
                status = post.get("status", "LIVE")

                slot_id: Optional[str] = None
                source_urls: List[str] = []
                topic_fingerprints: List[str] = []
                recorded_category: Optional[str] = None

                # 1. Parse embedded machine-readable JSON metadata comment
                meta_match = re.search(r"<!--\s*devicerank:meta:\s*(\{.*?\})\s*-->", content, re.DOTALL)
                if meta_match:
                    try:
                        meta_obj = json.loads(meta_match.group(1))
                        slot_id = meta_obj.get("slot_id") or slot_id
                        source_urls = meta_obj.get("sources") or []
                        topic_fingerprints = meta_obj.get("fingerprints") or []
                        recorded_category = meta_obj.get("category") or None
                    except Exception as parse_err:
                        logger.debug(f"Could not parse embedded meta JSON for post {post_id}: {parse_err}")

                # 2. Fallback: customMetaData tag [slot_id:...]
                if not slot_id and "[slot_id:" in custom_meta:
                    match = re.search(r"\[slot_id:([a-zA-Z0-9\-_]+)\]", custom_meta)
                    if match:
                        slot_id = match.group(1)

                # 3. Fallback: Title & date parsing for legacy posts
                if not slot_id and "— DeviceRank" in title and published_str:
                    date_prefix = published_str[:10]  # YYYY-MM-DD
                    if "Morning Brief" in title:
                        slot_id = f"{date_prefix}-morning"
                    elif "Midday Brief" in title:
                        slot_id = f"{date_prefix}-midday"
                    elif "Evening Brief" in title or "Night Brief" in title:
                        slot_id = f"{date_prefix}-evening"

                # 4. Fallback: Extract schema source URLs from content
                if not source_urls and content:
                    schema_matches = re.findall(r'"url":\s*"(https?://[^"]+)"', content)
                    source_urls = [u for u in schema_matches if "devicerank.blogspot" not in u and "schema.org" not in u][:8]

                history_db.sync_remote_post(
                    blogger_post_id=post_id,
                    title=title,
                    category=recorded_category or (
                        "news_digest" if "Digest" in labels or "Brief" in title else "tech_news"
                    ),
                    slot_id=slot_id,
                    source_urls=source_urls,
                    blogger_url=post_url,
                    status=status,
                    meta_description=custom_meta,
                    labels=labels,
                    topic_fingerprints=topic_fingerprints,
                )
                synced_count += 1

            logger.info(f"Synchronized {synced_count} remote posts from Blogger ledger.")
            return synced_count
        except Exception as e:
            logger.error(f"Failed to sync remote Blogger ledger: {e}")
            raise

    def is_slot_published_remotely(self, slot_id: str, live_only: bool = False) -> bool:
        """Checks directly against Blogger API if a post matching the slot ID exists."""
        if not slot_id:
            return False

        if history_db.is_slot_published(slot_id, live_only=live_only):
            return True

        try:
            recent_posts = self.list_recent_posts(max_results=20, fetch_drafts=not live_only)
            for p in recent_posts:
                meta = p.get("customMetaData", "") or ""
                content = p.get("content", "") or ""
                status = p.get("status", "LIVE")

                if live_only and status != "LIVE":
                    continue

                if f"[slot_id:{slot_id}]" in meta or f'"slot_id": "{slot_id}"' in content or f'"slot_id":"{slot_id}"' in content:
                    return True

                # Title & date heuristic
                title = p.get("title", "")
                parts = slot_id.split("-")
                if len(parts) >= 4:
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
        topic_fingerprints: Optional[List[str]] = None,
    ) -> Dict:
        """Publishes a GeneratedArticle to Blogger with draft promotion and publication idempotency."""
        if is_draft is None:
            is_draft = settings.default_publish_status != "LIVE"

        status_str = "DRAFT" if is_draft else "LIVE"
        source_urls = list(dict.fromkeys(
            url for url in ([article.source_url] + article.source_urls) if url
        ))
        url_hashes = [history_db.hash_url(url) for url in source_urls]

        # 1. Slot Idempotency & Draft Promotion Check
        if slot_id:
            try:
                self.sync_remote_ledger(max_posts=25)
            except Exception as e:
                logger.warning(f"Pre-publish ledger sync warning: {e}")

            existing_slot_post = history_db.get_slot_post(slot_id)
            if existing_slot_post:
                existing_status = (existing_slot_post.get("status") or "").upper()
                existing_post_id = existing_slot_post.get("blogger_post_id")

                if existing_status == "LIVE":
                    logger.warning(f"Slot '{slot_id}' is already LIVE on Blogger (Post ID: {existing_post_id}). Skipping duplicate post.")
                    return {"status": "SKIPPED_ALREADY_PUBLISHED", "title": article.title, "slot_id": slot_id, "id": existing_post_id}

                if existing_status == "DRAFT" and not is_draft and existing_post_id:
                    # Scheduled LIVE run encountered an existing DRAFT for this slot -> Promote draft to LIVE!
                    logger.info(f"Promoting existing slot draft {existing_post_id} for '{slot_id}' to LIVE...")
                    promoted = self.publish_draft_post(existing_post_id)
                    history_db.sync_remote_post(
                        blogger_post_id=existing_post_id,
                        title=existing_slot_post.get("title", article.title),
                        slot_id=slot_id,
                        source_urls=source_urls,
                        blogger_url=promoted.get("url"),
                        status="LIVE",
                        topic_fingerprints=topic_fingerprints,
                    )
                    return promoted

                if existing_status == "DRAFT" and is_draft:
                    logger.warning(f"Draft for slot '{slot_id}' already exists (ID: {existing_post_id}). Skipping duplicate draft.")
                    return {"status": "SKIPPED_ALREADY_PUBLISHED", "title": article.title, "slot_id": slot_id, "id": existing_post_id}

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
                topic_fingerprints=topic_fingerprints,
            )
            return existing_blogger_post

        # 4. Mark state as PUBLISHING before calling API
        for url_hash in url_hashes:
            history_db.mark_story_publishing(url_hash)

        logger.info(
            f"🚀 Publishing to Blogger ({status_str}): [bold]{article.title}[/bold] (Blog ID: {self.blog_id})"
        )

        # 5. Embed structured machine-readable metadata comment at the end of HTML content
        meta_payload = {
            "slot_id": slot_id or article.slot_id or "",
            "category": article.category or "general",
            "sources": source_urls,
            "topics": getattr(article, "topic_phrases", []),
            "fingerprints": topic_fingerprints or [],
        }
        meta_comment = f"\n<!-- devicerank:meta: {json.dumps(meta_payload)} -->\n"
        final_html_content = (article.html_content or "") + meta_comment

        # Keep Blogger's search description clean. Slot/category bookkeeping lives in
        # the embedded metadata comment and must not consume description characters.
        custom_metadata = (article.meta_description or "").strip()[:160]

        body = {
            "kind": "blogger#post",
            "blog": {"id": self.blog_id},
            "title": article.title,
            "content": final_html_content,
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
                topic_fingerprints=topic_fingerprints,
            )

            return response

        except Exception as e:
            logger.error(f"Error publishing post to Blogger: {e}")
            for url_hash in url_hashes:
                history_db.mark_story_failed(url_hash, error_message=str(e))
            raise
