# HireSignal — Backend

AI hiring intelligence platform. Monitors companies via public signals and computes explainable hiring momentum scores backed by linked evidence.

Frontend is a **separate repo**: `../get_job_fe` → github.com/varuntripathi-029/get_job_fe. There is no monorepo root; `D:\proj\GenAi\get_job\` is just a container directory and is not a git repo.

## Product guardrail

HireSignal never claims a company *will* hire. The strongest supported claim is "based on recent public activity, Company X shows strong hiring signals," and every score links to the evidence behind it. Keep this in mind when writing copy, prompts, or API field names.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 async + asyncpg · Alembic · Pydantic v2 · PostgreSQL 16 + pgvector · Celery + Redis · Google OAuth + PyJWT · httpx · Playwright · Gemini · Resend.

Package manager is **uv**, not pip or poetry. Frontend uses pnpm.

## Commands

```bash
uv sync                                    # install
docker compose up -d                       # postgres + redis
uv run alembic upgrade head                # migrate
uv run uvicorn app.main:app --reload       # API on :8000
uv run python -m scripts.seed              # seed from source-registry.md

uv run pytest                              # tests
uv run ruff check app/ workers/ tests/     # lint
uv run ruff format app/ workers/ tests/    # format
uv run mypy app                            # types

uv run celery -A workers.celery_app worker --loglevel=info
uv run celery -A workers.celery_app beat   --loglevel=info
```

## Architecture decisions

- **Modular monolith.** Feature packages under `app/`, one deployable. Not microservices — this is a solo project on free-tier infrastructure and the operational overhead isn't worth it.
- **Nine tables.** users, companies, sources, crawl_logs, events, jobs, company_scores, newsletter_subscribers, resumes. Resist adding a tenth without a strong reason.
- **Database as scheduler.** `sources.next_crawl_at` drives everything. Celery Beat ticks every 60s and selects due rows with `FOR UPDATE SKIP LOCKED`; there is no separate scheduler service and no in-memory schedule to lose on restart.
- **Deterministic scoring.** `app/scoring/engine.py` is a pure function of stored events — same events in, same score out. LLMs never produce the number, only the events feeding it. Scores are append-only so any historical score is reproducible.
- **Two-model LLM strategy.** A cheap model gates relevance; only survivors reach the expensive model for structured extraction. A rule-based pre-filter runs before either, at zero cost. Provider is configured per role (`CLASSIFIER_PROVIDER` / `EXTRACTOR_PROVIDER`, each one of `gemini | openai | anthropic | groq | xai`), so the two roles can sit on different vendors.

  Default is **Groq** for both. Groq, xAI and OpenAI all speak the OpenAI chat-completions protocol, so they share one client and differ only by `base_url` (see `settings.base_url_for`); Gemini and Anthropic use their own SDKs.

  Cost note — the classifier runs on every page clearing the pre-filter and dominates LLM spend, so it drives the default:

  | Model | Input | Output |
  |---|---|---|
  | `llama-3.1-8b-instant` (groq) | $0.05 | $0.08 |
  | `openai/gpt-oss-120b` (groq) | $0.15 | $0.60 |
  | `llama-3.3-70b-versatile` (groq) | $0.59 | $0.79 |
  | `gemini-2.5-flash-lite` | $0.10 | $0.40 |
  | cheapest Grok (xai) | $1.00 | $2.00 |

  Groq has a free tier with no card, and batch + prompt caching each cut 50% (stackable). Swapping any role to another vendor is a two-line `.env` change.
- **ATS-first crawling.** Prefer the tier that gives structured data for the least work: `ats_api` → `rss` → `static_http` → `playwright`. Playwright is quarantined behind `requires_js` and a concurrency semaphore.
- **Bitemporal events.** `event_occurred_at` (real world) vs `observed_at` (when we saw it). Scoring decays on the former, falling back to the latter.
- **Categorical columns are TEXT + CHECK**, never Postgres ENUM — adding a value is an ALTER on a constraint, not a type migration.
- **UUIDv7 primary keys** via `app.database.uuid7` for index locality.

## Auth

Google OAuth 2.0 **only**. No password field exists anywhere in the schema, and no password/reset flow should ever be added. Frontend gets a Google ID token → `POST /auth/google` → backend verifies with `google.oauth2.id_token.verify_oauth2_token` → finds/creates a `User` by the Google `sub` claim → issues its own JWT access + refresh pair.

The first user to sign in becomes `admin` (logged as a warning); everyone after is `user`.

## Security

- **Every user-submitted URL is SSRF-validated** (`app/crawler/ssrf.py`) at submission *and* again at crawl time, because DNS can change in between. Private ranges, link-local, cloud metadata, and IPv4-mapped IPv6 are all blocked.
- News/search API sources use pseudo-URLs (`newsapi://search`) and deliberately bypass SSRF validation — they call trusted third-party APIs, not user input.
- `.env` is gitignored and must stay that way. Only `.env.example` is committed.

## Current state

Implemented: config, database, all 9 models, initial migration, Google OAuth, companies CRUD + entity resolution, source registry + approval workflow, SSRF, admin health/metrics.

Not yet implemented: fetchers, rate limiter, pre-filter, LLM extraction, dedup, scoring engine, job sync, Celery workers, seed script. Placeholder routers exist for jobs/resumes/newsletter/scoring.

## Gotchas

- `app/models.py` must import every model. Alembic autogenerate silently produces an empty migration for anything not imported there.
- `alembic/env.py` runs `CREATE EXTENSION IF NOT EXISTS vector` before migrating — pgvector columns fail without it.
- `cors_origins` uses `NoDecode` so `.env` can hold a plain comma-separated list instead of JSON.
- `uv run alembic upgrade head --sql` renders DDL offline, with no database needed. Useful for reviewing a migration before applying it.
