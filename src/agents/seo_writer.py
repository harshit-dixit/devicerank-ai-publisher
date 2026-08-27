"""SEO Content Generation Engine powered by Google Gemini."""

import json
import re
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from config.settings import settings
from src.agents.prompts import (
    ARTICLE_GENERATION_PROMPT,
    BLOGGER_HTML_TEMPLATE,
    SYSTEM_PROMPT_SEO_EXPERT,
)
from src.fetchers.rss_fetcher import RawArticle
from src.utils.logger import logger


class FAQItem(BaseModel):
    question: str
    answer: str


class GeneratedArticle(BaseModel):
    """Structured SEO Article ready for Blogger publishing."""

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
    """Orchestrates LLM generation, E-E-A-T formatting, and HTML assembly."""

    def __init__(self, api_key: Optional[str] = None, model_name: Optional[str] = None):
        self.api_key = api_key or settings.gemini_api_key
        self.model_name = model_name or settings.gemini_model

    def _call_gemini(self, prompt: str) -> str:
        """Invokes Gemini API via google-genai or google-generativeai."""
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set it in your .env file or pass it to SEOWriter."
            )

        # Try modern google-genai SDK first
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT_SEO_EXPERT,
                    response_mime_type="application/json",
                    temperature=0.7,
                ),
            )
            return response.text
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"google-genai client attempt: {e}. Falling back to google.generativeai...")

        # Fallback to google-generativeai SDK
        try:
            import google.generativeai as gai

            gai.configure(api_key=self.api_key)
            model = gai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=SYSTEM_PROMPT_SEO_EXPERT,
                generation_config={"response_mime_type": "application/json", "temperature": 0.7},
            )
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            logger.error(f"Failed to generate content with Gemini: {e}")
            raise

    def _clean_json_output(self, raw_text: str) -> Dict:
        """Parses JSON from LLM response safely."""
        text = raw_text.strip()
        # Remove markdown code block if present
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON from Gemini output: {e}\nRaw text: {text[:300]}")
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise

    def _generate_json_ld_schema(
        self,
        title: str,
        meta_description: str,
        raw_article: RawArticle,
        faqs: List[Dict[str, str]],
    ) -> str:
        """Generates Google Rich Snippet JSON-LD Structured Data."""
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
            article_schema["image"] = [raw_article.image_url]
        schemas.append(article_schema)

        # 2. FAQPage Schema
        if faqs:
            faq_schema = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": f.get("question", ""),
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": f.get("answer", ""),
                        },
                    }
                    for f in faqs
                    if f.get("question") and f.get("answer")
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
        faqs: List[Dict[str, str]],
    ) -> str:
        """Injects images, callouts, FAQs, JSON-LD Schema, and source links into the post template."""
        # 1. Image Figure
        image_figure = ""
        if raw_article.image_url:
            alt_text = f"{raw_article.title} - DeviceRank"
            image_figure = f"""
  <figure style="margin: 0 0 24px 0; text-align: center;">
    <img src="{raw_article.image_url}" alt="{alt_text}" loading="lazy" style="max-width: 100%; height: auto; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.08);" />
    <figcaption style="font-size: 13px; color: #64748b; margin-top: 8px; font-style: italic;">Featured Image: {raw_article.source_name}</figcaption>
  </figure>"""

        # 2. Takeaways
        takeaways_html = "\n".join(f'<li style="margin-bottom: 6px;">{t}</li>' for t in takeaways)

        # 3. FAQ Content
        faq_html_list = []
        for faq in faqs:
            q = faq.get("question", "")
            a = faq.get("answer", "")
            faq_html_list.append(
                f"""
    <div style="margin-bottom: 16px; background: #f8fafc; padding: 14px 18px; border-radius: 6px; border: 1px solid #e2e8f0;">
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

        return BLOGGER_HTML_TEMPLATE.format(
            image_figure=image_figure,
            takeaways_items=takeaways_html,
            body_content=body_content,
            faq_content=faq_content,
            source_name=raw_article.source_name,
            source_url=raw_article.link,
            schema_markup=schema_markup,
        )

    def write_article(
        self,
        article: RawArticle,
        target_word_count: Optional[int] = None,
    ) -> GeneratedArticle:
        """Generates an SEO-optimized post from a raw article."""
        target_words = target_word_count or settings.target_word_count

        full_text_section = ""
        if article.full_text:
            full_text_section = f"### DETAILED CONTEXT:\n{article.full_text[:3000]}"

        prompt = ARTICLE_GENERATION_PROMPT.format(
            category=article.category,
            blogger_label=article.blogger_label,
            source_name=article.source_name,
            title=article.title,
            link=article.link,
            image_url=article.image_url or "None",
            summary=article.summary,
            full_text_section=full_text_section,
            target_word_count=target_words,
        )

        logger.info(f"Generating SEO article with Gemini ({self.model_name})...")
        raw_response = self._call_gemini(prompt)
        data = self._clean_json_output(raw_response)

        # Parse FAQs
        faqs_data = data.get("faq_items", [])
        faqs = [FAQItem(**item) if isinstance(item, dict) else FAQItem(question=str(item), answer="") for item in faqs_data]

        # Extract takeaways
        takeaways = data.get("key_takeaways", [])

        title = data.get("title", article.title)
        meta_desc = data.get("meta_description", "")[:160]

        # Build complete Blogger-ready HTML with JSON-LD
        raw_body_html = data.get("html_content", "")
        final_html = self._assemble_html_content(
            raw_article=article,
            title=title,
            meta_description=meta_desc,
            body_content=raw_body_html,
            takeaways=takeaways,
            faqs=[f.model_dump() for f in faqs],
        )

        # Calculate word count
        clean_text_count = len(re.findall(r"\w+", re.sub(r"<[^>]+>", " ", final_html)))

        # Construct labels
        labels = data.get("labels", [])
        if article.blogger_label not in labels:
            labels.insert(0, article.blogger_label)

        return GeneratedArticle(
            title=title,
            meta_description=meta_desc,
            focus_keyword=data.get("focus_keyword", ""),
            secondary_keywords=data.get("secondary_keywords", []),
            key_takeaways=takeaways,
            html_content=final_html,
            labels=labels,
            faq_items=faqs,
            word_count=clean_text_count,
            source_url=article.link,
            source_name=article.source_name,
            category=article.category,
            featured_image=article.image_url,
        )
