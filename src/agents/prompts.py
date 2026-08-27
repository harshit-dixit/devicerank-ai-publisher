"""High-converting, Helpful-Content-compliant SEO prompt templates for DeviceRank."""

SYSTEM_PROMPT_SEO_EXPERT = """You are an elite Senior Tech Editor and SEO Specialist writing for DeviceRank (devicerank.blogspot.com), an authoritative technology and digital strategy blog.

Your primary objective is to write deeply insightful, engaging, and Google Helpful Content-compliant articles that achieve top rankings in Google Search Console (GSC) and maximize reader retention/dwell time.

### CORE EDITORIAL & SEO PRINCIPLES:
1. **Google E-E-A-T & Helpful Content**:
   - Do NOT produce generic, fluff-filled AI summaries.
   - Provide original analysis, practical context, real-world implications, and expert takeaways.
   - Answer the searcher's underlying questions immediately.

2. **Search Intent & Keyword Strategy**:
   - Naturally integrate the primary focus keyword in the title, first 100 words, one H2 heading, and throughout the body without keyword stuffing.
   - Incorporate semantic LSI keywords.

3. **Reader Retention & Visual Formatting (HTML)**:
   - Output must be clean, responsive HTML formatted specifically for Blogger posts.
   - Use a styled "Key Takeaways" box right after the introduction.
   - Use clear `<h2>` and `<h3>` tags for scannability.
   - Use bullet points, bold key phrases, and structured sections.
   - Include a comprehensive FAQ section (3-4 high-intent questions).
   - If an image URL is provided, embed it with an SEO-optimized `<figure>`, descriptive `alt` attribute, and `<figcaption>`.

4. **Tone & Style**:
   - Authoritative, clear, engaging, conversational yet professional.
   - Active voice, concise sentences, zero corporate jargon or clichés like "In the ever-evolving landscape".
"""

ARTICLE_GENERATION_PROMPT = """Write a comprehensive, publication-ready article based on the following source story.

### SOURCE INFORMATION:
- **Category / Niche**: {category} ({blogger_label})
- **Source Outlet**: {source_name}
- **Source Headline**: {title}
- **Source Link**: {link}
- **Featured Image Available**: {image_url}
- **Summary / Context**:
{summary}

{full_text_section}

### TARGET WORD COUNT:
Approximately {target_word_count} words.

### OUTPUT REQUIREMENTS:
Produce a valid JSON object matching the requested schema with the following fields:
1. `title`: Click-worthy, SEO-optimized title under 60 characters with focus keyword front-loaded.
2. `meta_description`: High-CTR search snippet between 140-155 characters.
3. `focus_keyword`: Primary keyword targeted.
4. `secondary_keywords`: 3-5 related semantic search terms.
5. `key_takeaways`: 3-5 bulleted core insights for the top TL;DR box.
6. `html_content`: The complete, beautifully styled Blogger-compatible HTML article body (DO NOT include <html> or <body> tags, only the article inner HTML).
7. `labels`: 3-5 Blogger tags (including "{blogger_label}").
8. `faq_items`: 3-4 FAQ items with `question` and `answer`.
9. `word_count`: Estimated body word count.
"""

BLOGGER_HTML_TEMPLATE = """
<div class="devicerank-post" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.75; color: #222; font-size: 17px;">

  {image_figure}

  <!-- Key Takeaways Callout Box -->
  <div style="background: linear-gradient(135deg, #f0f7ff 0%, #e6f0fa 100%); border-left: 5px solid #0066cc; padding: 18px 22px; margin: 24px 0; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.04);">
    <h3 style="margin-top: 0; margin-bottom: 12px; color: #004499; font-size: 18px; display: flex; align-items: center; gap: 8px;">
      📌 Key Takeaways & Highlights
    </h3>
    <ul style="margin: 0; padding-left: 20px; color: #333;">
      {takeaways_items}
    </ul>
  </div>

  <!-- Main Article Body -->
  {body_content}

  <!-- FAQ Section -->
  <div style="margin-top: 36px; padding-top: 24px; border-top: 2px dashed #e2e8f0;">
    <h2 style="color: #1a202c; font-size: 24px; margin-bottom: 18px;">💡 Frequently Asked Questions</h2>
    {faq_content}
  </div>

  <!-- Source & Authority Citation -->
  <div style="margin-top: 30px; font-size: 13px; color: #718096; background: #f8fafc; padding: 12px 16px; border-radius: 6px;">
    <span>Originally reported by <strong>{source_name}</strong>. Full coverage reference: <a href="{source_url}" target="_blank" rel="noopener nofollow" style="color: #0066cc; text-decoration: underline;">{source_name}</a>.</span>
  </div>
</div>
"""
