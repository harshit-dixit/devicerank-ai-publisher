"""HTML, URL, and Content Sanitization utilities for DeviceRank AI Publisher."""

import html
import re
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag

ALLOWED_TAGS = {
    "h2",
    "h3",
    "h4",
    "p",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "strong",
    "b",
    "em",
    "i",
    "blockquote",
    "code",
    "pre",
    "figure",
    "figcaption",
    "img",
    "div",
    "span",
    "a",
    "br",
    "hr",
}

DISALLOWED_TAGS = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "applet",
    "form",
    "input",
    "button",
    "textarea",
    "select",
    "svg",
    "meta",
    "link",
}

ALLOWED_ATTRS = {
    "img": {"src", "alt", "loading", "style", "width", "height", "class"},
    "a": {"href", "title", "target", "rel", "class", "style"},
    "figure": {"style", "class"},
    "figcaption": {"style", "class"},
    "table": {"style", "class", "border", "cellpadding", "cellspacing"},
    "thead": {"style", "class"},
    "tbody": {"style", "class"},
    "tr": {"style", "class"},
    "th": {"style", "class", "scope", "colspan", "rowspan"},
    "td": {"style", "class", "colspan", "rowspan"},
    "div": {"style", "class"},
    "span": {"style", "class"},
    "p": {"style", "class"},
    "ul": {"style", "class"},
    "ol": {"style", "class"},
    "li": {"style", "class"},
    "h2": {"style", "class"},
    "h3": {"style", "class"},
    "h4": {"style", "class"},
    "blockquote": {"style", "class"},
    "code": {"style", "class"},
    "pre": {"style", "class"},
}


def sanitize_url(url: Optional[str], enforce_https: bool = True) -> Optional[str]:
    """Validates and sanitizes a URL, ensuring safe protocol schemes.

    If enforce_https is True, only https:// schemes are permitted.
    """
    if not url:
        return None

    cleaned = str(url).strip()
    if not cleaned:
        return None

    try:
        parsed = urlparse(cleaned)
        scheme = parsed.scheme.lower()

        if enforce_https:
            if scheme == "http":
                # Upgrade to https if possible
                cleaned = "https://" + cleaned[7:]
                parsed = urlparse(cleaned)
                scheme = parsed.scheme.lower()

            if scheme != "https":
                return None
        else:
            if scheme not in ("http", "https"):
                return None

        if not parsed.netloc:
            return None

        return cleaned
    except Exception:
        return None


def escape_feed_text(text: Optional[str]) -> str:
    """Escapes raw feed-provided text to prevent HTML injection when used in templates."""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def sanitize_html(html_content: str, enforce_zero_outbound_links: bool = True) -> str:
    """Sanitizes HTML against an allowlist of tags and attributes.

    - Removes dangerous elements (script, iframe, etc.)
    - Removes JS event handlers (onclick, onerror, etc.)
    - Enforces https for images
    - Enforces zero outbound links policy by converting external <a> tags to bold text.
    """
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Decompose dangerous tags
    for dangerous in soup.find_all(list(DISALLOWED_TAGS)):
        dangerous.decompose()

    # 2. Iterate all tags in reverse order to sanitize attributes and elements
    for tag in soup.find_all(True):
        tag_name = tag.name.lower()

        if tag_name not in ALLOWED_TAGS:
            tag.unwrap()
            continue

        # Sanitize attributes
        allowed_tag_attrs = ALLOWED_ATTRS.get(tag_name, set())
        attrs_to_remove = []

        for attr_name, attr_val in list(tag.attrs.items()):
            attr_lower = attr_name.lower()

            # Strip any javascript event handlers (on*)
            if attr_lower.startswith("on"):
                attrs_to_remove.append(attr_name)
                continue

            # Strip attributes not in allowlist
            if attr_lower not in allowed_tag_attrs:
                attrs_to_remove.append(attr_name)
                continue

            # Check style attributes for javascript: or expressions
            if attr_lower == "style":
                val_str = str(attr_val).lower()
                if "javascript:" in val_str or "expression(" in val_str or "behavior:" in val_str:
                    attrs_to_remove.append(attr_name)

        for attr in attrs_to_remove:
            del tag.attrs[attr]

        # Process <img> tags
        if tag_name == "img":
            src = tag.get("src")
            valid_src = sanitize_url(src, enforce_https=True)
            if not valid_src:
                tag.decompose()
                continue
            tag["src"] = valid_src
            if not tag.get("loading"):
                tag["loading"] = "lazy"

        # Process <a> tags
        if tag_name == "a":
            href = tag.get("href", "")
            valid_href = str(href).strip() if href else ""

            is_internal = (
                "devicerank.blogspot.com" in valid_href.lower()
                or valid_href.startswith("/")
                or valid_href.startswith("#")
            )

            if enforce_zero_outbound_links and not is_internal:
                # Convert external link to strong tag (bold attribution)
                anchor_text = tag.get_text().strip()
                if anchor_text:
                    strong_tag = soup.new_tag("strong")
                    strong_tag.string = anchor_text
                    tag.replace_with(strong_tag)
                else:
                    tag.decompose()
            else:
                # Keep safe internal link
                if valid_href:
                    tag["rel"] = "noopener"

    # Return string representation of sanitized body
    return "".join(str(child) for child in soup.contents)


