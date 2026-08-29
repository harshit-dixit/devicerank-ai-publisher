"""Gemini 2.5 Flash SEO writer with slot-specific schemas, mandatory originality, and HTML assembly."""

import html as html_lib
import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Type

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from config.settings import settings
from src.agents.prompts import (
    ARTICLE_GENERATION_PROMPT,
    BLOGGER_HTML_TEMPLATE,
    DIGEST_BLOGGER_HTML_TEMPLATE,
    EVERGREEN_ARTICLE_PROMPT,
    EVERGREEN_BLOGGER_HTML_TEMPLATE,
    EVERGREEN_SYSTEM_PROMPT,
    EVENING_DIGEST_PROMPT,
    MIDDAY_DIGEST_PROMPT,
    MORNING_DIGEST_PROMPT,
    SEO_SYSTEM_PROMPT,
)
from src.fetchers.clustering import StoryCluster
from src.fetchers.rss_fetcher import RawArticle
from src.evergreen import SelectedEvergreenTopic, is_devicerank_url
from src.google_sources import GoogleEvidence, is_official_google_url
from src.image_sources import ArticleImage, CATEGORY_IMAGE_QUERIES, UnsplashImageFetcher
from src.utils.image_validator import validate_image_url
from src.utils.logger import logger
from src.utils.sanitizer import (
    clean_html_fragment,
    generate_json_ld_schema,
    remove_all_anchor_tags,
    sanitize_title,
    sanitize_url,
    strip_html,
)
from src.utils.slots import SlotInfo, SlotType, build_deterministic_title, get_current_slot, get_standardized_labels


# ---------------------------------------------------------------------------
# Pydantic Schemas for Structured Gemini Outputs
# ---------------------------------------------------------------------------

class FAQItem(BaseModel):
    question: str = Field(description="Frequently asked question relevant to search queries")
    answer: str = Field(description="Direct, informative answer resolving the question (50-90 words)")


class SEOArticleOutput(BaseModel):
    title: str = Field(description="Search-optimized title (50-65 chars)")
    meta_description: str = Field(description="High-CTR meta description (140-155 chars)")
    focus_keyword: str = Field(description="Primary high-intent keyword")
    secondary_keywords: List[str] = Field(description="2-4 related semantic keywords")
    key_takeaways: List[str] = Field(description="Exactly 3 high-impact summary bullets")
    html_content: str = Field(description="Clean, valid semantic HTML body")
    labels: List[str] = Field(description="2-4 relevant taxonomy labels")
    faq_items: List[FAQItem] = Field(description="1-3 relevant FAQ items")
    word_count: int = Field(description="Estimated body word count")


class MorningStoryOutput(BaseModel):
    summary: str = Field(description="Punchy 70-110 word summary explaining what happened overnight")
    why_it_matters: str = Field(description="Practical impact on consumers, engineers, or the market")
    key_metric_delta: str = Field(description="Concrete verified metric, pricing, or spec delta (e.g. '$200 price drop', '15% IPC gain', '5,000mAh vs 4,000mAh')")


