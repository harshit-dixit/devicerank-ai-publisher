"""SEO Content Generation Engine powered exclusively by Google Gemini via modern google-genai SDK.

Features typed Pydantic structured output, exponential backoff for transient errors,
HTML sanitization against allowlists, and Google Rich Snippets JSON-LD schema generation.
"""

import json
import random
import re
import time
from typing import Any, Dict, List, Optional, Type
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
from src.fetchers.rss_fetcher import RawArticle
from src.utils.logger import logger
from src.utils.sanitizer import escape_feed_text, sanitize_html, sanitize_url


# Reserved example domains are frequently used by fixtures and documentation.
# They are valid HTTPS URLs but never usable article images.
_PLACEHOLDER_IMAGE_HOSTS = {"example.com", "example.net", "example.org"}


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

    summary: str = Field(description="Factual 100-160 word summary of the supplied story")
    why_it_matters: str = Field(description="Short practical explanation of why the story matters")


class SEODigestOutput(BaseModel):
    """Typed Gemini output for a six-to-eight-story digest."""

    title: str = Field(description="SEO news-digest title between 45-65 characters")
    meta_description: str = Field(description="Meta search description between 140-155 characters")
    focus_keyword: str = Field(description="Primary target focus keyword")
    secondary_keywords: List[str] = Field(default_factory=list, description="3-5 secondary keywords")
    key_takeaways: List[str] = Field(default_factory=list, description="Exactly 3 digest-level takeaways")
    stories: List[DigestStoryOutput] = Field(min_length=6, max_length=8)
    labels: List[str] = Field(default_factory=list, description="3-5 clean taxonomy tags")
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
        """Return a safe, non-placeholder featured-image URL if one is available."""
        sanitized = sanitize_url(image_url, enforce_https=True)
        if not sanitized:
            return None

        hostname = (urlparse(sanitized).hostname or "").lower()
        if hostname in _PLACEHOLDER_IMAGE_HOSTS:
            logger.warning("Skipping placeholder featured image URL from %s", hostname)
            return None
        return sanitized

    def _select_digest_featured_image(self, articles: List[RawArticle]) -> Optional[str]:
        """Select the first usable image rather than assuming the lead story has one."""
        for article in articles:
            image_url = self._usable_featured_image(article.image_url)
            if image_url:
                return image_url
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

                # 2. Fallback to parsing text JSON into SEOArticleOutput
                raw_text = response.text or ""
                return response_schema.model_validate_json(raw_text.strip())

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
        # 1. Featured Image Figure with HTTPS validation
        image_figure = ""
        sanitized_img = self._usable_featured_image(raw_article.image_url)
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

    def _generate_digest_json_ld_schema(
        self,
        title: str,
        meta_description: str,
        articles: List[RawArticle],
    ) -> str:
        """Builds NewsArticle schema that identifies every story covered by the digest."""
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
            "about": [
                {"@type": "Thing", "name": article.title}
                for article in articles
            ],
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
        articles: List[RawArticle],
        title: str,
        meta_description: str,
        story_outputs: List[DigestStoryOutput],
        takeaways: List[str],
    ) -> str:
        """Assembles a deterministic section for every selected source story."""
        image_figure = ""
        sanitized_img = self._select_digest_featured_image(articles)
        if sanitized_img:
            image_figure = f"""  <figure style="margin: 20px 0; text-align: center;">
    <img src="{sanitized_img}" alt="DeviceRank News Digest featured image" loading="lazy" style="max-width: 100%; height: auto; border-radius: 8px;" />
    <figcaption style="font-size: 0.85rem; color: #666; margin-top: 6px;">Featured image from a source covered in this digest.</figcaption>
  </figure>"""

        takeaways_html = "\n".join(
            f'<li style="margin-bottom: 6px;">{escape_feed_text(item)}</li>'
            for item in takeaways[:3]
        )

        sections = []
        source_items = []
        for index, (article, story) in enumerate(zip(articles, story_outputs), 1):
            published = escape_feed_text(article.published_date or "Publication time unavailable")
            sections.append(
                f"""  <section class="digest-story" style="margin: 0 0 34px 0;">
    <h2 style="color: #1a202c; font-size: 24px; margin-bottom: 10px;">{index}. {escape_feed_text(article.title)}</h2>
    <p style="font-size: 13px; color: #718096; margin: 0 0 12px 0;">{escape_feed_text(article.source_name)} · {published}</p>
    <p>{escape_feed_text(story.summary)}</p>
    <p><strong>Why it matters:</strong> {escape_feed_text(story.why_it_matters)}</p>
  </section>"""
            )
            source_items.append(
                f"<li>{escape_feed_text(article.source_name)} — {escape_feed_text(article.title)}</li>"
            )

        return DIGEST_BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            takeaways_items=takeaways_html,
            story_sections="\n".join(sections),
            source_items="\n".join(source_items),
            schema_markup=self._generate_digest_json_ld_schema(
                title=title,
                meta_description=meta_description,
                articles=articles,
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
            featured_image=self._usable_featured_image(article.image_url),
        )

    def write_digest(
        self,
        articles: List[RawArticle],
        target_word_count: Optional[int] = None,
    ) -> GeneratedArticle:
        """Generates one structured digest from six to eight newest source stories."""
        if not 6 <= len(articles) <= 8:
            raise ValueError("A news digest requires between 6 and 8 source stories.")

        target_words = target_word_count or settings.digest_target_word_count
        story_blocks = []
        for index, article in enumerate(articles, 1):
            full_text = escape_feed_text((article.full_text or "")[:1200])
            story_blocks.append(
                "\n".join(
                    [
                        f'<story index="{index}">',
                        f"Source outlet: {escape_feed_text(article.source_name)}",
                        f"Headline: {escape_feed_text(article.title)}",
                        "Published: "
                        + escape_feed_text(
                            article.published_date or article.raw_published_date or "Unknown"
                        ),
                        f"Category: {escape_feed_text(article.category)}",
                        f"Feed summary: {escape_feed_text(article.summary)}",
                        f"Detailed context: {full_text or 'Not available'}",
                        "</story>",
                    ]
                )
            )

        prompt = DIGEST_GENERATION_PROMPT.format(
            story_count=len(articles),
            stories_context="\n\n".join(story_blocks),
            target_word_count=target_words,
        )
        logger.info(
            f"Generating {len(articles)}-story news digest with Gemini ({self.model_name})..."
        )
        output = self._call_gemini_structured(prompt, SEODigestOutput)
        if not isinstance(output, SEODigestOutput):
            output = SEODigestOutput.model_validate(output)
        if len(output.stories) != len(articles):
            raise ValueError(
                f"Gemini returned {len(output.stories)} story summaries for "
                f"{len(articles)} sources; refusing to publish an incomplete digest."
            )

        title = output.title.strip().strip('"').strip("'") or "Latest Technology News Digest"
        meta_desc = output.meta_description.strip()[:155]
        takeaways = output.key_takeaways or []
        final_html = self._assemble_digest_html_content(
            articles=articles,
            title=title,
            meta_description=meta_desc,
            story_outputs=output.stories,
            takeaways=takeaways,
        )
        clean_text_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", final_html)))

        labels = ["News Digest"]
        for label in [article.blogger_label for article in articles] + output.labels:
            if label and label not in labels:
                labels.append(label)

        categories = list(dict.fromkeys(article.category for article in articles))
        source_names = list(dict.fromkeys(article.source_name for article in articles))
        source_urls = list(dict.fromkeys(article.link for article in articles))

        return GeneratedArticle(
            title=title,
            meta_description=meta_desc,
            focus_keyword=output.focus_keyword,
            secondary_keywords=output.secondary_keywords,
            key_takeaways=takeaways,
            html_content=final_html,
            labels=labels[:10],
            word_count=clean_text_count,
            source_url=source_urls[0],
            source_name=", ".join(source_names),
            source_urls=source_urls,
            source_names=source_names,
            category=categories[0] if len(categories) == 1 else "news_digest",
            featured_image=self._select_digest_featured_image(articles),
        )