def clean_html_fragment(html_content: str) -> str:
    """Sanitizes an HTML fragment using the standard allowlist."""
    return sanitize_html(html_content)


def remove_all_anchor_tags(html_content: str) -> str:
    """Replaces all <a> anchor tags with their inner text or bold text."""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    for a in soup.find_all("a"):
        text = a.get_text().strip()
        if text:
            strong = soup.new_tag("strong")
            strong.string = text
            a.replace_with(strong)
        else:
            a.decompose()
    return "".join(str(c) for c in soup.contents)


def sanitize_title(title: Optional[str]) -> str:
    """Cleans and sanitizes post titles."""
    if not title:
        return "DeviceRank Technology Update"
    cleaned = strip_html(str(title)).strip()
    return re.sub(r"\s+", " ", cleaned)


def strip_html(text: Optional[str]) -> str:
    """Removes all HTML tags and returns plain text."""
    if not text:
        return ""
    soup = BeautifulSoup(str(text), "html.parser")
    return soup.get_text(separator=" ").strip()


def generate_json_ld_schema(
    title: str,
    meta_description: str,
    canonical_url: Optional[str],
    author_name: str = "DeviceRank Editorial Team",
    publisher_name: str = "DeviceRank",
    faq_items: Optional[list] = None,
    article_type: str = "BlogPosting",
    image_url: Optional[str] = None,
    word_count: Optional[int] = None,
    include_faq_schema: bool = False,
) -> str:
    """Generate conservative Article JSON-LD using Google-supported properties."""
    import json
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()

    article_schema = {
            "@type": article_type,
            "headline": title,
            "description": meta_description,
            "datePublished": now_iso,
            "dateModified": now_iso,
            "inLanguage": "en-IN",
            "author": {
                "@type": "Organization",
                "name": author_name,
                "url": "https://devicerank.blogspot.com",
            },
            "publisher": {
                "@type": "Organization",
                "name": publisher_name,
                "url": "https://devicerank.blogspot.com",
            },
        }
    safe_canonical_url = sanitize_url(canonical_url, enforce_https=True)
    if safe_canonical_url:
        article_schema["url"] = safe_canonical_url
        article_schema["mainEntityOfPage"] = safe_canonical_url
    safe_image_url = sanitize_url(image_url, enforce_https=True)
    if safe_image_url:
        article_schema["image"] = [safe_image_url]
    if word_count and word_count > 0:
        article_schema["wordCount"] = word_count

    schema_graph = [article_schema]

    if include_faq_schema and faq_items:
        schema_graph.append({
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item.get("question") if isinstance(item, dict) else getattr(item, "question", ""),
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": item.get("answer") if isinstance(item, dict) else getattr(item, "answer", ""),
                    },
                }
                for item in faq_items
            ],
        })

    full_schema = {
        "@context": "https://schema.org",
        "@graph": schema_graph,
    }

    return f"<script type=\"application/ld+json\">\n{json.dumps(full_schema, indent=2)}\n</script>"
