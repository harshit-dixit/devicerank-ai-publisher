# ⚡ DeviceRank AI Publisher

> **Automated, SEO-Driven AI Publishing Pipeline for [devicerank.blogspot.com](https://devicerank.blogspot.com)**  
> Reviving the blog with high-ranking tech news, SEO strategy guides, gadget launches, and monetization tips compliant with Google's Helpful Content Guidelines.

---

## 🌟 Key Features

- **Multi-Niche RSS Ingestion**: Aggregates breaking stories and expert insights across *Tech News & AI*, *SEO & Search*, *Gadgets & Hardware*, and *Digital Monetization*.
- **Google E-E-A-T & Helpful Content Optimized**:
  - Front-loaded focus keyword title under 60 characters.
  - High-CTR meta description (search snippet).
  - Key Takeaways & Highlights summary callout box for maximum reader retention.
  - Clear semantic headings (`<h2>`, `<h3>`), bullet points, and responsive layout.
  - Structured FAQ section designed for Google Rich Snippets.
  - Embedded responsive `<figure>` images with keyword-rich `alt` tags and captions.
- **Deduplication Engine**: Built-in SQLite database tracks all ingested URLs and generated posts to guarantee zero duplicate content.
- **Google Blogger API v3 Integration**: Programmatically publishes directly to Blogger with labels, custom search descriptions, and Draft/Live options.
- **GitHub Actions Ready**: Run fully automated publishing cycles on a daily schedule using repository secrets.

---

## 📁 Repository Structure

```
devicerank-ai-publisher/
├── .github/
│   └── workflows/
│       └── publisher.yml          # GitHub Actions daily cron & manual trigger
├── config/
│   ├── feeds.json                 # Curated RSS sources organized by category
│   └── settings.py                # Environment configuration loader
├── src/
│   ├── main.py                    # Unified Typer CLI entrypoint
│   ├── agents/
│   │   ├── prompts.py             # E-E-A-T SEO system prompts & HTML templates
│   │   └── seo_writer.py          # Google Gemini AI generation engine
│   ├── db/
│   │   └── history.py             # SQLite deduplication & post tracker
│   ├── fetchers/
│   │   ├── rss_fetcher.py         # Multi-feed aggregator
│   │   └── content_extractor.py   # Web scraper & OpenGraph image extractor
│   ├── publishers/
│   │   ├── blogger_client.py      # Google Blogger API v3 client
│   │   └── oauth_helper.py        # Interactive OAuth token generator
│   └── utils/
│       └── logger.py              # Rich formatted console logger
├── tests/                         # Full Pytest test suite
├── .env.example                   # Template for secrets and API keys
├── requirements.txt               # Dependencies
└── README.md
```

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+
- A Google Gemini API Key (Free from [Google AI Studio](https://aistudio.google.com/))
- Blogger Blog ID (Found in your Blogger dashboard URL)

### 2. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/your-username/devicerank-ai-publisher.git
cd devicerank-ai-publisher
pip install -r requirements.txt
```

### 3. Environment Setup
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
```ini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-2.5-flash
BLOGGER_BLOG_ID=YOUR_NUMERIC_BLOG_ID
DEFAULT_PUBLISH_STATUS=DRAFT
```

---

## 🔑 Google Blogger API Setup (One-Time)

To allow the publisher to post to Blogger:

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `DeviceRank Publisher`).
3. Navigate to **APIs & Services** $\rightarrow$ **Library** $\rightarrow$ Search for **Blogger API v3** and click **Enable**.
4. Go to **APIs & Services** $\rightarrow$ **OAuth consent screen**:
   - User Type: **External**
   - Fill in App Name (e.g., `DeviceRank Publisher`) and your support email.
   - Add your Google account email under **Test users**.
5. Go to **APIs & Services** $\rightarrow$ **Credentials**:
   - Click **Create Credentials** $\rightarrow$ **OAuth client ID**.
   - Application type: **Desktop App**.
   - Name: `DeviceRank CLI`.
6. Download the OAuth credentials JSON file and save it in the root of this project as `client_secret.json`.
7. Run the interactive authorization command:
   ```bash
   python -m src.main auth
   ```
   This will open a browser window for you to sign in and grant permission. A `token.json` file will be generated automatically.

---

## 💻 CLI Usage

### View Categories & Active Feeds
```bash
python -m src.main categories
```

### Fetch & Inspect Latest News (Without generating)
```bash
# Fetch latest tech news
python -m src.main fetch --category tech_news --limit 3

# Fetch latest across all categories
python -m src.main fetch
```

### Generate SEO Article & Preview Locally
```bash
# Generate an article and save HTML preview in output/ directory
python -m src.main generate --category tech_news --save

# Generate and publish directly to Blogger as Draft
python -m src.main generate --category seo_tips --publish --draft

# Generate and publish Live
python -m src.main generate --category gadgets --publish --live
```

### Run Full Automated Pipeline
```bash
# Run one article per category and save as drafts
python -m src.main run-pipeline --draft --max 1
```

### View Publishing Stats & History
```bash
python -m src.main stats
```

---

## 🤖 GitHub Actions Automation

To run the publisher automatically on GitHub Actions:
1. In your GitHub repository, go to **Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions**.
2. Add the following repository secrets:
   - `GEMINI_API_KEY`: Your Gemini API key.
   - `BLOGGER_BLOG_ID`: Your Blogger Blog ID.
   - `BLOGGER_CLIENT_ID`: The `client_id` from your `client_secret.json`.
   - `BLOGGER_CLIENT_SECRET`: The `client_secret` from your `client_secret.json`.
   - `BLOGGER_REFRESH_TOKEN`: The `refresh_token` from your generated `token.json`.

The workflow defined in `.github/workflows/publisher.yml` will automatically run every day at **07:00 UTC** and can also be triggered manually under the **Actions** tab.

---

## 🧪 Running Tests

Run the test suite with pytest:
```bash
pytest
```
