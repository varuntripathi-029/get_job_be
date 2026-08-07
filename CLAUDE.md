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

# --without-mingle/-gossip are CLI-only; they cannot be set in conf and each
# costs Redis commands at worker startup. --pool=solo is required on Windows.
uv run celery -A workers.celery_app worker --loglevel=info --pool=solo --without-mingle --without-gossip
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
- **Celery tasks are sync wrappers** around async code. `workers/base.py` gives each task its own event loop and session; the request-scoped `get_db` dependency does not apply in a worker.

## Auth

Google OAuth 2.0 **only**. No password field exists anywhere in the schema, and no password/reset flow should ever be added. Frontend gets a Google ID token → `POST /auth/google` → backend verifies with `google.oauth2.id_token.verify_oauth2_token` → finds/creates a `User` by the Google `sub` claim → issues its own JWT access + refresh pair.

The first user to sign in becomes `admin` (logged as a warning); everyone after is `user`.

## Security

- **Every user-submitted URL is SSRF-validated** (`app/crawler/ssrf.py`) at submission *and* again at crawl time, because DNS can change in between. Private ranges, link-local, cloud metadata, and IPv4-mapped IPv6 are all blocked.
- News/search API sources use pseudo-URLs (`newsapi://search`) and deliberately bypass SSRF validation — they call trusted third-party APIs, not user input.
- `.env` is gitignored and must stay that way. Only `.env.example` is committed.

## Current state

Implemented: config, database, all 9 models, initial migration, Google OAuth, companies CRUD + entity resolution, source registry + approval workflow, SSRF, admin health/metrics, resume upload/parse/match, embeddings, newsletter (subscribe → confirm → weekly send), public job and event endpoints, Celery app + Beat.

Implemented on top of that: search, public dashboard, company comparison, pagination, error contract, in-process rate limiting, extended health check, OpenAPI metadata. 41 endpoints.

Not yet implemented: **fetchers, crawler rate limiter, pre-filter, event extraction, dedup, scoring engine, ATS job sync, seed script.** Nothing writes to `events`, `jobs` or `company_scores`, so those tables are empty. Every read endpoint is wired and returns a correct empty result — but the whole product surface stays blank until the pipeline lands. This is the single largest gap.

## API conventions

- **Every list endpoint returns `PaginatedResponse`** (`items`, `total`, `page`, `per_page`, `total_pages`, `has_next`, `has_prev`) from `app/common/pagination.py`. Offset-based, not cursor: the biggest table is jobs in the low thousands, and cursor pagination earns its complexity only when deep offsets get slow.
- **Every error is `{"error": "CODE", "message": "..."}`**, flat, from the `AppError` hierarchy and the handler in `main.py`. `error` is a stable machine code; `message` is for humans and may change wording.
- **`/companies/compare` is declared before `/companies/{slug}`.** FastAPI matches in definition order, so reordering them makes `compare` resolve as a company slug and 404.
- **Rate limiting is in-process** (`app/common/rate_limit.py`), not Redis — spending an Upstash command per public request would dwarf the crawl pipeline's usage. Consequence: limits are per-worker and reset on restart, and behind a proxy every client shares one bucket unless the server runs with `--proxy-headers`. It stops casual abuse; it is not a security control.
- **Dashboard aggregates are cached in process memory**, 5-30 minutes depending on endpoint, for the same reason. Two workers can disagree for up to one TTL.
- **Search uses two strategies deliberately.** Companies and events match by substring (ILIKE) because users search them by name and "razor" should find "Razorpay". Jobs use the `ix_jobs_fts` GIN index, because descriptions are long prose where stemming and ranking help. The tsvector expression in `search/service.py` must stay character-identical to the one in the index, or Postgres silently drops to a sequential scan.

## Resumes and matching

Upload → `parser.extract_text` (PyMuPDF for PDF, python-docx for DOCX) → `parse_resume_with_llm` (classifier model, cheap) → embedding → one row per user.

Every stage degrades instead of failing: an LLM outage stores the raw text, an embedding outage leaves the vector NULL, and both surface as `warnings` on the upload response. `ParsedResume` truncates rather than rejects over-long lists for the same reason — a validation error would throw away a usable parse.

Matching is `pgvector` cosine distance with a 0.55 similarity floor, filtered by role family / seniority / work mode. Match reasons are pure keyword comparison, never an LLM — they render once per result, so an LLM there would multiply cost by the result count.

## Embeddings

**Groq has no embedding endpoint.** `EMBEDDING_PROVIDER` is therefore always a different provider from the chat roles — Gemini by default, whose free tier covers this comfortably. Without `GEMINI_API_KEY`, resume matching and job embeddings are disabled and log a warning at startup; everything else works.

768 dimensions is fixed by `Vector(768)` on `jobs.embedding` and `resumes.embedding`. `generate_embedding` asserts the width and refuses a mismatched vector, because the alternative is an opaque driver error at insert time.

## Newsletter

Double opt-in. Subscribe → confirmation email → `is_active=true` only after the link is clicked. `/newsletter/subscribe` returns the same message whether or not the address was already known, so it cannot be used to test who has an account.

**Tokens are HMAC-signed, not stored in Redis** (`app/newsletter/tokens.py`). Unsubscribe links live inside already-delivered mail and must work indefinitely; a Redis-backed token silently breaks every one of them when a free-tier instance evicts a key, which is a legal problem and not just a bug. The signed `purpose` claim stops a confirmation token being replayed against unsubscribe. Redis is still used, but only for the edition counter and the daily send cap — both safe to lose.

Sending goes through httpx rather than the `resend` package, which is synchronous and would block the event loop. Free tier is 100/day at 2 req/s; the sender paces at 1/s and defers the remainder to the next run when the cap is hit.

## Gotchas

- `app/models.py` must import every model. Alembic autogenerate silently produces an empty migration for anything not imported there.
- `alembic/env.py` runs `CREATE EXTENSION IF NOT EXISTS vector` before migrating — pgvector columns fail without it.
- `cors_origins` uses `NoDecode` so `.env` can hold a plain comma-separated list instead of JSON.
- `uv run alembic upgrade head --sql` renders DDL offline, with no database needed. Useful for reviewing a migration before applying it.
- `ix_jobs_fts` is excluded from autogenerate comparison in `alembic/env.py`. Postgres rewrites expression indexes when storing them (`'english'` becomes `'english'::regconfig`), so alembic reports permanent false drift and would emit a spurious drop/create on every run.
- `tests/conftest.py` refuses to run if `TEST_DATABASE_URL` equals `DATABASE_URL` — the fixtures call `drop_all` on teardown.
- Grouping on `coalesce(col, 'literal')` fails on Postgres: SQLAlchemy renders the literal as a bind parameter and `$1` in SELECT will not match `$3` in GROUP BY. Group on the raw column and fold nulls in Python.
