# ⚡ DeviceRank AI Publisher

> **Automated, SEO-Driven AI Publishing Pipeline for [devicerank.blogspot.com](https://devicerank.blogspot.com)**  
> Built for Public GitHub Repositories to leverage **unlimited free GitHub Actions automation**, generating Google Helpful Content-compliant articles with structured rich schema.

---

## 🌟 Key Features

- **Multi-Niche RSS Aggregator**: Continuously tracks breaking tech news, search engine updates, gadget releases, and online monetization tactics.
- **Combined News Digests**: Selects 6–8 of the newest unprocessed stories and summarizes them in one roundup post every eight hours.
- **Google E-E-A-T & Helpful Content Optimized**:
  - Front-loaded focus keyword title (<60 chars) and high-CTR meta description (<160 chars).
  - Prominently styled **Key Takeaways & Highlights** callout box for maximum reader dwell time.
  - Semantic HTML structure (`<h2>`, `<h3>`), scannable bullet points, and responsive layout.
  - **JSON-LD Schema Markup (`FAQPage` & `TechArticle`)** embedded directly into posts for Google Rich Snippets.
  - Responsive `<figure>` images with context-rich `alt` tags and source attribution.
- **Zero-Duplicate Deduplication Engine**: SQLite-backed history tracks every ingested story and generated post.
- **Google Blogger API v3 Integration**: Automated posting with labels, custom search descriptions, and Draft/Live toggles.
- **Public GitHub Actions Automation (Unlimited Runs)**:
  - Multi-schedule daily workflows (Morning Tech Roundup, Afternoon SEO Guides, Evening Gadgets).
  - Interactive `$GITHUB_STEP_SUMMARY` reporting in Actions UI.
  - CI test suite on every PR and push.

---

## 📁 Repository Structure

```
devicerank-ai-publisher/
├── .github/
│   └── workflows/
│       ├── publisher.yml          # Multi-schedule automated publishing pipeline
│       └── ci.yml                 # Automated testing on Push/PR
├── config/
│   ├── feeds.json                 # Curated RSS sources organized by niche
│   └── settings.py                # Environment configuration loader
├── src/
│   ├── main.py                    # Unified Typer CLI entrypoint
│   ├── agents/
│   │   ├── prompts.py             # E-E-A-T SEO system prompts & HTML templates
│   │   └── seo_writer.py          # Google Gemini AI generation engine + JSON-LD Schema
│   ├── db/
│   │   └── history.py             # SQLite deduplication & post tracker
│   ├── fetchers/
│   │   ├── rss_fetcher.py         # Multi-feed aggregator
│   │   └── content_extractor.py   # Web scraper & OpenGraph image extractor
│   ├── publishers/
│   │   ├── blogger_client.py      # Google Blogger API v3 client
│   │   └── oauth_helper.py        # Interactive OAuth token generator & secrets exporter
│   └── utils/
│       └── logger.py              # Rich formatted console logger
├── tests/                         # Full Pytest test suite
├── .env.example                   # Template for secrets and API keys
├── requirements.txt               # Dependencies
└── README.md
```

---

## 🚀 Quickstart & Local Setup

### 1. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/your-username/devicerank-ai-publisher.git
cd devicerank-ai-publisher
python --version  # Python 3.14.7
pip install -r requirements.txt
```

### 2. Environment Setup
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
```ini
GEMINI_API_KEY=your_gemini_api_key_from_google_ai_studio
GEMINI_MODEL=gemini-3.5-flash
BLOGGER_BLOG_ID=your_numeric_blogger_blog_id
DEFAULT_PUBLISH_STATUS=DRAFT
```

---

## 🔑 Google Blogger API Setup (One-Time)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g., `DeviceRank Publisher`).
3. Navigate to **APIs & Services** $\rightarrow$ **Library** $\rightarrow$ Search for **Blogger API v3** and click **Enable**.
4. Go to **APIs & Services** $\rightarrow$ **OAuth consent screen**:
   - User Type: **External**
   - Fill in App Name and your support email.
   - Add your Google account email under **Test users**.
5. Go to **APIs & Services** $\rightarrow$ **Credentials**:
   - Click **Create Credentials** $\rightarrow$ **OAuth client ID**.
   - Application type: **Desktop App**.
   - Name: `DeviceRank CLI`.
6. Download the OAuth credentials JSON file and save it in the project root as `client_secret.json`.
7. Run the interactive authorization command:
   ```bash
   python -m src.main auth
   ```
   This will open a browser window to grant permission and generate `token.json`.

---

## 💻 CLI Commands

### View Configured Categories & Feeds
```bash
python -m src.main categories
```

### Fetch & Inspect Latest Trending Articles
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
# Publish one article per category as a draft (legacy single-story mode)
python -m src.main run-pipeline --draft --max 1

# Combine the latest 8 stories across all categories into one draft
python -m src.main run-digest --stories 8 --draft

# Combine 6 latest tech-news stories and publish the digest live
python -m src.main run-digest --category tech_news --stories 6 --live
```

The digest command accepts 6–8 stories. If fewer than six unprocessed stories are available, it skips the run instead of publishing an incomplete or repeated roundup.

### Export Secrets for GitHub Actions
```bash
python -m src.main export-secrets
```

### View Publishing Stats & History
```bash
python -m src.main stats
```

---

## 🤖 GitHub Actions Automation (Public Repo Setup)

Because this is a **public GitHub repository**, you have **unlimited free GitHub Actions execution**.

### 1. Add Repository Secrets
Run the following locally:
```bash
python -m src.main export-secrets
```
Then navigate to your GitHub Repository:  
**Settings** $\rightarrow$ **Secrets and variables** $\rightarrow$ **Actions** $\rightarrow$ **New repository secret**:

| Secret Name | Description | Source |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API Key | [Google AI Studio](https://aistudio.google.com/) |
| `BLOGGER_BLOG_ID` | Numeric Blogger Blog ID | Blogger Dashboard URL |
| `BLOGGER_CLIENT_ID` | OAuth Client ID | `client_secret.json` |
| `BLOGGER_CLIENT_SECRET` | OAuth Client Secret | `client_secret.json` |
| `BLOGGER_REFRESH_TOKEN` | OAuth Refresh Token | `token.json` |

### 2. Automated Publishing Schedule
The workflow `.github/workflows/publisher.yml` runs at **00:00, 08:00, and 16:00 UTC** (05:30, 13:30, and 21:30 IST). Each scheduled run aggregates the newest unprocessed stories across all configured categories and publishes one live digest containing up to eight stories.

Manual workflow runs default to drafts and can optionally filter to one category or choose any story count from six through eight.

You can also trigger manual runs anytime under the **Actions** tab with custom categories and draft/live toggles.

---

## 🧪 Testing

Run the test suite with pytest:
```bash
pytest
```
