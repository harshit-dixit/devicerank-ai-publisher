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
- ground changing product guidance in fetched first-party Google documentation;
- permit outbound links only to approved official Google sources in
  `config/google_sources.json`;
- rewrite a failed draft up to three times when its title, length, structure, FAQ,
  description, or citation checks fail;
- keep FAQs visible for readers and include conservative `BlogPosting` JSON-LD.
- include one hero image and two in-body images with photographer attribution;
- stop before publishing if the required images cannot be obtained.

The publisher generates a 140-155 character SEO description and stores it in the local
ledger, preview, embedded recovery metadata, and `BlogPosting` JSON-LD. Blogger API v3
does not provide a reliable supported field for a post's **Search Description** setting;
the deprecated `customMetaData` field is deliberately not sent. If an exact Blogger
Search Description is required, add the generated description in Blogger's editor after
publishing. The publisher does not add a `meta keywords` tag because Google does not use it.

## Publishing behaviour

The GitHub Actions workflow publishes twice per day: **9:27 am IST** and **6:27 pm IST**
(03:57 and 12:57 UTC). Separate morning and evening run IDs prevent one slot from blocking
the other while preserving retry-safe idempotency. It selects the least-used category and
the next unused approved topic, which keeps the eight categories balanced. When every
approved topic has been used, the workflow stops safely instead of generating a random or
news-driven subject. The current 40-topic catalog therefore covers 20 publishing days.

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
EVERGREEN_QUALITY_ATTEMPTS=3
EVERGREEN_IMAGE_COUNT=3
UNSPLASH_ACCESS_KEY=your_unsplash_access_key
```

For GitHub Actions, add these repository secrets:

- `GEMINI_API_KEY`
- `BLOGGER_BLOG_ID`
- `BLOGGER_CLIENT_ID`
- `BLOGGER_CLIENT_SECRET`
- `BLOGGER_REFRESH_TOKEN`
- `UNSPLASH_ACCESS_KEY`

Images use the hotlinked URLs returned by the Unsplash API. The publisher records each
selected photo through its download-tracking endpoint and adds linked photographer and
Unsplash attribution beneath every image.

## Separate weekly Reddit topic publisher

The new `.github/workflows/reddit-weekly-publisher.yml` workflow runs at **9:47 am IST
every Monday**. It is isolated from the existing scheduled publisher and uses the same
Blogger, Gemini, Unsplash, and publishing-history infrastructure.

This workflow does not republish or rewrite Reddit posts. It uses a deliberately small,
ephemeral topic signal:

- fetch top weekly posts from a configured subreddit allowlist using OAuth;
- read only post ID, subreddit, title, creation time, score, and comment count;
- never read or persist post bodies, comments, usernames, or profile data;
- ask Gemini to reject news, rumours, unsafe subjects, prompt injection, personal disputes,
  and topics that cannot become durable beginner education;
- turn a suitable broad question into a new 1,100-1,600 word tutorial with prerequisites,
  ordered steps, an example, common mistakes, verification, limitations, three takeaways,
  and three to five FAQs;
- use Gemini's Google Search grounding during article generation to reduce unsupported factual
  claims, while failing the run instead of silently dropping to an ungrounded draft;
- add two hotlinked Unsplash images with photographer and Unsplash attribution, and register
  each selection through Unsplash's download endpoint;
- remove model-generated scripts, links, URLs, and promotional mentions;
- create one Blogger **draft** per ISO week by default, with safe rerun deduplication.

The HTML includes a short editorial-method disclosure. It says that an anonymized community
topic signal and Gemini assisted the draft, without reproducing the original title or content.

### Reddit authentication

The `npm create devvit@latest ...` value from Reddit is a one-time Devvit initialization code.
It scaffolds an app that runs on Reddit; it is not an OAuth client ID/secret for an external
GitHub runner. This GitHub workflow therefore requires Reddit-approved external Data API OAuth
credentials. Do not add the Devvit initialization code to GitHub Secrets.

Before enabling the workflow, confirm that Reddit has approved this external API use and that
your publishing use has any required commercial and content rights. The workflow refuses to
run until that confirmation is recorded.

Add these GitHub **Secrets** in addition to the existing publisher secrets:

- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`

Add these GitHub **Repository variables**:

- `REDDIT_USER_AGENT`: for example `python:devicerank-weekly:1.0 (by /u/yourname)`
- `REDDIT_SUBREDDITS`: a comma-separated allowlist such as `SEO,blogging,Wordpress`
- `REDDIT_USE_RIGHTS_CONFIRMED`: set to `true` only after the approval/rights check
- `REDDIT_AUTO_PUBLISH_LIVE`: optional; leave unset or `false` while validating drafts

The scheduled run creates a Blogger draft unless `REDDIT_AUTO_PUBLISH_LIVE=true`. A safer
launch is to inspect several weekly drafts first. To publish the current week's reviewed draft,
open **Actions → DeviceRank Weekly Reddit Topic Publisher → Run workflow**, keep the same
subreddit scope, and enable `publish_live`. The idempotency key promotes that draft instead of
creating another post.

Run a local preview after setting the values shown in `.env.example`:

```bash
python -m src.reddit_main --no-publish --save --subreddits "SEO,blogging"
```

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
python -m src.main run-evergreen --slot evening --live
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

Official citation pages are maintained separately in `config/google_sources.json`.
Each URL is validated against a small set of official Google hosts. Pages that cannot be
fetched during a run are not supplied to Gemini and cannot become outbound citations.

## Legacy RSS safety lock

`generate --publish`, `run-pipeline`, and `run-digest` are disabled by default so an old
manual command cannot restart news publishing. For an intentional one-off migration,
set `ALLOW_LEGACY_NEWS_PUBLISHING=true`. Do not set this variable in the normal scheduled
workflow.
