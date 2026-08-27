"""RSS Feed fetcher and deduplication manager for multiple niche categories."""

import re
from typing import Any, Dict, List, Optional
import feedparser
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from config.settings import FeedsConfig, load_feeds_config
from src.db.history import history_db
from src.fetchers.content_extractor import ContentExtractor
from src.utils.logger import logger


class RawArticle(BaseModel):
    """Normalized raw article fetched from an RSS feed."""

    title: str
    link: str
    source_name: str
    category: str
    blogger_label: str
    published_date: Optional[str] = None
    summary: str = ""
    full_text: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class RSSFetcher:
    """Fetches, parses, and normalizes articles from configured RSS feeds."""

    def __init__(self, config: Optional[FeedsConfig] = None):
        self.config = config or load_feeds_config()

    def _extract_image_from_entry(self, entry: Any) -> Optional[str]:
        """Extracts media thumbnail or enclosure image from feed item."""
        # Helper to get field from dict or object
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
                    return str(url)
            first_url = get_field(media_content[0], "url")
            if first_url:
                return str(first_url)

        # 2. media_thumbnail
        media_thumbnail = get_field(entry, "media_thumbnail")
        if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
            thumb_url = get_field(media_thumbnail[0], "url")
            if thumb_url:
                return str(thumb_url)

        # 3. enclosures
        enclosures = get_field(entry, "enclosures")
        if enclosures and isinstance(enclosures, list):
            for enc in enclosures:
                enc_type = str(get_field(enc, "type") or "")
                enc_href = str(get_field(enc, "href") or "")
                if enc_type.startswith("image/") or any(
                    enc_href.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]
                ):
                    return enc_href

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
                    return src

        return None

    def _clean_summary(self, raw_summary: str) -> str:
        """Removes HTML tags from summary to return clean text."""
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
    ) -> List[RawArticle]:
        """Fetches and parses a single RSS feed."""
        logger.info(f"📡 Fetching feed: [bold]{feed_name}[/bold] ({feed_url})")
        articles: List[RawArticle] = []

        try:
            parsed = feedparser.parse(feed_url)
            entries = getattr(parsed, "entries", [])
            if not entries and getattr(parsed, "bozo", 0):
                logger.warning(f"Failed or empty feed at {feed_url}: {getattr(parsed, 'bozo_exception', 'unknown')}")
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
                    raw_summary = content_field[0].get("value", "") if isinstance(content_field[0], dict) else getattr(content_field[0], "value", "")

                summary = self._clean_summary(str(raw_summary))

                # Extract image
                image_url = self._extract_image_from_entry(entry)

                # Extract published date
                published_date = None
                if isinstance(entry, dict):
                    published_date = entry.get("published", entry.get("updated", None))
                else:
                    published_date = getattr(entry, "published", getattr(entry, "updated", None))

                # Extract tags
                raw_tags = entry.get("tags", []) if isinstance(entry, dict) else getattr(entry, "tags", [])
                tags = []
                for t in raw_tags:
                    term = t.get("term") if isinstance(t, dict) else getattr(t, "term", None)
                    if term:
                        tags.append(str(term))

                # Enrich with full content / OpenGraph if image is missing or summary is very short
                full_text = None
                if enrich_content and (not image_url or len(summary) < 150):
                    extracted = ContentExtractor.extract(link)
                    if not image_url and extracted.get("og_image"):
                        image_url = extracted["og_image"]
                    if extracted.get("text"):
                        full_text = extracted["text"]

                article = RawArticle(
                    title=title,
                    link=link,
                    source_name=feed_name,
                    category=category,
                    blogger_label=blogger_label,
                    published_date=str(published_date) if published_date else None,
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
    ) -> List[RawArticle]:
        """Fetches all enabled feeds for a specific category key."""
        cat_config = self.config.categories.get(category_key)
        if not cat_config:
            raise ValueError(f"Category '{category_key}' not found in configuration.")

        category_articles: List[RawArticle] = []
        for feed in cat_config.feeds:
            if not feed.enabled:
                continue
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
        max_per_category: int = 2,
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
