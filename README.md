# DeviceRank Evergreen AI Publisher

Automated, SEO-focused tutorial publishing for
[devicerank.blogspot.com](https://devicerank.blogspot.com).

The scheduled workflow publishes evergreen educational content instead of news,
announcements, launch coverage, or multi-story digests.

## Approved content scope

Only these categories are available to the evergreen workflow:

- SEO Tips
- AdSense Tips
- Digital Marketing Tips
- Blogging Tips
- WordPress Tips
- Shopify Tips
- Google Search Console Tips
- Google Analytics 4 Tips

The curated library in `config/evergreen_topics.json` contains 40 teaching titles.
Every title promises a task, solution, checklist, or skill. Titles containing years,
breaking-news language, announcements, or roundup language fail catalog validation.

## Editorial and SEO safeguards

Each generated tutorial must:

- use the approved educational title exactly;
- contain a 1,400-2,000 word target body and pass the configured minimum word count;
- answer the search intent early and use a clear H2/H3 hierarchy;
- include prerequisites, ordered steps, an illustrative example, common mistakes,
  verification steps, limitations, and a practical next action;
- provide exactly three takeaways and three to five FAQs;
- include a 140-155 character meta description and natural secondary keywords;
- use simple English in the style of a patient Indian YouTube educator;
- avoid fake hands-on claims, invented results, unsupported statistics, and made-up
  screenshots or interface labels;
- add only trusted DeviceRank internal links selected from relevant published posts;
- include `TechArticle` and `FAQPage` JSON-LD.

Blogger receives the meta description through its post metadata field. The publisher
does not add a `meta keywords` tag because Google does not use it for rankings.

## Publishing behaviour

The GitHub Actions workflow runs once each day, with a second idempotent retry twenty
minutes later. It selects the least-used category and the next unused approved topic,
which keeps the eight categories balanced. A stable topic ID and daily run ID prevent
duplicates. When every approved topic has been used, the workflow stops safely instead
of generating a random or news-driven subject.

Existing RSS commands remain available for research and migration, but RSS/news
publishing is locked by default. The scheduled workflow calls only `run-evergreen`.

## Setup

Install dependencies and create the local environment file:

```bash
pip install -r requirements.txt
cp .env.example .env
```

Required values:

```ini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash
BLOGGER_BLOG_ID=your_numeric_blog_id
DEFAULT_PUBLISH_STATUS=DRAFT
EVERGREEN_MIN_WORD_COUNT=1200
```

For GitHub Actions, add these repository secrets:

- `GEMINI_API_KEY`
- `BLOGGER_BLOG_ID`
- `BLOGGER_CLIENT_ID`
- `BLOGGER_CLIENT_SECRET`
- `BLOGGER_REFRESH_TOKEN`

Run Blogger OAuth locally when credential files are used:

```bash
python -m src.main auth
```

## Commands

List approved topics and see which ones have already been used:

```bash
python -m src.main evergreen-topics
python -m src.main evergreen-topics --category wordpress_tips
```

Generate the next balanced topic as a local HTML preview:

```bash
python -m src.main run-evergreen --no-publish --save
```

Create a Blogger draft:

```bash
python -m src.main run-evergreen --publish --draft
```

Publish the next approved topic live:

```bash
python -m src.main run-evergreen --publish --live
```

Choose a category or exact approved topic:

```bash
python -m src.main run-evergreen --category ga4_tips --draft
python -m src.main run-evergreen --topic-id gsc-submit-sitemap --draft
```

## Adding future topics

Add a new entry to `config/evergreen_topics.json` with a unique ID, educational title,
primary keyword, search intent, reader problem, practical outcome, and four to eight
required subject areas. Run the tests before publishing:

```bash
pytest
```

Catalog validation rejects duplicate IDs, dated titles, news language, and titles that
do not teach or solve a task.

## Legacy RSS safety lock

`generate --publish`, `run-pipeline`, and `run-digest` are disabled by default so an old
manual command cannot restart news publishing. For an intentional one-off migration,
set `ALLOW_LEGACY_NEWS_PUBLISHING=true`. Do not set this variable in the normal scheduled
workflow.
