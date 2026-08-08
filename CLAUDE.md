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
uv run python -m scripts.seed --validate   # seed from the registry, probing ATS boards

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

Also implemented: the whole crawl pipeline — fetchers, crawler rate limiter, pre-filter, LLM classify/extract, dedup, scoring engine, ATS job sync, and the seed script.

## Seeding

`hiring_intelligence_source_registry.md` is the seed input; `source-registry.md` is its predecessor and still parses. Parsing lives in `scripts/registry.py`, separate from `scripts/seed.py` so the rules are testable without a database.

Three things the parser exists to get right, each of which silently corrupts the seed otherwise:

- **URLs have no scheme** in the current registry (`bytes.swiggy.com/feed`), and cells carry inline caveats (`sebi.gov.in (no confirmed public RSS — verify before use)`). A cell counts as a URL only if it is *entirely* a URL, so an annotated cell seeds nothing rather than a source that 404s forever.
- **The Website column is not always the company's site.** `global_engineering_blogs` lists the blog there, and several live on Medium — Airbnb and Pinterest both reduce to `medium.com`, which is a unique column, so the second would be merged into the first. `_company_domain` rejects shared hosts and falls through to the next column.
- **Publications never become companies.** VC, accelerator and news rows have websites and careers pages of their own, but seeding them would put Blume Ventures on the dashboard with a hiring score. What they publish is kept, attached to no company.

Run `--validate` for real work: it probes each candidate ATS board and seeds the ones that fail as `pending`. The registry names a vendor but never a board token, and the token is only usually the company name — 26 of 55 guesses were wrong on the current file.

## Entity resolution

Two functions, and picking the wrong one produces silence rather than an error:

- `companies.service.resolve_company` takes a clean name or domain and matches exactly. Right for user input and API lookups.
- `companies.matcher` takes prose and finds the company inside it. Right for crawling. It was worth building because most registry rows have no career page at all: an article written about a company is the only way it is ever observed, so without prose matching those rows are inert.

The matcher is deliberately asymmetric. Missing a mention costs one signal; a false match publishes someone else's funding round as evidence on a company's page, which is the one thing the product promises not to do. So a name that is also an ordinary word ("Open", "Linear", "Meta"), or one of four characters or fewer ("Ola", "OYO"), must match the company's own casing *and* sit within 120 characters of a corporate cue. A name two companies share identifies neither.

**Attribution happens per event, not per document.** A company blog is about one company, but a news feed is not — one Entrackr piece lists eight departures across three firms. `_extract_and_store(..., route_per_event=True)` files each extracted event against the company its own **title** names, via `resolve_event_subject`; the evidence excerpt supplies surrounding words for the cue test but can never itself produce the match. Without that split, "SkyAI recruiting Python developers" was filed against LinkedIn because the excerpt said where the post appeared, and a roundup of Peak XV departures was filed against Pine Labs. An event naming nobody tracked is dropped rather than inheriting the article's company.

## Database

Postgres is **Neon** (serverless), not the `docker-compose.yml` in this repo — that is a local-dev fallback. Redis is **Upstash**, TLS-only.

`app/db_url.py` translates a provider connection string into asyncpg's vocabulary. Three things it handles that each cause a confusing failure otherwise:
- `sslmode` / `channel_binding` are libpq-only; `asyncpg.connect()` has no `**kwargs`, so passing them raises `TypeError`.
- `ssl` must live in the URL query, not `connect_args` — Alembic builds its own engine from `sqlalchemy.url` alone and would otherwise migrate over an unencrypted connection, which Neon rejects.
- `str(url)` masks the password as `***`. Always `render_as_string(hide_password=False)`.

A `-pooler` host disables both prepared-statement caches (asyncpg's and SQLAlchemy's); PgBouncer's transaction pooling otherwise causes intermittent "prepared statement does not exist".

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

Everything lives in `app/embeddings/`. Nothing outside that package imports a concrete provider — call `get_provider()` and use the `EmbeddingProvider` ABC. Text construction is centralised in `app/embeddings/text.py`; inline concatenation at a call site is what lets the crawler and the backfill worker embed the same job differently, producing vectors that cannot be compared.

**Groq has no embedding endpoint**, so `EMBEDDING_PROVIDER` is always a different provider from the chat roles — Gemini by default. Without a key, the job worker skips with a warning, resume indexing records `failed`, and matching returns an explanatory message. Crawling, scoring, search and the dashboard are unaffected: embeddings are an enhancement, not a requirement.

768 dimensions is fixed by `Vector(768)`. Providers validate the width and drop a mismatched vector, because the alternative is an opaque pgvector error at insert time.

**No embedding call ever happens on a read path.** `GET /resumes/matches` reads the stored vector and does pgvector similarity only. Generation happens in workers, and only when the source text actually changed:

- **Batching** — the provider takes 100 inputs per request. Measured on 100 real jobs: 100 calls became 1.
- **In-batch dedup** — identical embedding text is embedded once and the vector fanned out.
- **Sibling copy** — a job whose `content_hash` *and* title match an already-embedded posting copies the vector with no call. Companies post the same role in ten cities. Title is compared too, because two different roles at one company often share a description.

## Resume indexing

Upload is deliberately split. The request extracts text, stores the row with `indexing_status='pending'`, and returns; the LLM parse and embedding run in `workers/resumes.py`. The LLM alone was measured at ~14s on a cold model, which is not something to hold an HTTP request open for.

`indexing_status` moves `pending → processing → ready | failed`. Clients poll `GET /resumes/me`. Matching before `ready` returns an empty list plus a message explaining which state it is in, rather than a bare empty array that looks like "no jobs suit you".

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
