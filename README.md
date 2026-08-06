# HireSignal — Backend

API and background workers for **HireSignal**, an AI hiring intelligence platform that computes explainable hiring momentum scores for companies from public signals.

> Frontend lives in a separate repo: [get_job_fe](https://github.com/varuntripathi-029/get_job_fe)

## What HireSignal does

HireSignal monitors companies using public signals — career pages, funding announcements, engineering blogs, news, and ATS APIs — and turns them into a hiring momentum score backed by linked evidence.

It is **not** a job portal, and it never claims a company *will* hire. The strongest claim it makes is:

> "Based on recent public activity, Company X shows strong hiring signals."

Every score links back to the evidence that produced it.

## Status

🚧 **In development.**

Working: configuration, async database layer, all 9 models with an initial Alembic migration, Google OAuth + JWT sessions, companies CRUD with entity resolution, the source registry with its submission/approval workflow, SSRF protection, and admin crawler-health/metrics endpoints.

Not built yet: the fetchers (ATS/RSS/static/Playwright/news APIs), rate limiter, content pre-filter, LLM extraction, event deduplication, scoring engine, ATS job sync, Celery workers, and the seed script. `source-registry.md` holds the seed input.

## Tech stack

| Area | Choice |
|---|---|
| Language | Python 3.12+ |
| Framework | FastAPI |
| ORM | SQLAlchemy (async) + asyncpg |
| Migrations | Alembic |
| Config | Pydantic v2 + pydantic-settings |
| Database | PostgreSQL (Neon) with pgvector |
| Queue | Celery + Redis |
| Auth | Google OAuth 2.0 + PyJWT |
| HTTP | httpx |
| Parsing | BeautifulSoup4, readability-lxml, feedparser |
| Browser | Playwright (used sparingly) |
| Email | Resend |
| Package manager | **uv** |

## Authentication

Google OAuth 2.0 **only**. There is no email/password registration, no password reset flow, and no `password_hash` column anywhere in the schema.

The flow:

1. Frontend renders a Google sign-in button and receives a Google ID token
2. Frontend posts that token to `POST /auth/google`
3. Backend verifies it with `google.oauth2.id_token.verify_oauth2_token`
4. Backend finds or creates a `User` matched on the Google `sub` claim
5. Backend issues its own JWT access + refresh tokens
6. Frontend uses the JWT as a Bearer token for all subsequent calls

## Architecture decisions

- **Modular monolith** — feature modules inside one deployable, not microservices
- **9 PostgreSQL tables** — deliberately small schema
- **Database as scheduler** — next-run timestamps in Postgres drive crawls, no separate scheduler service
- **Deterministic scoring** — the score is a pure function of stored events, so it is reproducible and explainable. LLMs never produce the number
- **Two-model LLM strategy** — a cheap model classifies and filters, an expensive model extracts structured data only from what survives
- **ATS-first crawling** — prefer Greenhouse/Lever/Ashby APIs, then RSS, then static HTML, and only fall back to a headless browser when nothing else works

This is a solo project running on free-tier infrastructure, which is why cost control shows up in almost every decision above.

## Local setup

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
# 1. Install dependencies
uv sync

# 2. Start Postgres (with pgvector) and Redis
docker compose up -d

# 3. Configure environment
cp .env.example .env      # then fill in the blanks

# 4. Apply migrations
uv run alembic upgrade head

# 5. Run the API
uv run uvicorn app.main:app --reload
```

The API will be at `http://localhost:8000`, with interactive docs at `/docs` and a health check at `/health`.

To run the background workers:

```bash
uv run celery -A workers.celery_app worker --loglevel=info
uv run celery -A workers.celery_app beat --loglevel=info
```

## Tests and linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy app
```

## Environment variables

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored and must never be committed.

Required to boot: `DATABASE_URL`, `REDIS_URL`, `GOOGLE_CLIENT_ID`, `JWT_SECRET_KEY`.
