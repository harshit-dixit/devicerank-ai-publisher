"""SEO Content Generation Engine powered exclusively by Google Gemini via modern google-genai SDK.

Features typed Pydantic structured output, exponential backoff for transient errors,
HTML sanitization against allowlists, and Google Rich Snippets JSON-LD schema generation.
"""

import json
import random
import re
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from config.settings import settings
from src.agents.prompts import (
    ARTICLE_GENERATION_PROMPT,
    BLOGGER_HTML_TEMPLATE,
    SEO_SYSTEM_PROMPT,
)
from src.db.history import history_db
from src.fetchers.rss_fetcher import RawArticle
from src.utils.logger import logger
from src.utils.sanitizer import escape_feed_text, sanitize_html, sanitize_url


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
    category: Optional[str] = None
    featured_image: Optional[str] = None


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

    def _call_gemini_structured(self, prompt: str) -> SEOArticleOutput:
        """Invokes Gemini API with typed Pydantic structured output and exponential backoff for transient errors."""
        client = self._get_genai_client()
        from google.genai import types, errors

        config = types.GenerateContentConfig(
            system_instruction=SEO_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=SEOArticleOutput,
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
                    if isinstance(response.parsed, SEOArticleOutput):
                        return response.parsed
                    if isinstance(response.parsed, dict):
                        return SEOArticleOutput.model_validate(response.parsed)

                # 2. Fallback to parsing text JSON into SEOArticleOutput
                raw_text = response.text or ""
                return SEOArticleOutput.model_validate_json(raw_text.strip())

            except errors.APIError as e:
                # Check for retryable HTTP status codes: 429, 500, 502, 503, 504
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
                # Retry transient network exceptions
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
        if raw_article.image_url:
            sanitized_img = sanitize_url(raw_article.image_url, enforce_https=True)
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
        # 1. Featured Image Figure with HTTPS validation
        image_figure = ""
        sanitized_img = sanitize_url(raw_article.image_url, enforce_https=True)
        if sanitized_img:
            alt_text = f"{escape_feed_text(raw_article.title)} - DeviceRank Tech Analysis"
            image_figure = f"""  <figure style="margin: 20px 0; text-align: center;">
    <img src="{sanitized_img}" alt="{alt_text}" loading="lazy" style="max-width: 100%; height: auto; border-radius: 8px;" />
    <figcaption style="font-size: 0.85rem; color: #666; margin-top: 6px;">Featured Image: {escape_feed_text(raw_article.source_name)}</figcaption>
  </figure>"""

        # 2. Key Takeaways Callout Items
        takeaways_html = "\n".join(
            f'<li style="margin-bottom: 6px;">{escape_feed_text(t)}</li>'
            for t in takeaways[:3]
        )

        # 3. FAQ Content
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

        # 4. JSON-LD Schema
        schema_markup = self._generate_json_ld_schema(
            title=title,
            meta_description=meta_description,
            raw_article=raw_article,
            faqs=faqs,
        )

        # 5. Sanitize LLM body HTML
        sanitized_body = sanitize_html(body_content, enforce_zero_outbound_links=True)

        assembled = BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            takeaways_items=takeaways_html,
            body_content=sanitized_body,
            faq_content=faq_content,
            source_name=escape_feed_text(raw_article.source_name),
            schema_markup=schema_markup,
        )

        return assembled

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

        # Retrieve relevant past published posts from SQLite for internal linking
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

        # Normalize and validate title and meta description
        title = structured_output.title.strip().strip('"').strip("'")
        if not title:
            title = article.title

        meta_desc = structured_output.meta_description.strip()[:155]

        # Extract FAQs
        faqs = structured_output.faq_items or []

        # Extract Takeaways
        takeaways = structured_output.key_takeaways or []

        # Assemble full HTML
        final_html = self._assemble_html_content(
            raw_article=article,
            title=title,
            meta_description=meta_desc,
            body_content=structured_output.html_content,
            takeaways=takeaways,
            faqs=faqs,
        )

        # Calculate word count accurately
        clean_text_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", final_html)))

        # Construct labels
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
            featured_image=sanitize_url(article.image_url, enforce_https=True),
        )
