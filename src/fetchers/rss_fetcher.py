"""RSS Feed fetcher, conditional HTTP caching, date normalization, and concurrency manager."""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from config.settings import FeedsConfig, load_feeds_config, settings
from src.db.history import history_db
from src.fetchers.content_extractor import ContentExtractor
from src.utils.logger import logger
from src.utils.sanitizer import sanitize_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36 DeviceRankPublisher/1.0"
)


class RawArticle(BaseModel):
    """Normalized raw article fetched from an RSS feed."""

    title: str
    link: str
    source_name: str
    category: str
    blogger_label: str
    published_date: Optional[str] = None
    raw_published_date: Optional[str] = None
    summary: str = ""
    full_text: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    score: float = 0.0


def _get_http_session() -> requests.Session:
    """Creates a configured requests.Session with connection pooling and retries."""
    session = requests.Session()
    retries = Retry(
        total=settings.http_max_retries,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=15, pool_maxsize=25)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


_GLOBAL_FETCH_SESSION = _get_http_session()


def parse_published_date(entry: Any) -> Tuple_PubDate:
    """Extracts raw published date string and converts it to ISO 8601 UTC string."""
    def get_field(item, name):
        if isinstance(item, dict):
            return item.get(name)
        return getattr(item, name, None)

    raw_val = (
        get_field(entry, "published")
        or get_field(entry, "updated")
        or get_field(entry, "pubDate")
        or get_field(entry, "created")
    )
    if not raw_val:
        return None, None

    raw_str = str(raw_val).strip()

    # Try feedparser parsed tuple
    parsed_tuple = get_field(entry, "published_parsed") or get_field(entry, "updated_parsed")
    if parsed_tuple:
        try:
            dt = datetime(*parsed_tuple[:6], tzinfo=timezone.utc)
            return dt.isoformat(), raw_str
        except Exception:
            pass

    # Try RFC 2822
    try:
        dt = parsedate_to_datetime(raw_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat(), raw_str
    except Exception:
        pass

    # Try ISO 8601
    try:
        dt = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat(), raw_str
    except Exception:
        pass

    return None, raw_str


Tuple_PubDate = tuple[Optional[str], Optional[str]]


class RSSFetcher:
    """Fetches, parses, normalizes, and enqueues articles from configured RSS feeds."""

    def __init__(self, config: Optional[FeedsConfig] = None):
        self.config = config or load_feeds_config()

    def _extract_image_from_entry(self, entry: Any) -> Optional[str]:
        """Extracts media thumbnail or enclosure image from feed item with HTTPS enforcement."""
        def get_field(item, name):
            if isinstance(item, dict):
                return item.get(name)
            return getattr(item, name, None)

        # 1. media_content
        media_content = get_field(entry, "media_content")
        if media_content and isinstance(media_content, list):
            for item in media_content:
                url = get_field(item, "url")
                if url and any(str(url).lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".avif"]):
                    sanitized = sanitize_url(str(url), enforce_https=True)
                    if sanitized:
                        return sanitized
            first_url = get_field(media_content[0], "url")
            if first_url:
                sanitized = sanitize_url(str(first_url), enforce_https=True)
                if sanitized:
                    return sanitized

        # 2. media_thumbnail
        media_thumbnail = get_field(entry, "media_thumbnail")
        if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
            thumb_url = get_field(media_thumbnail[0], "url")
            if thumb_url:
                sanitized = sanitize_url(str(thumb_url), enforce_https=True)
                if sanitized:
                    return sanitized

        # 3. enclosures
        enclosures = get_field(entry, "enclosures")
        if enclosures and isinstance(enclosures, list):
            for enc in enclosures:
                enc_type = str(get_field(enc, "type") or "")
                enc_href = str(get_field(enc, "href") or "")
                if enc_type.startswith("image/") or any(
                    enc_href.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]
                ):
                    sanitized = sanitize_url(enc_href, enforce_https=True)
                    if sanitized:
                        return sanitized

        # 4. <img> tag in summary / content
        content_html = ""
        content = get_field(entry, "content")
        if content and isinstance(content, list) and len(content) > 0:
            content_html = get_field(content[0], "value") or ""
        elif get_field(entry, "summary"):
            content_html = get_field(entry, "summary") or ""

        if content_html:
            soup = BeautifulSoup(content_html, "html.parser")
            img_tag = soup.find("img")
            if img_tag and img_tag.get("src"):
                src = img_tag["src"]
                # Skip tiny tracking pixels
                if not any(px in src.lower() for px in ["pixel", "feedburner", "1x1", "badge"]):
                    sanitized = sanitize_url(src, enforce_https=True)
                    if sanitized:
                        return sanitized

        return None

    def _clean_summary(self, raw_summary: str) -> str:
        """Removes HTML tags from summary to return clean plain text."""
        if not raw_summary:
            return ""
        soup = BeautifulSoup(raw_summary, "html.parser")
        return soup.get_text(separator=" ", strip=True)

    def fetch_feed(
        self,
        feed_url: str,
        feed_name: str,
        category: str,
        blogger_label: str,
        max_items: int = 5,
        deduplicate: bool = True,
        enrich_content: bool = True,
        use_cache: bool = True,
    ) -> List[RawArticle]:
        """Fetches and parses a single RSS feed with conditional HTTP caching and queue ingestion."""
        logger.info(f"📡 Fetching feed: [bold]{feed_name}[/bold] ({feed_url})")
        articles: List[RawArticle] = []

        try:
            # Check conditional HTTP cache
            headers = {}
            if use_cache:
                cache = history_db.get_feed_cache(feed_url)
                if cache.get("etag"):
                    headers["If-None-Match"] = cache["etag"]
                if cache.get("last_modified"):
                    headers["If-Modified-Since"] = cache["last_modified"]

            # Perform HTTP request
            resp = _GLOBAL_FETCH_SESSION.get(
                feed_url,
                headers=headers,
                timeout=(5.0, float(settings.http_timeout_seconds)),
            )

            if resp.status_code == 304:
                logger.debug(f"Feed {feed_name} not modified (304).")
                return articles

            if resp.status_code != 200:
                logger.warning(f"Feed {feed_name} returned status {resp.status_code}.")
                return articles

            # Save new cache headers
            new_etag = resp.headers.get("ETag")
            new_last_modified = resp.headers.get("Last-Modified")
            history_db.set_feed_cache(feed_url, new_etag, new_last_modified)

            # Parse feed content
            parsed = feedparser.parse(resp.content)
            entries = getattr(parsed, "entries", [])
            if not entries and getattr(parsed, "bozo", 0):
                logger.warning(f"Empty feed or parse warning at {feed_url}: {getattr(parsed, 'bozo_exception', 'unknown')}")
                return articles

            for entry in entries[:max_items]:
                link = entry.get("link", "") if isinstance(entry, dict) else getattr(entry, "link", "")
                title = entry.get("title", "") if isinstance(entry, dict) else getattr(entry, "title", "")
                link = str(link).strip()
                title = str(title).strip()

                if not link or not title:
                    continue

                # Check deduplication
                if deduplicate and history_db.is_url_processed(link):
                    logger.debug(f"Skipping already processed URL: {link}")
                    continue

                # Extract summary
                raw_summary = entry.get("summary", "") if isinstance(entry, dict) else getattr(entry, "summary", "")
                content_field = entry.get("content") if isinstance(entry, dict) else getattr(entry, "content", None)
                if not raw_summary and content_field and isinstance(content_field, list) and len(content_field) > 0:
                    raw_summary = (
                        content_field[0].get("value", "")
                        if isinstance(content_field[0], dict)
                        else getattr(content_field[0], "value", "")
                    )

                summary = self._clean_summary(str(raw_summary))

                # Extract image
                image_url = self._extract_image_from_entry(entry)

                # Extract and normalize publication date
                published_date, raw_published_date = parse_published_date(entry)

                # Extract tags
                raw_tags = entry.get("tags", []) if isinstance(entry, dict) else getattr(entry, "tags", [])
                tags = []
                for t in raw_tags:
                    term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
                    if term:
                        tags.append(str(term))

                # Enrich with full content / OpenGraph if image is missing or summary is short
                full_text = None
                if enrich_content and (not image_url or len(summary) < 150):
                    extracted = ContentExtractor.extract(link)
                    if not image_url and extracted.get("og_image"):
                        image_url = extracted["og_image"]
                    if extracted.get("text"):
                        full_text = extracted["text"]

                # Automatically persist in queue
                history_db.enqueue_story(
                    source_url=link,
                    source_name=feed_name,
                    category=category,
                    blogger_label=blogger_label,
                    title=title,
                    summary=summary,
                    full_text=full_text,
                    image_url=image_url,
                    published_date=published_date,
                    raw_published_date=raw_published_date,
                    tags=tags,
                )

                article = RawArticle(
                    title=title,
                    link=link,
                    source_name=feed_name,
                    category=category,
                    blogger_label=blogger_label,
                    published_date=published_date,
                    raw_published_date=raw_published_date,
                    summary=summary,
                    full_text=full_text,
                    image_url=image_url,
                    tags=tags,
                )
                articles.append(article)

        except Exception as e:
            logger.error(f"Error fetching feed {feed_name}: {e}")

        return articles

    def fetch_category(
        self,
        category_key: str,
        max_items: int = 5,
        deduplicate: bool = True,
        parallel: bool = True,
    ) -> List[RawArticle]:
        """Fetches all enabled feeds for a category, optionally using bounded concurrency."""
        cat_config = self.config.categories.get(category_key)
        if not cat_config:
            raise ValueError(f"Category '{category_key}' not found in configuration.")

        enabled_feeds = [f for f in cat_config.feeds if f.enabled]
        if not enabled_feeds:
            return []

        category_articles: List[RawArticle] = []

        if parallel and len(enabled_feeds) > 1:
            max_workers = min(len(enabled_feeds), settings.max_concurrent_fetches)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_feed = {
                    executor.submit(
                        self.fetch_feed,
                        feed.url,
                        feed.name,
                        category_key,
                        cat_config.blogger_label,
                        max_items,
                        deduplicate,
                    ): feed
                    for feed in enabled_feeds
                }
                for future in as_completed(future_to_feed):
                    try:
                        arts = future.result()
                        category_articles.extend(arts)
                    except Exception as e:
                        logger.error(f"Concurrent fetch failed for feed: {e}")
        else:
            for feed in enabled_feeds:
                feed_articles = self.fetch_feed(
                    feed_url=feed.url,
                    feed_name=feed.name,
                    category=category_key,
                    blogger_label=cat_config.blogger_label,
                    max_items=max_items,
                    deduplicate=deduplicate,
                )
                category_articles.extend(feed_articles)

        return category_articles

    def fetch_all(
        self,
        max_per_category: int = 3,
        deduplicate: bool = True,
    ) -> Dict[str, List[RawArticle]]:
        """Fetches articles across all configured categories."""
        all_results: Dict[str, List[RawArticle]] = {}
        for cat_key in self.config.categories.keys():
            articles = self.fetch_category(
                category_key=cat_key,
                max_items=max_per_category,
                deduplicate=deduplicate,
            )
            all_results[cat_key] = articles
        return all_results