class MorningDigestOutput(BaseModel):
    topic_phrases: List[str] = Field(description="Exactly 3 punchy entity/topic phrases (e.g. 'Pixel 11', 'DLSS 5', 'iOS 27')")
    meta_description: str = Field(description="High-CTR meta description summarizing the morning developments (140-155 chars)")
    focus_keyword: str = Field(description="Primary search keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="2-3 secondary search keywords")
    key_takeaways: List[str] = Field(description="Exactly 3 bulleted overnight highlights")
    stories: List[MorningStoryOutput] = Field(description="One entry for each supplied source cluster in exact order")


class DigestStoryOutput(BaseModel):
    summary: str = Field(description="Crisp 80-120 word factual summary")
    why_it_matters: str = Field(description="Practical impact and consumer implications")


class MiddayLeadStoryOutput(BaseModel):
    headline: str = Field(description="Analytical headline for the multi-source lead story")
    summary: str = Field(description="In-depth 250-350 word multi-source synthesis")
    core_conflict_and_engineering: str = Field(description="Technical deep-dive into architectural tradeoffs or competing claims")
    market_implications: str = Field(description="Ecosystem impact, pricing ripple effects, or industry shifts")


class MiddayDigestOutput(BaseModel):
    topic_phrases: List[str] = Field(description="Exactly 3 punchy entity/topic phrases")
    meta_description: str = Field(description="High-CTR meta description (140-155 chars)")
    focus_keyword: str = Field(description="Primary search keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="2-3 secondary search keywords")
    key_takeaways: List[str] = Field(description="Exactly 3 bulleted executive takeaways")
    lead_story: MiddayLeadStoryOutput = Field(description="Deep synthesis of Cluster #1")
    supporting_stories: List[DigestStoryOutput] = Field(description="Entries for remaining supporting clusters")
    comparison_table_html: str = Field(description="Mandatory clean HTML <table> comparing specs, prices, or architectures")


class EveningStoryOutput(BaseModel):
    summary: str = Field(description="Clear 90-130 word summary")
    buyer_privacy_implications: str = Field(description="Transparent evaluation of upgrade value, repairability, and telemetry/privacy")


class DeviceRankScorecardItem(BaseModel):
    device_name: str = Field(description="Name of product or device evaluated")
    value_score: str = Field(description="Transparent rating or price-to-performance evaluation (e.g. '8.5 / 10' or '$599 vs $799 predecessor')")
    longevity_score: str = Field(description="Software update commitment or hardware durability (e.g. '7 Years OS updates')")
    privacy_score: str = Field(description="On-device vs cloud AI telemetry evaluation (e.g. 'Local NPU / Zero training telemetry')")
    repairability_score: str = Field(description="Modular parts and ease of repair rating (e.g. '7 / 10 Modular battery')")
    buying_verdict: str = Field(description="Direct, evidence-backed recommendation (e.g. 'Essential upgrade for S22 users; skip if owning S24')")


class EveningDigestOutput(BaseModel):
    topic_phrases: List[str] = Field(description="Exactly 3 punchy entity/topic phrases")
    meta_description: str = Field(description="High-CTR meta description (140-155 chars)")
    focus_keyword: str = Field(description="Primary search keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="2-3 secondary search keywords")
    key_takeaways: List[str] = Field(description="Exactly 3 bulleted buyer insights")
    stories: List[EveningStoryOutput] = Field(description="One entry for each supplied source cluster in exact order")
    scorecards: List[DeviceRankScorecardItem] = Field(description="1 to 3 mandatory DeviceRank Upgrade Scorecards")


# ---------------------------------------------------------------------------
# Generated Article Output Dataclass
# ---------------------------------------------------------------------------

@dataclass
class GeneratedArticle:
    """Final, verified article ready for Blogger publishing."""

    title: str
    meta_description: str
    html_content: str
    labels: List[str]
    word_count: int
    focus_keyword: str
    secondary_keywords: List[str]
    key_takeaways: List[str]
    faq_items: List[FAQItem]
    source_url: str
    source_urls: List[str]
    source_name: str
    source_names: List[str]
    category: str
    featured_image: Optional[str] = None
    image_count: int = 0
    slot_id: Optional[str] = None
    topic_phrases: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SEOWriter Engine
# ---------------------------------------------------------------------------

class SEOWriter:
    """Production SEO content generation engine powered by Gemini 2.5 Flash."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        image_fetcher: Optional[UnsplashImageFetcher] = None,
    ):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model_name or settings.gemini_model
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured.")
        self.client = genai.Client(api_key=self.api_key)
        self.image_fetcher = image_fetcher
        if self.image_fetcher is None and settings.unsplash_access_key:
            self.image_fetcher = UnsplashImageFetcher(
                settings.unsplash_access_key,
                timeout_seconds=settings.http_timeout_seconds,
            )

    def _call_gemini_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel],
        max_retries: int = 4,
        system_prompt: str = SEO_SYSTEM_PROMPT,
    ) -> BaseModel:
        """Executes a structured schema request to Gemini with exponential backoff."""
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        temperature=0.35,
                    ),
                )
                if not response.text:
                    raise ValueError("Empty response text from Gemini API")
                return response_schema.model_validate_json(response.text)

            except Exception as e:
                delay = (2 ** attempt) + random.uniform(0.5, 1.5)
                logger.warning(
                    f"Gemini API attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {delay:.2f}s..."
                )
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)

        raise RuntimeError("Failed to generate content after maximum retries")

    def write_evergreen(
        self,
        selected: SelectedEvergreenTopic,
        internal_links: Optional[List[Dict[str, str]]] = None,
        google_sources: Optional[List[GoogleEvidence]] = None,
        required_image_count: int = 0,
    ) -> GeneratedArticle:
        """Generate one approved, long-lived tutorial with strict quality gates."""
        topic = selected.topic
        links = internal_links or []
        citations = google_sources or []
        images: List[ArticleImage] = []
        if self.image_fetcher:
            images = self.image_fetcher.search(
                topic.primary_keyword,
                count=settings.evergreen_image_count,
                fallback_query=CATEGORY_IMAGE_QUERIES.get(selected.category_key),
            )
        if len(images) < required_image_count:
            raise RuntimeError(
                "Evergreen publishing requires "
                f"{required_image_count} images, but only {len(images)} usable images were found. "
                "Check UNSPLASH_ACCESS_KEY and the Unsplash API response."
            )
        sections = "\n".join(f"- {section}" for section in topic.sections)
        internal_link_lines = []
        for index, link in enumerate(links, 1):
            safe_title = html_lib.escape(strip_html(str(link.get("title") or "")))
            internal_link_lines.append(
                f"- [[INTERNAL_LINK_{index}]]: untrusted display title '{safe_title}'"
            )
        internal_links_context = (
            "\n".join(internal_link_lines)
            if internal_link_lines
            else "No internal links are available. Do not create an internal-link token."
        )
        google_source_blocks = []
        for index, source in enumerate(citations, 1):
            safe_title = html_lib.escape(source.title)
            safe_url = html_lib.escape(source.url, quote=True)
            safe_excerpt = html_lib.escape(source.excerpt)
            google_source_blocks.append(
                f'<source token="[[GOOGLE_CITATION_{index}]]" title="{safe_title}" '
                f'url="{safe_url}">\n{safe_excerpt}\n</source>'
            )
        google_sources_context = (
            "\n".join(google_source_blocks)
            if google_source_blocks
            else "No official Google evidence was fetched. Do not create a Google citation token."
        )

        prompt = EVERGREEN_ARTICLE_PROMPT.format(
            category_name=selected.category_name,
            category_description=selected.category_description,
            title=topic.title,
            primary_keyword=topic.primary_keyword,
            search_intent=topic.search_intent,
            reader_problem=topic.reader_problem,
            outcome=topic.outcome,
            sections=sections,
            internal_links=internal_links_context,
            google_sources=google_sources_context,
        )
        output: Optional[SEOArticleOutput] = None
        quality_prompt = prompt
        for quality_attempt in range(settings.evergreen_quality_attempts):
            candidate: SEOArticleOutput = self._call_gemini_structured(
                quality_prompt,
                SEOArticleOutput,
                max_retries=2,
                system_prompt=EVERGREEN_SYSTEM_PROMPT,
            )
            candidate.meta_description = self._normalize_meta_description(
                candidate.meta_description
            )
            try:
                self._validate_evergreen_output(
                    candidate,
                    expected_title=topic.title,
                    google_citation_count=len(citations),
                )
                output = candidate
                break
            except ValueError as exc:
                if quality_attempt == settings.evergreen_quality_attempts - 1:
                    raise
                logger.warning(
                    "Evergreen quality attempt %s/%s failed: %s",
                    quality_attempt + 1,
                    settings.evergreen_quality_attempts,
                    exc,
                )
                quality_prompt = (
                    prompt
                    + "\n\n<quality_feedback>\nThe previous draft was rejected: "
                    + html_lib.escape(str(exc))
                    + "\nRewrite the complete article and satisfy every contract item."
                    + "\n</quality_feedback>"
                )

        if output is None:
            raise RuntimeError("Evergreen generation ended without a valid article")

        html_content = self._assemble_evergreen_html(
            selected=selected,
            output=output,
            internal_links=links,
            google_sources=citations,
            images=images,
        )
        return GeneratedArticle(
            title=topic.title,
            meta_description=output.meta_description.strip(),
            html_content=html_content,
            labels=[selected.blogger_label, "How To Guides", "Evergreen"],
            word_count=len(strip_html(html_content).split()),
            focus_keyword=topic.primary_keyword,
            secondary_keywords=output.secondary_keywords[:4],
            key_takeaways=output.key_takeaways,
            faq_items=output.faq_items,
            source_url=topic.source_id,
            source_urls=[topic.source_id, *[source.url for source in citations]],
            source_name="DeviceRank Evergreen Topic Library",
            source_names=[
                "DeviceRank Evergreen Topic Library",
                *[source.title for source in citations],
            ],
            category=selected.category_key,
            featured_image=images[0].url if images else None,
            image_count=len(images),
            topic_phrases=[topic.primary_keyword],
        )

    def _validate_evergreen_output(
        self,
        output: SEOArticleOutput,
        expected_title: str,
        google_citation_count: int = 0,
    ) -> None:
        """Reject thin or off-contract tutorials before they can reach Blogger."""
        failures = []
        if sanitize_title(output.title) != expected_title:
            failures.append("the model changed the approved title")
        meta_length = len(output.meta_description.strip())
        if not 140 <= meta_length <= 155:
            failures.append(f"meta description is {meta_length} characters (expected 140-155)")
        if len(output.key_takeaways) != 3:
            failures.append("exactly 3 key takeaways are required")
        if not 3 <= len(output.faq_items) <= 5:
            failures.append("3-5 FAQ items are required")

        clean_body = clean_html_fragment(output.html_content)
        body_text = strip_html(clean_body)
        body_word_count = len(body_text.split())
        if body_word_count < settings.evergreen_min_word_count:
            failures.append(
                f"body has {body_word_count} words (minimum {settings.evergreen_min_word_count})"
            )
        if clean_body.lower().count("<h2") < 5:
            failures.append("at least 5 useful H2 sections are required")
        body_lower = body_text.lower()
        if "common mistake" not in body_lower:
            failures.append("a Common mistakes section is required")
        if "verify" not in body_lower and "check your result" not in body_lower:
            failures.append("a result-verification section is required")
        if google_citation_count:
            cited_indexes = {
                int(index)
                for index in re.findall(r"\[\[GOOGLE_CITATION_(\d+)\]\]", clean_body)
            }
            if not any(1 <= index <= google_citation_count for index in cited_indexes):
                failures.append("at least one valid supplied Google citation token is required")

        if failures:
            raise ValueError("Evergreen article failed quality gates: " + "; ".join(failures))

    @staticmethod
    def _normalize_meta_description(description: str) -> str:
        """Normalize whitespace and safely shorten overlong SEO descriptions."""
        normalized = re.sub(r"\s+", " ", strip_html(description)).strip()
        if len(normalized) <= 155:
            return normalized
        shortened = normalized[:156].rsplit(" ", 1)[0].rstrip(" ,;:-")
        if len(shortened) >= 140 and shortened[-1:] not in ".!?":
            shortened = shortened[:154].rstrip(" ,;:-") + "."
        return shortened

    def _assemble_evergreen_html(
        self,
        selected: SelectedEvergreenTopic,
        output: SEOArticleOutput,
        internal_links: List[Dict[str, str]],
        google_sources: List[GoogleEvidence],
        images: Optional[List[ArticleImage]] = None,
    ) -> str:
        """Build safe Blogger HTML, preserving only trusted DeviceRank internal links."""
        clean_body = remove_all_anchor_tags(clean_html_fragment(output.html_content))
        article_images = images or []
        used_link_indexes: Set[int] = set()

        for index, link in enumerate(internal_links, 1):
            token = f"[[INTERNAL_LINK_{index}]]"
            url = sanitize_url(str(link.get("blogger_url") or ""), enforce_https=True)
            title = strip_html(str(link.get("title") or "")).strip()
            if not url or not is_devicerank_url(url) or not title:
                clean_body = clean_body.replace(token, "")
                continue
            if token in clean_body:
                anchor = (
                    f'<a href="{html_lib.escape(url, quote=True)}" '
                    f'rel="noopener">{html_lib.escape(title)}</a>'
                )
                clean_body = clean_body.replace(token, anchor, 1)
                used_link_indexes.add(index)

        clean_body = re.sub(r"\[\[INTERNAL_LINK_\d+\]\]", "", clean_body)

        for index, source in enumerate(google_sources, 1):
            token = f"[[GOOGLE_CITATION_{index}]]"
            if not is_official_google_url(source.url):
                clean_body = clean_body.replace(token, "")
                continue
            citation = (
                f'<a href="{html_lib.escape(source.url, quote=True)}" '
                f'rel="noopener noreferrer" target="_blank">'
                f'{html_lib.escape(source.title)}</a>'
            )
            clean_body = clean_body.replace(token, citation, 1)
            clean_body = clean_body.replace(token, "")
        clean_body = re.sub(r"\[\[GOOGLE_CITATION_\d+\]\]", "", clean_body)
        remaining_links = [
            (index, link)
            for index, link in enumerate(internal_links, 1)
            if index not in used_link_indexes
        ]
        related_guides = self._build_related_guides(remaining_links)

        image_figures = [
            self._build_evergreen_image_figure(image, featured=index == 0)
            for index, image in enumerate(article_images)
        ]
        hero_image = image_figures[0] if image_figures else ""
        clean_body = self._insert_inline_images(clean_body, image_figures[1:])

        takeaways_items = "\n".join(
            f"      <li>{remove_all_anchor_tags(clean_html_fragment(item))}</li>"
            for item in output.key_takeaways
        )
        faq_parts = []
        for faq in output.faq_items:
            clean_q = remove_all_anchor_tags(clean_html_fragment(faq.question))
            clean_a = remove_all_anchor_tags(clean_html_fragment(faq.answer))
            faq_parts.append(
                '<div style="margin-bottom: 16px;">\n'
                f'  <h3 style="color: #2d3748; font-size: 18px; margin-bottom: 6px;">{clean_q}</h3>\n'
                f'  <p style="color: #4a5568; margin-top: 0;">{clean_a}</p>\n'
                "</div>"
            )

        schema_markup = generate_json_ld_schema(
            title=selected.topic.title,
            meta_description=output.meta_description.strip(),
            canonical_url="",
            author_name="DeviceRank Editorial Team",
            publisher_name="DeviceRank",
            article_type="BlogPosting",
            image_url=article_images[0].url if article_images else None,
            word_count=len(strip_html(clean_body).split()),
        )
        return EVERGREEN_BLOGGER_HTML_TEMPLATE.format(
            hero_image=hero_image,
            takeaways_items=takeaways_items,
            body_content=clean_body,
            related_guides=related_guides,
            faq_content="\n".join(faq_parts),
            schema_markup=schema_markup,
        )

    @staticmethod
    def _build_evergreen_image_figure(image: ArticleImage, featured: bool = False) -> str:
        """Render a responsive photo with accessible text and required attribution."""
        loading = "eager" if featured else "lazy"
        priority = ' fetchpriority="high"' if featured else ""
        return (
            '<figure style="margin: 24px 0; text-align: center;">\n'
            f'  <img src="{html_lib.escape(image.url, quote=True)}" '
            f'alt="{html_lib.escape(image.alt_text, quote=True)}" '
            f'width="{image.width}" height="{image.height}" loading="{loading}" '
            f'decoding="async"{priority} '
            'style="display: block; width: 100%; height: auto; border-radius: 10px; '
            'box-shadow: 0 4px 14px rgba(15,23,42,0.14);" />\n'
            '  <figcaption style="font-size: 12px; color: #64748b; margin-top: 7px;">'
            'Photo by '
            f'<a href="{html_lib.escape(image.photographer_url, quote=True)}" '
            'rel="noopener noreferrer" target="_blank">'
            f'{html_lib.escape(image.photographer_name)}</a> on '
            f'<a href="{html_lib.escape(image.source_url, quote=True)}" '
            'rel="noopener noreferrer" target="_blank">Unsplash</a>'
            '</figcaption>\n'
            '</figure>'
        )

    @staticmethod
    def _insert_inline_images(body_html: str, image_figures: List[str]) -> str:
        """Distribute supporting images between tutorial sections."""
        if not image_figures:
            return body_html
        parts = re.split(r"(?=<h2\b)", body_html, flags=re.IGNORECASE)
        section_count = len(parts) - 1
        if section_count < 1:
            return body_html + "\n" + "\n".join(image_figures)

        insertions: Dict[int, List[str]] = {}
        for index, figure in enumerate(image_figures, 1):
            section_index = round(index * section_count / (len(image_figures) + 1))
            section_index = max(1, min(section_count, section_index))
            insertions.setdefault(section_index, []).append(figure)
        for section_index, figures in insertions.items():
            parts[section_index] += "\n" + "\n".join(figures)
        return "".join(parts)

    @staticmethod
    def _build_related_guides(links: List[Any]) -> str:
        items = []
        for _index, link in links:
            url = sanitize_url(str(link.get("blogger_url") or ""), enforce_https=True)
            title = strip_html(str(link.get("title") or "")).strip()
            if not url or not is_devicerank_url(url) or not title:
                continue
            items.append(
                f'<li><a href="{html_lib.escape(url, quote=True)}" rel="noopener">'
                f"{html_lib.escape(title)}</a></li>"
            )
        if not items:
            return ""
        return (
            '<aside style="margin-top: 30px; padding: 16px 20px; background: #f0f7ff; border-radius: 6px;">'
            '<h2 style="margin-top: 0; font-size: 21px;">Related DeviceRank Guides</h2>'
            f'<ul style="margin-bottom: 0;">{"".join(items)}</ul></aside>'
        )

    def _format_cluster_context(self, clusters: List[Any]) -> str:
        """Formats clusters into distinct, structured source blocks for Gemini context."""
        context_blocks = []
        for index, item in enumerate(clusters, 1):
            is_cluster = isinstance(item, StoryCluster)
            canonical = item.canonical_article if is_cluster else item
            articles = item.articles if is_cluster else [item]

            article_blocks = []
            for art in articles:
                text_part = f"\n    <full_text>{art.full_text[:1200]}</full_text>" if getattr(art, "full_text", None) else ""
                article_blocks.append(
                    f"  <source_article outlet=\"{art.source_name}\" url=\"{art.link}\" headline=\"{art.title}\">\n"
                    f"    <summary>{art.summary}</summary>{text_part}\n"
                    f"  </source_article>"
                )

            corrob_str = ", ".join(item.source_names) if is_cluster else canonical.source_name
            context_blocks.append(
                f"<cluster index=\"{index}\" headline=\"{canonical.title}\" primary_outlet=\"{canonical.source_name}\" corroborated_by=\"{corrob_str}\">\n"
                + "\n".join(article_blocks)
                + f"\n</cluster>"
            )

        return "\n\n".join(context_blocks)

    def write_digest(
        self,
        clusters: List[Any],
        slot_info: Optional[SlotInfo] = None,
    ) -> GeneratedArticle:
        """Generates a complete, slot-specific digest with deterministic title, 4 labels, and mandatory originality layers."""
        if not 3 <= len(clusters) <= 10:
            raise ValueError(f"Digest requires between 3 and 10 story clusters, received {len(clusters)}")

        if slot_info is None:
            slot_info = get_current_slot()

        # Extract articles and sources
        all_articles: List[RawArticle] = []
        for item in clusters:
            if isinstance(item, StoryCluster):
                all_articles.extend(item.articles)
            elif isinstance(item, RawArticle):
                all_articles.append(item)

        canonical_articles = [item.canonical_article if isinstance(item, StoryCluster) else item for item in clusters]
        all_source_urls = list(dict.fromkeys(a.link for a in all_articles if a.link))
        all_source_names = list(dict.fromkeys(a.source_name for a in all_articles if a.source_name))
        primary_category = canonical_articles[0].blogger_label or "Tech News"

        # Validate featured image
        validated_image_url = None
        for art in canonical_articles:
            if art.image_url:
                validated = validate_image_url(art.image_url)
                if validated:
                    validated_image_url = validated
                    break

        stories_context = self._format_cluster_context(clusters)

        # -------------------------------------------------------------------
        # Slot-Specific Structured Generation & Quality Gates
        # -------------------------------------------------------------------
        if slot_info.slot_type == SlotType.MORNING:
            prompt = MORNING_DIGEST_PROMPT.format(
                story_count=len(clusters),
                stories_context=stories_context,
            )
            raw_output: MorningDigestOutput = self._call_gemini_structured(prompt, MorningDigestOutput)
            html_content = self._assemble_morning_html(
                output=raw_output,
                clusters=clusters,
                slot_info=slot_info,
                featured_image=validated_image_url,
            )
            topic_phrases = raw_output.topic_phrases
            meta_desc = raw_output.meta_description
            focus_kw = raw_output.focus_keyword
            secondary_kws = raw_output.secondary_keywords
            takeaways = raw_output.key_takeaways

        elif slot_info.slot_type == SlotType.MIDDAY:
            supporting_count = max(0, len(clusters) - 1)
            prompt = MIDDAY_DIGEST_PROMPT.format(
                story_count=len(clusters),
                supporting_count=supporting_count,
                stories_context=stories_context,
            )
            raw_output: MiddayDigestOutput = self._call_gemini_structured(prompt, MiddayDigestOutput)
            html_content = self._assemble_midday_html(
                output=raw_output,
                clusters=clusters,
                slot_info=slot_info,
                featured_image=validated_image_url,
            )
            topic_phrases = raw_output.topic_phrases
            meta_desc = raw_output.meta_description
            focus_kw = raw_output.focus_keyword
            secondary_kws = raw_output.secondary_keywords
            takeaways = raw_output.key_takeaways

        else:  # Evening Slot
            prompt = EVENING_DIGEST_PROMPT.format(
                story_count=len(clusters),
                stories_context=stories_context,
            )
            raw_output: EveningDigestOutput = self._call_gemini_structured(prompt, EveningDigestOutput)
            html_content = self._assemble_evening_html(
                output=raw_output,
                clusters=clusters,
                slot_info=slot_info,
                featured_image=validated_image_url,
            )
            topic_phrases = raw_output.topic_phrases
            meta_desc = raw_output.meta_description
            focus_kw = raw_output.focus_keyword
            secondary_kws = raw_output.secondary_keywords
            takeaways = raw_output.key_takeaways

        # -------------------------------------------------------------------
        # Deterministic Title & Exactly 4 Controlled Taxonomy Labels
        # -------------------------------------------------------------------
        deterministic_title = build_deterministic_title(topic_phrases, slot_info.slot_display)
        standardized_labels = get_standardized_labels(slot_info.slot_display, primary_category)

        return GeneratedArticle(
            title=deterministic_title,
            meta_description=meta_desc,
            html_content=html_content,
            labels=standardized_labels,
            word_count=len(strip_html(html_content).split()),
            focus_keyword=focus_kw,
            secondary_keywords=secondary_kws,
            key_takeaways=takeaways,
            faq_items=[],
            source_url=canonical_articles[0].link,
            source_urls=all_source_urls,
            source_name=canonical_articles[0].source_name,
            source_names=all_source_names,
            category=canonical_articles[0].category,
            featured_image=validated_image_url,
            slot_id=slot_info.slot_id,
            topic_phrases=topic_phrases,
        )

    # -----------------------------------------------------------------------
    # Slot HTML Assemblers
    # -----------------------------------------------------------------------

    def _assemble_morning_html(
        self,
        output: MorningDigestOutput,
        clusters: List[Any],
        slot_info: SlotInfo,
        featured_image: Optional[str],
    ) -> str:
        """Assembles Morning Brief HTML with punchy developments and metric deltas."""
        takeaways_items = "\n".join(f"      <li>{t}</li>" for t in output.key_takeaways)
        image_figure = self._build_image_figure(featured_image, output.topic_phrases)

        story_sections = []
        for index, (cluster, story) in enumerate(zip(clusters, output.stories), 1):
            canonical = cluster.canonical_article if isinstance(cluster, StoryCluster) else cluster
            is_multi = isinstance(cluster, StoryCluster) and len(cluster.source_names) > 1

            if is_multi:
                source_badge = f"Corroborated by: <strong>{', '.join(cluster.source_names)}</strong>"
            else:
                source_badge = f"Source: <strong>{canonical.source_name}</strong>"

            metric_badge = ""
            if story.key_metric_delta:
                metric_badge = (
                    f"<div style=\"margin: 10px 0; background: #eef2ff; border-left: 3px solid #4f46e5; "
                    f"padding: 8px 14px; border-radius: 4px; font-size: 14px; color: #3730a3;\">"
                    f"<strong>⚡ Key Metric / Delta:</strong> {story.key_metric_delta}</div>"
                )

            story_sections.append(
                f"<section class=\"digest-story\" style=\"margin-bottom: 28px; padding-bottom: 22px; border-bottom: 1px solid #e2e8f0;\">\n"
                f"  <h2 style=\"color: #1a202c; font-size: 20px; margin-bottom: 8px;\">{index}. {canonical.title}</h2>\n"
                f"  <p style=\"font-size: 13px; color: #64748b; margin-top: 0; margin-bottom: 12px;\">{source_badge}</p>\n"
                f"  <p style=\"margin-bottom: 10px;\">{remove_all_anchor_tags(story.summary)}</p>\n"
                f"  {metric_badge}\n"
                f"  <div style=\"background: #f8fafc; padding: 10px 14px; border-radius: 4px; font-size: 14px;\">"
                f"<strong>Why It Matters:</strong> {remove_all_anchor_tags(story.why_it_matters)}</div>\n"
                f"</section>"
            )

        source_items = self._build_source_footer_items(clusters)
        schema_markup = self._build_schema_markup(output.topic_phrases, slot_info, clusters)

        return DIGEST_BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            slot_display=slot_info.slot_display,
            takeaways_items=takeaways_items,
            story_sections="\n\n".join(story_sections),
            originality_section="",
            source_items=source_items,
            schema_markup=schema_markup,
        )

    def _assemble_midday_html(
        self,
        output: MiddayDigestOutput,
        clusters: List[Any],
        slot_info: SlotInfo,
        featured_image: Optional[str],
    ) -> str:
        """Assembles Midday Brief HTML with 1 lead multi-source synthesis and comparison table."""
        takeaways_items = "\n".join(f"      <li>{t}</li>" for t in output.key_takeaways)
        image_figure = self._build_image_figure(featured_image, output.topic_phrases)

        # 1. Lead Story Hero Section
        lead_cluster = clusters[0]
        lead_canonical = lead_cluster.canonical_article if isinstance(lead_cluster, StoryCluster) else lead_cluster
        lead_sources = lead_cluster.source_names if isinstance(lead_cluster, StoryCluster) else [lead_canonical.source_name]

        lead_source_badge = (
            f"Corroborated by: <strong>{', '.join(lead_sources)}</strong>"
            if len(lead_sources) > 1
            else f"Source: <strong>{lead_canonical.source_name}</strong>"
        )

        lead_section = (
            f"<section class=\"digest-story lead-story\" style=\"margin-bottom: 32px; padding: 20px; background: #fafafa; border: 1px solid #e5e7eb; border-radius: 8px;\">\n"
            f"  <div style=\"font-size: 12px; font-weight: 700; color: #0284c7; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;\">⭐ Lead Story Analysis</div>\n"
            f"  <h2 style=\"color: #0f172a; font-size: 23px; margin-top: 0; margin-bottom: 8px;\">{output.lead_story.headline or lead_canonical.title}</h2>\n"
            f"  <p style=\"font-size: 13px; color: #64748b; margin-top: 0; margin-bottom: 14px;\">{lead_source_badge}</p>\n"
            f"  <p style=\"font-size: 17px; line-height: 1.7; margin-bottom: 16px;\">{remove_all_anchor_tags(output.lead_story.summary)}</p>\n"
            f"  <div style=\"background: #ffffff; border-left: 4px solid #0284c7; padding: 12px 16px; margin-bottom: 14px; border-radius: 4px;\">\n"
            f"    <strong style=\"color: #0369a1;\">Technical & Architectural Reality:</strong>\n"
            f"    <p style=\"margin: 6px 0 0 0; font-size: 15px;\">{remove_all_anchor_tags(output.lead_story.core_conflict_and_engineering)}</p>\n"
            f"  </div>\n"
            f"  <div style=\"background: #ffffff; border-left: 4px solid #059669; padding: 12px 16px; border-radius: 4px;\">\n"
            f"    <strong style=\"color: #047857;\">Market & Ecosystem Shift:</strong>\n"
            f"    <p style=\"margin: 6px 0 0 0; font-size: 15px;\">{remove_all_anchor_tags(output.lead_story.market_implications)}</p>\n"
            f"  </div>\n"
            f"</section>"
        )

        # 2. Supporting Stories
        supporting_sections = []
        for index, (cluster, story) in enumerate(zip(clusters[1:], output.supporting_stories), 2):
            canonical = cluster.canonical_article if isinstance(cluster, StoryCluster) else cluster
            is_multi = isinstance(cluster, StoryCluster) and len(cluster.source_names) > 1
            source_badge = (
                f"Corroborated by: <strong>{', '.join(cluster.source_names)}</strong>"
                if is_multi
                else f"Source: <strong>{canonical.source_name}</strong>"
            )

            supporting_sections.append(
                f"<section class=\"digest-story\" style=\"margin-bottom: 24px; padding-bottom: 18px; border-bottom: 1px solid #e2e8f0;\">\n"
                f"  <h2 style=\"color: #1a202c; font-size: 19px; margin-bottom: 6px;\">{index}. {canonical.title}</h2>\n"
                f"  <p style=\"font-size: 13px; color: #64748b; margin-top: 0; margin-bottom: 10px;\">{source_badge}</p>\n"
                f"  <p style=\"margin-bottom: 10px;\">{remove_all_anchor_tags(story.summary)}</p>\n"
                f"  <div style=\"background: #f8fafc; padding: 8px 12px; border-radius: 4px; font-size: 14px;\">"
                f"<strong>Why It Matters:</strong> {remove_all_anchor_tags(story.why_it_matters)}</div>\n"
                f"</section>"
            )

        # 3. Mandatory Comparison Matrix
        table_html = clean_html_fragment(remove_all_anchor_tags(output.comparison_table_html or ""))
        if not table_html or "<table" not in table_html.lower():
            # Fallback table if empty
            table_html = (
                "<table style=\"width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;\">\n"
                "  <thead><tr style=\"background: #f1f5f9;\"><th style=\"border: 1px solid #cbd5e1; padding: 8px;\">Key Dimension</th><th style=\"border: 1px solid #cbd5e1; padding: 8px;\">Technical Reality</th></tr></thead>\n"
                "  <tbody><tr><td style=\"border: 1px solid #cbd5e1; padding: 8px;\">Lead Focus</td><td style=\"border: 1px solid #cbd5e1; padding: 8px;\">Multi-source verified technical shift</td></tr></tbody>\n"
                "</table>"
            )

        originality_section = (
            f"<div class=\"devicerank-originality-matrix\" style=\"margin: 32px 0; background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 18px;\">\n"
            f"  <h3 style=\"margin-top: 0; color: #0f172a; font-size: 18px;\">📊 DeviceRank Technical Comparison Matrix</h3>\n"
            f"  {table_html}\n"
            f"</div>"
        )

        all_story_html = lead_section + "\n\n" + "\n\n".join(supporting_sections)
        source_items = self._build_source_footer_items(clusters)
        schema_markup = self._build_schema_markup(output.topic_phrases, slot_info, clusters)

        return DIGEST_BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            slot_display=slot_info.slot_display,
            takeaways_items=takeaways_items,
            story_sections=all_story_html,
            originality_section=originality_section,
            source_items=source_items,
            schema_markup=schema_markup,
        )

    def _assemble_evening_html(
        self,
        output: EveningDigestOutput,
        clusters: List[Any],
        slot_info: SlotInfo,
        featured_image: Optional[str],
    ) -> str:
        """Assembles Evening Brief HTML with buyer/privacy implications and DeviceRank scorecards."""
        takeaways_items = "\n".join(f"      <li>{t}</li>" for t in output.key_takeaways)
        image_figure = self._build_image_figure(featured_image, output.topic_phrases)

        story_sections = []
        for index, (cluster, story) in enumerate(zip(clusters, output.stories), 1):
            canonical = cluster.canonical_article if isinstance(cluster, StoryCluster) else cluster
            is_multi = isinstance(cluster, StoryCluster) and len(cluster.source_names) > 1
            source_badge = (
                f"Corroborated by: <strong>{', '.join(cluster.source_names)}</strong>"
                if is_multi
                else f"Source: <strong>{canonical.source_name}</strong>"
            )

            story_sections.append(
                f"<section class=\"digest-story\" style=\"margin-bottom: 26px; padding-bottom: 20px; border-bottom: 1px solid #e2e8f0;\">\n"
                f"  <h2 style=\"color: #1a202c; font-size: 20px; margin-bottom: 6px;\">{index}. {canonical.title}</h2>\n"
                f"  <p style=\"font-size: 13px; color: #64748b; margin-top: 0; margin-bottom: 10px;\">{source_badge}</p>\n"
                f"  <p style=\"margin-bottom: 10px;\">{remove_all_anchor_tags(story.summary)}</p>\n"
                f"  <div style=\"background: #fdf2f8; border-left: 3px solid #db2777; padding: 10px 14px; border-radius: 4px; font-size: 14px; color: #831843;\">"
                f"<strong>🛡️ Buyer & Privacy Implication:</strong> {remove_all_anchor_tags(story.buyer_privacy_implications)}</div>\n"
                f"</section>"
            )

        # Build Scorecards HTML
        scorecards = output.scorecards or []
        if not scorecards:
            # Fallback scorecard
            scorecards = [
                DeviceRankScorecardItem(
                    device_name="Evening Featured Devices",
                    value_score="Transparent Evaluation",
                    longevity_score="Standard OS Support",
                    privacy_score="Standard Telemetry",
                    repairability_score="Standard Modular Index",
                    buying_verdict="Review pricing and longevity metrics prior to upgrading.",
                )
            ]

        scorecard_cards = []
        for card in scorecards:
            scorecard_cards.append(
                f"<div style=\"background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 14px;\">\n"
                f"  <h4 style=\"margin-top: 0; margin-bottom: 10px; color: #0f172a; font-size: 17px;\">📱 {card.device_name}</h4>\n"
                f"  <div style=\"display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; font-size: 13px;\">\n"
                f"    <div style=\"background: #fff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;\"><strong>💰 Value Rating:</strong><br/>{card.value_score}</div>\n"
                f"    <div style=\"background: #fff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;\"><strong>⏳ Longevity:</strong><br/>{card.longevity_score}</div>\n"
                f"    <div style=\"background: #fff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;\"><strong>🔒 Privacy:</strong><br/>{card.privacy_score}</div>\n"
                f"    <div style=\"background: #fff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;\"><strong>🔧 Repair Index:</strong><br/>{card.repairability_score}</div>\n"
                f"  </div>\n"
                f"  <div style=\"margin-top: 12px; font-size: 14px; color: #1e293b; background: #ecfdf5; border-left: 3px solid #10b981; padding: 8px 12px; border-radius: 4px;\">\n"
                f"    <strong>🎯 Buying Verdict:</strong> {card.buying_verdict}\n"
                f"  </div>\n"
                f"</div>"
            )

        originality_section = (
            f"<div class=\"devicerank-originality-scorecards\" style=\"margin: 32px 0;\">\n"
            f"  <h3 style=\"color: #0f172a; font-size: 19px; margin-bottom: 14px;\">🏷️ DeviceRank Buyer Scorecards</h3>\n"
            f"  {''.join(scorecard_cards)}\n"
            f"</div>"
        )

        source_items = self._build_source_footer_items(clusters)
        schema_markup = self._build_schema_markup(output.topic_phrases, slot_info, clusters)

        return DIGEST_BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            slot_display=slot_info.slot_display,
            takeaways_items=takeaways_items,
            story_sections="\n\n".join(story_sections),
            originality_section=originality_section,
            source_items=source_items,
            schema_markup=schema_markup,
        )

    # -----------------------------------------------------------------------
    # Helper Components
    # -----------------------------------------------------------------------

    def _build_image_figure(self, image_url: Optional[str], topic_phrases: List[str]) -> str:
        """Constructs a responsive semantic figure for featured images."""
        if not image_url:
            return ""
        topics_str = ", ".join(topic_phrases[:2]) if topic_phrases else "Technology Developments"
        return (
            f"<figure style=\"margin: 20px 0; text-align: center;\">\n"
            f"  <img src=\"{image_url}\" alt=\"{topics_str}\" "
            f"style=\"max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);\" "
            f"loading=\"lazy\" />\n"
            f"  <figcaption style=\"font-size: 13px; color: #718096; margin-top: 6px;\">"
            f"Visual briefing: {topics_str}</figcaption>\n"
            f"</figure>"
        )

    def _build_source_footer_items(self, clusters: List[Any]) -> str:
        """Builds plain-text source attribution list for post footer."""
        items = []
        for cluster in clusters:
            canonical = cluster.canonical_article if isinstance(cluster, StoryCluster) else cluster
            if isinstance(cluster, StoryCluster) and len(cluster.source_names) > 1:
                items.append(
                    f"<li><strong>{canonical.title}</strong> — Corroborated by {', '.join(cluster.source_names)}</li>"
                )
            else:
                items.append(
                    f"<li><strong>{canonical.title}</strong> — Reported by {canonical.source_name}</li>"
                )
        return "\n      ".join(items)

    def _build_schema_markup(
        self,
        topic_phrases: List[str],
        slot_info: SlotInfo,
        clusters: List[Any],
    ) -> str:
        """Constructs JSON-LD schema for search engines."""
        canonical_articles = [c.canonical_article if isinstance(c, StoryCluster) else c for c in clusters]
        schema_dict = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": build_deterministic_title(topic_phrases, slot_info.slot_display),
            "description": f"DeviceRank {slot_info.slot_display} analyzing top verified technology news.",
            "datePublished": datetime.now(timezone.utc).isoformat(),
            "dateModified": datetime.now(timezone.utc).isoformat(),
            "author": {
                "@type": "Organization",
                "name": "DeviceRank Editorial Team",
                "url": "https://devicerank.blogspot.com",
            },
            "publisher": {
                "@type": "Organization",
                "name": "DeviceRank",
                "url": "https://devicerank.blogspot.com",
            },
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": "https://devicerank.blogspot.com",
            },
            "isAccessibleForFree": True,
            "hasPart": [
                {
                    "@type": "NewsArticle",
                    "headline": art.title,
                    "url": art.link,
                }
                for art in canonical_articles
            ],
        }
        return f"<script type=\"application/ld+json\">\n{json.dumps(schema_dict, indent=2)}\n</script>"

    def write_article(self, raw_article: RawArticle) -> GeneratedArticle:
        """Generates a standalone single-story article with structured output and HTML assembly."""
        prompt = ARTICLE_GENERATION_PROMPT.format(
            title=raw_article.title,
            source_name=raw_article.source_name,
            category=raw_article.category,
            summary=raw_article.summary,
            full_text=raw_article.full_text or "No additional full text provided.",
        )

        output: SEOArticleOutput = self._call_gemini_structured(prompt, SEOArticleOutput)

        validated_image_url = None
        if raw_article.image_url:
            validated_image_url = validate_image_url(raw_article.image_url)

        html_content = self._assemble_html_content(
            raw_article=raw_article,
            title=output.title,
            meta_description=output.meta_description,
            body_content=output.html_content,
            takeaways=output.key_takeaways,
            faqs=output.faq_items,
            featured_image=validated_image_url,
        )

        return GeneratedArticle(
            title=sanitize_title(output.title),
            meta_description=output.meta_description,
            html_content=html_content,
            labels=output.labels or [raw_article.blogger_label or "Tech News"],
            word_count=len(strip_html(html_content).split()),
            focus_keyword=output.focus_keyword,
            secondary_keywords=output.secondary_keywords,
            key_takeaways=output.key_takeaways,
            faq_items=output.faq_items,
            source_url=raw_article.link,
            source_urls=[raw_article.link],
            source_name=raw_article.source_name,
            source_names=[raw_article.source_name],
            category=raw_article.category,
            featured_image=validated_image_url,
        )

    def _assemble_html_content(
        self,
        raw_article: RawArticle,
        title: str,
        meta_description: str,
        body_content: str,
        takeaways: List[str],
        faqs: List[FAQItem],
        featured_image: Optional[str] = None,
    ) -> str:
        """Assembles a standalone article into the standard Blogger HTML template."""
        clean_body = remove_all_anchor_tags(clean_html_fragment(body_content))
        takeaways_items = "\n".join(f"      <li>{t}</li>" for t in takeaways)

        faq_parts = []
        for faq in faqs:
            clean_q = remove_all_anchor_tags(clean_html_fragment(faq.question))
            clean_a = remove_all_anchor_tags(clean_html_fragment(faq.answer))
            faq_parts.append(
                f"<div style=\"margin-bottom: 16px;\">\n"
                f"  <h3 style=\"color: #2d3748; font-size: 18px; margin-bottom: 6px;\">{clean_q}</h3>\n"
                f"  <p style=\"color: #4a5568; margin-top: 0;\">{clean_a}</p>\n"
                f"</div>"
            )
        faq_content = "\n".join(faq_parts) if faq_parts else "<p>No questions submitted yet.</p>"

        img_to_use = featured_image
        if not img_to_use and raw_article.image_url:
            img_to_use = validate_image_url(raw_article.image_url)

        image_figure = ""
        if img_to_use:
            image_figure = (
                f"<figure style=\"margin: 20px 0; text-align: center;\">\n"
                f"  <img src=\"{img_to_use}\" alt=\"{title}\" "
                f"style=\"max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);\" "
                f"loading=\"lazy\" />\n"
                f"  <figcaption style=\"font-size: 13px; color: #718096; margin-top: 6px;\">Featured coverage: {title}</figcaption>\n"
                f"</figure>"
            )

        schema_markup = generate_json_ld_schema(
            title=title,
            meta_description=meta_description,
            canonical_url=raw_article.link,
            author_name="DeviceRank Editorial Team",
            publisher_name="DeviceRank",
            article_type="BlogPosting",
            image_url=img_to_use,
            word_count=len(strip_html(clean_body).split()),
        )

        return BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            takeaways_items=takeaways_items,
            body_content=clean_body,
            faq_content=faq_content,
            source_name=raw_article.source_name,
            schema_markup=schema_markup,
        )
