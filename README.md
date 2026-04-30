# AI Digest

AI Digest is a website-first AI industry briefing with Telegram delivery.

The product collects AI updates from curated sources, filters them down to the most useful stories, uses Gemini to write a clear digest, publishes the full version on a website, and sends a shorter version through Telegram.

## Architecture

AI Digest is split into a few simple parts:

```txt
Sources
  -> Fetching
  -> Deduping and filtering
  -> Story selection
  -> Gemini writing
  -> Storage
  -> Website + Telegram
```

### 1. Sources

The system starts with curated AI sources:

- AI labs and model companies
- developer tools and framework updates
- research feeds
- serious AI and tech reporting
- selected policy or infrastructure sources

The goal is not to let an agent browse the whole internet freely. The system begins from known sources so the digest stays reliable and token-efficient.

### 2. Fetching

Code fetches RSS feeds, source pages, and structured links.

This part does not need an LLM. It is normal software work:

- collect items
- parse titles, links, dates, summaries
- remove broken or stale entries
- keep source metadata

### 3. Deduping And Filtering

Many sites cover the same story. The code removes obvious duplicates and filters noisy items before Gemini sees anything.

This keeps cost down and improves quality because the model receives a smaller, cleaner set of stories instead of raw internet noise.

### 4. Story Selection

The pipeline narrows the feed into:

- 5 top stories
- 3 smaller notes

The system also tries to balance the digest so one company or one source does not dominate unless the day genuinely demands it.

### 5. Gemini Writing

Gemini is used for judgment and writing, not for uncontrolled browsing.

The model receives a compressed shortlist and writes:

- a full website digest
- a compact Telegram version

The website is the canonical read. Telegram is the delivery layer.

### 6. Storage

The app stores:

- final digest markdown
- Telegram message text
- issue metadata
- subscriber records

Locally this lives in `data/`. In cloud deployment, Supabase can store subscribers and digests so the app survives restarts.

### 7. Website

The website shows:

- the latest full digest
- source links
- archive
- Telegram join link

The website is intentionally a reading product, not a dashboard. It is meant for the full version of each issue.

### 8. Telegram

Telegram sends a shorter version of the same issue.

Each Telegram message includes:

- title
- what happened today
- 5 top stories
- 3 smaller notes
- link to the full website issue

Users subscribe with `/start` and unsubscribe with `/stop`.

## Editions

The three public editions use neutral names:

- `Edition 1`
- `Edition 2`
- `Edition 3`

They are scheduled at:

- `02:30 UTC`
- `10:00 UTC`
- `19:00 UTC`

Neutral names avoid confusing users in different countries.

## How A Run Works

When a scheduled run starts:

1. The app fetches all configured sources.
2. Code removes stale, duplicate, and weak items.
3. Code creates a compact shortlist.
4. Gemini analyzes the shortlist.
5. The final top stories and smaller notes are selected.
6. Gemini writes the website digest.
7. The Telegram message is generated from the same final story list.
8. The issue is saved.
9. The website archive updates.
10. Telegram sends the compact issue to active subscribers.

The important design choice is that the website and Telegram use the same final story selection. They differ in depth, not in what they cover.

## Why This Is Token Efficient

The expensive work is not given to the LLM.

Code handles:

- fetching
- parsing
- deduping
- source balancing
- recency checks
- storage
- scheduling
- delivery

Gemini handles:

- judging what matters
- summarizing the day
- writing the issue
- turning the full issue into a Telegram-friendly brief

This keeps model usage small while preserving quality.

## Heroku Shape

On Heroku, the app runs as:

- `web` dyno: hosts the website and receives Telegram bot commands through a webhook
- Heroku Scheduler: triggers the three digest editions
- Supabase: stores subscribers and digests

The `Procfile` defines:

```txt
web: python -m ai_digest serve --host 0.0.0.0 --port $PORT
```

There is no always-on Telegram worker in the default Heroku setup. This keeps the app to one paid dyno. Telegram commands such as `/start`, `/stop`, and `/latest` are handled by the web dyno through `/telegram/webhook`.

Set these Heroku config vars:

```txt
GEMINI_API_KEY
AI_DIGEST_TELEGRAM_BOT_TOKEN
AI_DIGEST_TELEGRAM_BOT_USERNAME
AI_DIGEST_TELEGRAM_WEBHOOK_SECRET
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
AI_DIGEST_PUBLIC_BASE_URL
```

`AI_DIGEST_PUBLIC_BASE_URL` should be your Heroku app URL, for example:

```txt
https://your-app-name.herokuapp.com
```

After the app is deployed, register the Telegram webhook once from the Heroku console:

```txt
python -m ai_digest telegram-set-webhook
```

If Telegram ever needs to be disconnected from the web dyno:

```txt
python -m ai_digest telegram-delete-webhook
```

Heroku Scheduler should run:

```txt
python -m ai_digest run --mode normal --brief first-light --send
python -m ai_digest run --mode normal --brief midday-note --send
python -m ai_digest run --mode normal --brief night-read --send
```
