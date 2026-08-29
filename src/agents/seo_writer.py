"""SEO Content Generation Engine powered exclusively by Google Gemini via modern google-genai SDK.

Features typed Pydantic structured output, exponential backoff for transient errors,
HTML sanitization against allowlists, deterministic title grammar, and Google Rich Snippets JSON-LD schema generation.
"""

import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Type, Union
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from config.settings import settings
from src.agents.prompts import (
    ARTICLE_GENERATION_PROMPT,
    BLOGGER_HTML_TEMPLATE,
    DIGEST_BLOGGER_HTML_TEMPLATE,
    DIGEST_GENERATION_PROMPT,
    SEO_SYSTEM_PROMPT,
)
from src.db.history import history_db
from src.fetchers.clustering import StoryCluster
from src.fetchers.rss_fetcher import RawArticle
from src.utils.image_validator import validate_image_url
from src.utils.logger import logger
from src.utils.sanitizer import escape_feed_text, sanitize_html, sanitize_url
from src.utils.slots import SlotInfo, SlotType, build_deterministic_title, get_current_slot, get_standardized_labels


class FAQItem(BaseModel):
    """Single Q&A item for FAQ section and JSON-LD schema."""

    question: str
    answer: str


class SEOArticleOutput(BaseModel):
    """Pydantic schema passed directly to Gemini for typed structured output."""

    title: str = Field(description="SEO title between 45-58 characters with primary keyword front-loaded")
    meta_description: str = Field(description="Meta search description between 140-155 characters")
    focus_keyword: str = Field(description="Primary target focus keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="3-5 secondary LSI keywords")
    key_takeaways: List[str] = Field(default_factory=list, description="Exactly 3 core takeaway bullet points")
    html_content: str = Field(description="Article body HTML with H2/H3 headings, table, and Why It Matters")
    labels: List[str] = Field(default_factory=list, description="3-5 clean taxonomy tags")
    faq_items: List[FAQItem] = Field(default_factory=list, description="3-4 targeted FAQ items")
    word_count: int = Field(default=0, description="Estimated word count")


class DigestStoryOutput(BaseModel):
    """Generated summary for one source story in a news digest."""

    summary: str = Field(description="Factual 100-160 word summary of the supplied story cluster")
    why_it_matters: str = Field(description="Short practical explanation of why the story matters")
    key_metric_or_shift: Optional[str] = Field(
        default=None,
        description="Concrete metric, price delta, or overnight shift specification",
    )


class DeviceRankScorecardItem(BaseModel):
    """DeviceRank evaluation scorecard for a featured product."""

    device_name: str = Field(description="Product or technology name (e.g. Galaxy S26 FE, Pixel 11)")
    value_score: str = Field(description="Value rating e.g. '8.5 / 10' or 'High Value'")
    longevity_score: str = Field(description="Longevity & software support rating e.g. '7 / 10'")
    privacy_score: str = Field(description="Privacy & data control rating e.g. '9 / 10'")
    repairability_score: str = Field(description="Hardware repairability / upgrade rating e.g. '6 / 10'")
    buying_verdict: str = Field(description="Clear 1-sentence buyer verdict")


class SEODigestOutput(BaseModel):
    """Typed Gemini output for a six-to-eight-story digest."""

    topic_phrases: List[str] = Field(
        default_factory=list,
        description="Exactly 3 concise entity/topic phrases (e.g. ['Pixel 11', 'DLSS 5', 'iOS 27']) for deterministic title grammar",
    )
    meta_description: str = Field(description="Meta search description between 140-155 characters")
    focus_keyword: str = Field(description="Primary target focus keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="3-5 secondary keywords")
    key_takeaways: List[str] = Field(default_factory=list, description="Exactly 3 digest-level takeaways")
    stories: List[DigestStoryOutput] = Field(min_length=6, max_length=8)
    comparison_table_html: Optional[str] = Field(
        default=None,
        description="Clean responsive HTML table comparing verified specs or pricing if applicable",
    )
    scorecards: Optional[List[DeviceRankScorecardItem]] = Field(
        default=None,
        description="DeviceRank evaluation scorecard items for evening digests",
    )
    labels: List[str] = Field(default_factory=list, description="Taxonomy suggestions")
    word_count: int = Field(default=0, description="Estimated word count")


class GeneratedArticle(BaseModel):
    """Structured SEO Article ready for Blogger publishing and local preview."""

    title: str
    meta_description: str
    focus_keyword: str
    secondary_keywords: List[str] = Field(default_factory=list)
    key_takeaways: List[str] = Field(default_factory=list)
    html_content: str
    labels: List[str] = Field(default_factory=list)
    faq_items: List[FAQItem] = Field(default_factory=list)
    word_count: int = 0
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    source_urls: List[str] = Field(default_factory=list)
    source_names: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    featured_image: Optional[str] = None
    slot_id: Optional[str] = None


class SEOWriter:
    """Orchestrates structured LLM generation, exponential backoff, sanitization, and HTML assembly."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model_name or settings.gemini_model

    def _get_genai_client(self):
        """Instantiates modern google-genai Client."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set it in your .env file or pass it to SEOWriter."
            )
        from google import genai
        return genai.Client(api_key=self.api_key)

    @staticmethod
    def _usable_featured_image(image_url: Optional[str]) -> Optional[str]:
        """Return a validated, live, non-placeholder featured-image URL if available."""
        return validate_image_url(image_url, verify_live_http=True)

    def _select_digest_featured_image(self, articles: List[Any]) -> Optional[str]:
        """Select the first live, verified image from the candidate stories or clusters."""
        for item in articles:
            raw_art = item.canonical_article if hasattr(item, "canonical_article") else item
            image_url = getattr(raw_art, "image_url", None)
            if image_url:
                validated = self._usable_featured_image(image_url)
                if validated:
                    return validated
        return None

    def _call_gemini_structured(
        self,
        prompt: str,
        response_schema: Type[BaseModel] = SEOArticleOutput,
    ) -> BaseModel:
        """Invokes Gemini API with typed Pydantic structured output and exponential backoff for transient errors."""
        client = self._get_genai_client()
        from google.genai import types, errors

        config = types.GenerateContentConfig(
            system_instruction=SEO_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.7,
        )

        max_retries = 4
        base_delay = 2.0
        max_delay = 30.0

        for attempt in range(max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=config,
                )

                # 1. Use typed parsed response if directly available
                if hasattr(response, "parsed") and response.parsed is not None:
                    if isinstance(response.parsed, response_schema):
                        return response.parsed
                    if isinstance(response.parsed, dict):
                        return response_schema.model_validate(response.parsed)

                # 2. Fallback to parsing text JSON into Pydantic schema
                raw_text = response.text or ""
                return response_schema.model_validate_json(raw_text.strip())

            except errors.APIError as e:
                code = getattr(e, "code", None)
                is_transient = code in (429, 500, 502, 503, 504) or "503" in str(e) or "429" in str(e) or "quota" in str(e).lower()

                if is_transient and attempt < max_retries:
                    delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0.1, 1.0)
                    logger.warning(
                        f"Gemini API transient error (status={code}): {e}. "
                        f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    continue

                logger.error(f"Gemini API non-retryable or max retries exceeded: {e}")
                raise

            except Exception as e:
                if attempt < max_retries and any(err in str(e).lower() for err in ["connection", "timeout", "reset", "503", "500"]):
                    delay = min(max_delay, base_delay * (2 ** attempt)) + random.uniform(0.1, 1.0)
                    logger.warning(
                        f"Gemini network exception: {e}. Retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    continue

                logger.error(f"Failed to generate structured content with Gemini: {e}")
                raise

    def _generate_json_ld_schema(
        self,
        title: str,
        meta_description: str,
        raw_article: RawArticle,
        faqs: List[FAQItem],
    ) -> str:
        """Generates Google Rich Snippet JSON-LD Structured Data for TechArticle and FAQPage."""
        schemas = []

        # 1. TechArticle Schema
        article_schema = {
            "@context": "https://schema.org",
            "@type": "TechArticle",
            "headline": title,
            "description": meta_description,
            "publisher": {
                "@type": "Organization",
                "name": "DeviceRank",
                "url": "https://devicerank.blogspot.com",
            },
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": "https://devicerank.blogspot.com",
            },
        }
        sanitized_img = self._usable_featured_image(raw_article.image_url)
        if sanitized_img:
            article_schema["image"] = [sanitized_img]

        schemas.append(article_schema)

        # 2. FAQPage Schema
        if faqs:
            faq_schema = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": f.question,
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": f.answer,
                        },
                    }
                    for f in faqs
                    if f.question and f.answer
                ],
            }
            if faq_schema["mainEntity"]:
                schemas.append(faq_schema)

        scripts = [
            f'<script type="application/ld+json">\n{json.dumps(s, indent=2)}\n</script>'
            for s in schemas
        ]
        return "\n".join(scripts)

    def _assemble_html_content(
        self,
        raw_article: RawArticle,
        title: str,
        meta_description: str,
        body_content: str,
        takeaways: List[str],
        faqs: List[FAQItem],
    ) -> str:
        """Assembles images, callout boxes, FAQs, JSON-LD Schema, and sanitizes against allowlists."""
        image_figure = ""
        sanitized_img = self._usable_featured_image(raw_article.image_url)
        if sanitized_img:
            alt_text = f"{escape_feed_text(raw_article.title)} - DeviceRank Tech Analysis"
            image_figure = f"""  <figure style="margin: 20px 0; text-align: center;">
    <img src="{sanitized_img}" alt="{alt_text}" loading="lazy" style="max-width: 100%; height: auto; border-radius: 8px;" />
    <figcaption style="font-size: 0.85rem; color: #666; margin-top: 6px;">Featured Image: {escape_feed_text(raw_article.source_name)}</figcaption>
  </figure>"""

        takeaways_html = "\n".join(
            f'<li style="margin-bottom: 6px;">{escape_feed_text(t)}</li>'
            for t in takeaways[:3]
        )

        faq_html_list = []
        for faq in faqs:
            q = escape_feed_text(faq.question)
            a = escape_feed_text(faq.answer)
            faq_html_list.append(
                f"""    <div style="margin-bottom: 16px; background: #f8fafc; padding: 14px 18px; border-radius: 6px; border: 1px solid #e2e8f0;">
      <h3 style="margin: 0 0 8px 0; font-size: 17px; color: #1e293b;">{q}</h3>
      <p style="margin: 0; color: #475569; font-size: 15px; line-height: 1.6;">{a}</p>
    </div>"""
            )
        faq_content = "\n".join(faq_html_list)

        schema_markup = self._generate_json_ld_schema(
            title=title,
            meta_description=meta_description,
            raw_article=raw_article,
            faqs=faqs,
        )

        sanitized_body = sanitize_html(body_content, enforce_zero_outbound_links=True)

        return BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            takeaways_items=takeaways_html,
            body_content=sanitized_body,
            faq_content=faq_content,
            source_name=escape_feed_text(raw_article.source_name),
            schema_markup=schema_markup,
        )

    def _generate_digest_json_ld_schema(
        self,
        title: str,
        meta_description: str,
        articles: List[Any],
    ) -> str:
        """Builds NewsArticle schema that identifies every story covered by the digest."""
        about_items = []
        for item in articles:
            art = item.canonical_article if hasattr(item, "canonical_article") else item
            about_items.append({"@type": "Thing", "name": art.title})

        schema: Dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": title,
            "description": meta_description,
            "publisher": {
                "@type": "Organization",
                "name": "DeviceRank",
                "url": "https://devicerank.blogspot.com",
            },
            "mainEntityOfPage": {
                "@type": "WebPage",
                "@id": "https://devicerank.blogspot.com",
            },
            "about": about_items,
        }
        featured_image = self._select_digest_featured_image(articles)
        if featured_image:
            schema["image"] = [featured_image]
        return (
            '<script type="application/ld+json">\n'
            + json.dumps(schema, indent=2)
            + "\n</script>"
        )

    def _assemble_digest_html_content(
        self,
        articles_or_clusters: List[Any],
        title: str,
        meta_description: str,
        story_outputs: List[DigestStoryOutput],
        takeaways: List[str],
        slot_info: SlotInfo,
        comparison_table_html: Optional[str] = None,
        scorecards: Optional[List[DeviceRankScorecardItem]] = None,
    ) -> str:
        """Assembles a rich, deterministic post with slot-specific styling and originality layers."""
        image_figure = ""
        sanitized_img = self._select_digest_featured_image(articles_or_clusters)
        if sanitized_img:
            image_figure = f"""  <figure style="margin: 20px 0; text-align: center;">
    <img src="{sanitized_img}" alt="{escape_feed_text(title)}" loading="lazy" style="max-width: 100%; height: auto; border-radius: 8px;" />
    <figcaption style="font-size: 0.85rem; color: #666; margin-top: 6px;">Featured coverage from verified source outlets.</figcaption>
  </figure>"""

        takeaways_html = "\n".join(
            f'<li style="margin-bottom: 6px;">{escape_feed_text(item)}</li>'
            for item in takeaways[:3]
        )

        sections = []
        source_items = []

        for index, (cluster_or_art, story) in enumerate(zip(articles_or_clusters, story_outputs), 1):
            is_cluster = hasattr(cluster_or_art, "canonical_article")
            canon_art = cluster_or_art.canonical_article if is_cluster else cluster_or_art
            source_names_list = cluster_or_art.source_names if is_cluster else [canon_art.source_name]

            sources_display = ", ".join(escape_feed_text(s) for s in source_names_list)
            published = escape_feed_text(canon_art.published_date or "Publication time unavailable")

            # Metric / Shift badge if present
            metric_badge = ""
            if story.key_metric_or_shift:
                metric_badge = f"""    <div style="display: inline-block; background: #eef2ff; color: #4338ca; font-size: 13px; font-weight: 600; padding: 4px 10px; border-radius: 4px; margin-bottom: 8px;">
      {escape_feed_text(story.key_metric_or_shift)}
    </div>"""

            # Lead story styling for midday briefs
            lead_border = "border-left: 4px solid #4f46e5; padding-left: 16px;" if (index == 1 and slot_info.slot_type == SlotType.MIDDAY) else ""

            sections.append(
                f"""  <section class="digest-story" style="margin: 0 0 32px 0; {lead_border}">
    {metric_badge}
    <h2 style="color: #1a202c; font-size: 22px; margin: 4px 0 10px 0;">{index}. {escape_feed_text(canon_art.title)}</h2>
    <p style="font-size: 13px; color: #718096; margin: 0 0 12px 0;">Corroborated by: <strong>{sources_display}</strong> · {published}</p>
    <p style="color: #334155; line-height: 1.7;">{escape_feed_text(story.summary)}</p>
    <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 16px; margin-top: 10px;">
      <strong style="color: #0f172a;">Why It Matters:</strong> <span style="color: #475569;">{escape_feed_text(story.why_it_matters)}</span>
    </div>
  </section>"""
            )

            source_items.append(
                f"<li><strong>{sources_display}</strong> — {escape_feed_text(canon_art.title)}</li>"
            )

        # Build Originality Layer HTML
        originality_html = ""

        # 1. Comparison Matrix for Midday / Hardware
        if comparison_table_html and comparison_table_html.strip():
            sanitized_table = sanitize_html(comparison_table_html, enforce_zero_outbound_links=True)
            originality_html += f"""
  <div style="margin: 36px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
    <h3 style="margin-top: 0; color: #1e293b; font-size: 20px; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px;">
      📊 DeviceRank Comparative Analysis Matrix
    </h3>
    {sanitized_table}
  </div>"""

        # 2. Scorecard for Evening / Buyer Verdicts
        if scorecards:
            scorecard_cards = []
            for sc in scorecards:
                scorecard_cards.append(
                    f"""    <div style="background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 8px; padding: 16px; margin-bottom: 16px;">
      <h4 style="margin: 0 0 10px 0; font-size: 18px; color: #0f172a;">{escape_feed_text(sc.device_name)}</h4>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 12px; font-size: 14px;">
        <div style="background: #ffffff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;">
          <span style="color: #64748b; font-size: 12px;">Value Index</span><br/><strong>{escape_feed_text(sc.value_score)}</strong>
        </div>
        <div style="background: #ffffff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;">
          <span style="color: #64748b; font-size: 12px;">Longevity</span><br/><strong>{escape_feed_text(sc.longevity_score)}</strong>
        </div>
        <div style="background: #ffffff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;">
          <span style="color: #64748b; font-size: 12px;">Privacy & Security</span><br/><strong>{escape_feed_text(sc.privacy_score)}</strong>
        </div>
        <div style="background: #ffffff; padding: 8px; border-radius: 4px; border: 1px solid #e2e8f0;">
          <span style="color: #64748b; font-size: 12px;">Upgrade Score</span><br/><strong>{escape_feed_text(sc.repairability_score)}</strong>
        </div>
      </div>
      <div style="font-size: 14px; color: #1e293b; background: #ecfdf5; border-left: 3px solid #10b981; padding: 8px 12px; border-radius: 4px;">
        <strong>DeviceRank Verdict:</strong> {escape_feed_text(sc.buying_verdict)}
      </div>
    </div>"""
                )

            originality_html += f"""
  <div style="margin: 36px 0; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px;">
    <h3 style="margin-top: 0; color: #1e293b; font-size: 20px; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px;">
      🛡️ DeviceRank Buyer Scorecard & Upgrade Verdicts
    </h3>
    {"".join(scorecard_cards)}
  </div>"""

        return DIGEST_BLOGGER_HTML_TEMPLATE.format(
            slot_display=slot_info.slot_display,
            image_figure=image_figure,
            takeaways_items=takeaways_html,
            story_sections="\n".join(sections),
            originality_section=originality_html,
            source_items="\n".join(source_items),
            schema_markup=self._generate_digest_json_ld_schema(
                title=title,
                meta_description=meta_description,
                articles=articles_or_clusters,
            ),
        )

    def write_article(
        self,
        article: RawArticle,
        target_word_count: Optional[int] = None,
    ) -> GeneratedArticle:
        """Generates a structured, SEO-optimized post from a raw article with strict quality rules."""
        target_words = target_word_count or settings.target_word_count

        full_text_section = ""
        if article.full_text:
            full_text_section = f"### DETAILED ARTICLE CONTEXT:\n{article.full_text[:3000]}"

        related_context_section = ""
        try:
            recent_posts = history_db.get_published_articles_for_linking(category=article.category, limit=3)
            if recent_posts:
                links_items = [
                    f"- [{p['title']}]({p['blogger_url']})"
                    for p in recent_posts
                    if p.get("blogger_url")
                ]
                if links_items:
                    related_context_section = (
                        "### AVAILABLE INTERNAL ARTICLES (Link using relative or devicerank.blogspot.com URLs if relevant):\n"
                        + "\n".join(links_items)
                    )
        except Exception as e:
            logger.debug(f"Could not retrieve internal linking context: {e}")

        prompt = ARTICLE_GENERATION_PROMPT.format(
            category=article.category,
            blogger_label=article.blogger_label,
            source_name=article.source_name,
            title=article.title,
            link=article.link,
            image_url=article.image_url or "None",
            summary=article.summary,
            full_text_section=full_text_section,
            related_context_section=related_context_section,
            target_word_count=target_words,
        )

        logger.info(f"Generating SEO article with Gemini ({self.model_name})...")
        structured_output = self._call_gemini_structured(prompt)

        title = structured_output.title.strip().strip('"').strip("'")
        if not title:
            title = article.title

        meta_desc = structured_output.meta_description.strip()[:155]
        faqs = structured_output.faq_items or []
        takeaways = structured_output.key_takeaways or []

        final_html = self._assemble_html_content(
            raw_article=article,
            title=title,
            meta_description=meta_desc,
            body_content=structured_output.html_content,
            takeaways=takeaways,
            faqs=faqs,
        )

        clean_text_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", final_html)))

        labels = structured_output.labels or []
        if article.blogger_label and article.blogger_label not in labels:
            labels.insert(0, article.blogger_label)

        return GeneratedArticle(
            title=title,
            meta_description=meta_desc,
            focus_keyword=structured_output.focus_keyword,
            secondary_keywords=structured_output.secondary_keywords,
            key_takeaways=takeaways,
            html_content=final_html,
            labels=labels,
            faq_items=faqs,
            word_count=clean_text_count,
            source_url=article.link,
            source_name=article.source_name,
            category=article.category,
            featured_image=self._usable_featured_image(article.image_url),
        )

    def write_digest(
        self,
        articles_or_clusters: List[Any],
        target_word_count: Optional[int] = None,
        slot_info: Optional[SlotInfo] = None,
    ) -> GeneratedArticle:
        """Generates one structured digest from six to eight source stories or clusters with deterministic titles and originality layer."""
        if not 6 <= len(articles_or_clusters) <= 8:
            raise ValueError("A news digest requires between 6 and 8 source stories.")

        slot = slot_info or get_current_slot()
        target_words = target_word_count or settings.digest_target_word_count

        # Build slot-specific editorial prompt context
        if slot.slot_type == SlotType.MORNING:
            slot_editorial_instructions = (
                "Morning Slot Directive: Focus on overnight developments, breaking hardware/software releases, "
                "and what changed overnight. Highlight concrete metric shifts (battery mAh, benchmark deltas, prices)."
            )
            slot_analysis_field_description = (
                "'why_it_matters' detailing the day-ahead reader impact, and 'key_metric_or_shift' with concrete figures"
            )
            slot_originality_instructions = (
                "Provide concrete fact metric deltas (battery mAh, price delta $, benchmark score) in 'key_metric_or_shift'"
            )
        elif slot.slot_type == SlotType.MIDDAY:
            slot_editorial_instructions = (
                "Midday Slot Directive: Deep synthesis of the lead multi-source story plus supporting industry moves. "
                "Synthesize multiple corroborated viewpoints for story #1 into an authoritative breakdown."
            )
            slot_analysis_field_description = (
                "'why_it_matters' analyzing the core engineering reality and market implications"
            )
            slot_originality_instructions = (
                "Provide comparison_table_html comparing verified specifications, pricing, or benchmarks derived from the source stories"
            )
        else:  # SlotType.EVENING
            slot_editorial_instructions = (
                "Evening Slot Directive: Buyer impact, longevity, privacy implications, and DeviceRank evaluation verdicts. "
                "Analyze what readers should actually buy, upgrade, or avoid based on today's verified facts."
            )
            slot_analysis_field_description = (
                "'why_it_matters' detailing consumer/buyer implications and privacy/ecosystem tradeoffs"
            )
            slot_originality_instructions = (
                "Provide 1-3 scorecards entries evaluating the featured devices on Value, Longevity, Privacy, Repairability, and Buying Verdict"
            )

        story_blocks = []
        for index, item in enumerate(articles_or_clusters, 1):
            is_cluster = hasattr(item, "canonical_article")
            art = item.canonical_article if is_cluster else item
            sources_str = ", ".join(item.source_names) if is_cluster else art.source_name
            full_text = item.combined_full_text if is_cluster else (art.full_text or "")
            summary = item.combined_summary if is_cluster else art.summary

            story_blocks.append(
                "\n".join(
                    [
                        f'<story_cluster index="{index}">',
                        f"Corroborated sources: {escape_feed_text(sources_str)}",
                        f"Headline: {escape_feed_text(art.title)}",
                        "Published: "
                        + escape_feed_text(
                            art.published_date or art.raw_published_date or "Unknown"
                        ),
                        f"Category: {escape_feed_text(art.category)}",
                        f"Combined summaries: {escape_feed_text(summary)}",
                        f"Detailed context: {escape_feed_text((full_text or '')[:1500]) or 'Not available'}",
                        "</story_cluster>",
                    ]
                )
            )

        prompt = DIGEST_GENERATION_PROMPT.format(
            slot_display=slot.slot_display,
            story_count=len(articles_or_clusters),
            stories_context="\n\n".join(story_blocks),
            slot_editorial_instructions=slot_editorial_instructions,
            slot_analysis_field_description=slot_analysis_field_description,
            slot_originality_instructions=slot_originality_instructions,
            target_word_count=target_words,
        )

        logger.info(
            f"Generating {len(articles_or_clusters)}-story {slot.slot_display} with Gemini ({self.model_name})..."
        )
        output = self._call_gemini_structured(prompt, SEODigestOutput)
        if not isinstance(output, SEODigestOutput):
            output = SEODigestOutput.model_validate(output)

        if len(output.stories) != len(articles_or_clusters):
            raise ValueError(
                f"Gemini returned {len(output.stories)} story summaries for "
                f"{len(articles_or_clusters)} sources; refusing to publish an incomplete digest."
            )

        # Deterministic title grammar: {Topic 1}, {Topic 2} & {Topic 3} — DeviceRank {Slot} Brief
        topic_phrases = output.topic_phrases or []
        if len(topic_phrases) < 3:
            # Extract top 3 story subjects if Gemini provided fewer
            fallback_topics = [
                (c.canonical_article.title if hasattr(c, "canonical_article") else c.title).split(":")[0].split("—")[0].strip()[:20]
                for c in articles_or_clusters[:3]
            ]
            topic_phrases = (topic_phrases + fallback_topics)[:3]

        deterministic_title = build_deterministic_title(topic_phrases, slot.slot_display)
        meta_desc = output.meta_description.strip()[:155]
        takeaways = output.key_takeaways or []

        final_html = self._assemble_digest_html_content(
            articles_or_clusters=articles_or_clusters,
            title=deterministic_title,
            meta_description=meta_desc,
            story_outputs=output.stories,
            takeaways=takeaways,
            slot_info=slot,
            comparison_table_html=output.comparison_table_html,
            scorecards=output.scorecards,
        )

        clean_text_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", final_html)))

        # Standardized controlled 4 labels
        categories = list(dict.fromkeys(
            (item.canonical_article.category if hasattr(item, "canonical_article") else item.category)
            for item in articles_or_clusters
        ))
        primary_category_label = "Tech News"
        if len(categories) == 1:
            primary_category_label = (
                articles_or_clusters[0].canonical_article.blogger_label
                if hasattr(articles_or_clusters[0], "canonical_article")
                else articles_or_clusters[0].blogger_label
            )

        labels = get_standardized_labels(slot.slot_display, primary_category_label)

        # Collect all source URLs and names
        all_source_urls = []
        all_source_names = []
        for item in articles_or_clusters:
            if hasattr(item, "canonical_article"):
                all_source_urls.extend(item.source_urls)
                all_source_names.extend(item.source_names)
            else:
                all_source_urls.append(item.link)
                all_source_names.append(item.source_name)

        all_source_urls = list(dict.fromkeys(all_source_urls))
        all_source_names = list(dict.fromkeys(all_source_names))

        return GeneratedArticle(
            title=deterministic_title,
            meta_description=meta_desc,
            focus_keyword=output.focus_keyword,
            secondary_keywords=output.secondary_keywords,
            key_takeaways=takeaways,
            html_content=final_html,
            labels=labels,
            word_count=clean_text_count,
            source_url=all_source_urls[0],
            source_name=", ".join(all_source_names),
            source_urls=all_source_urls,
            source_names=all_source_names,
            category=categories[0] if len(categories) == 1 else "news_digest",
            featured_image=self._select_digest_featured_image(articles_or_clusters),
            slot_id=slot.slot_id,
        )
