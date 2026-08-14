# HireSignal — backend

Every architectural decision in this project was made by a constraint, and the constraint was almost always money.

That sounds like an excuse. It isn't. Free-tier limits turned out to be a decent design teacher: they force you to answer "how little can this cost per request" before "how elegant is this," and a surprising number of the answers ended up being the right ones anyway. This README is about how the thing actually works, what broke while building it, and what the deployed version genuinely cannot show you.

The frontend lives in a separate repo: [get_job_fe](https://github.com/varuntripathi-029/get_job_fe). There is no monorepo root.

## What it does

HireSignal watches companies through public signals — funding rounds, engineering blog posts, career pages, ATS job boards, news coverage — and turns them into a hiring momentum score with the evidence attached.

It never claims a company *will* hire. The strongest supported claim is "based on recent public activity, Company X shows strong hiring signals," and every number links back to the article or job board that produced it. That guardrail is why the scoring engine is deliberately boring, which I'll get to.

## How a signal actually travels

Nine tables, one deployable, no microservices. Here is the path a single piece of information takes from a website to a score on a page.

**1. The database is the scheduler.** There is no cron daemon and no in-memory schedule. Every source row carries `next_crawl_at`, and a tick selects whatever is due with `FOR UPDATE SKIP LOCKED`. Two overlapping ticks cannot grab the same source, and nothing is lost on restart because the schedule was never in memory to begin with. This one decision paid for itself repeatedly — most notably when the whole scheduler had to move out of Celery and into HTTP endpoints, which took about a day precisely because the state lived in Postgres rather than in a process.

**2. Fetch by the cheapest tier that works.** `ats_api` first, then `rss`, then `static_http`, and only `playwright` when a page genuinely renders in JavaScript. A Greenhouse API call returns clean structured JSON in milliseconds; a headless Chromium launch costs seconds and hundreds of megabytes. Of the 886 sources currently registered, exactly zero sit on the browser tier, which is the tier working as designed rather than the tier being unused.

**3. A free rule-based pre-filter runs before any model.** Most crawled pages are festival greetings, product marketing and legal boilerplate. Keyword rules throw those out at zero cost.

**4. Two models, cheap one first.** A small classifier decides whether a page contains a hiring signal at all. Only survivors reach the expensive extractor that pulls out structured events. The classifier runs on everything that clears the pre-filter, so it dominates spend and drives the default model choice — `llama-3.1-8b-instant` at $0.05/$0.08 per million tokens, not the 70B model at ten times that.

**5. Events are bitemporal.** `event_occurred_at` is when the thing happened in the world; `observed_at` is when we saw it. Scoring decays on the former and falls back to the latter. Without the split, a two-year-old funding round discovered yesterday would read as fresh momentum.

**6. Attribution happens per event, not per document.** This one is subtle and it bit hard. A company blog is about one company, but a news feed is not — a single Entrackr article lists eight departures across three firms. Each extracted event is resolved back to the company its own *title* names. Before that split, "SkyAI recruiting Python developers" got filed against LinkedIn because the excerpt mentioned where the post appeared, and a roundup of Peak XV departures landed on Pine Labs.

**7. The score is a pure function.** Same events in, same score out. No model ever produces the number. Scores are append-only, so any historical score is reproducible and any claim on the site can be traced to the rows that justify it.

That last point is the whole product thesis. An LLM-generated score would be unfalsifiable, and a hiring-intelligence tool that can't show its work is a horoscope.

## Things that broke, and what they taught

### The first user could never sign in

`is_active` on the users table had `server_default='true'`. Postgres applies a server default during the INSERT — so on a freshly constructed, not-yet-flushed object, the attribute reads `None`. The deactivation guard in `get_or_create_user` runs *before* the commit, saw a falsy value, raised "This account has been deactivated," and rolled the row back.

Result: no account could ever be created. Not just the first admin — anyone. The logs cheerfully printed `bootstrapping_first_admin` on every single attempt, because the previous attempt had rolled back and there genuinely was still no admin.

What made it hard to see was the frontend, which reported it as `Request failed with status code 401`. The error handler only understood FastAPI's default `detail` key, while this API returns a flat `{"error", "message"}` contract. The real message — "This account has been deactivated." — was on the wire the entire time and never displayed. Two small bugs stacked into one that looked unexplainable.

### The API contract drifted from the API

There was a hand-written `designs/api-contract.md`. By the time the frontend was built, it disagreed with the running server in roughly a dozen structural ways and fifteen field names: companies addressed by slug not id, trending and industries living under `/dashboard`, newsletter confirmation being a POST with the token in the body.

The fix was to stop trusting the document and generate the client from the live OpenAPI dump. The lesson is older than this project: a contract nobody executes is a wish.

### The registry lies about ATS tokens, 26 times out of 55

The seed registry names an ATS vendor per company but never the board token, and the token is only *usually* the company name. On the current file, 26 of 55 guesses were wrong. Seeding those as approved would have created dozens of sources that fetch a 404 forever while looking perfectly healthy in the dashboard.

So `--validate` probes each candidate board and seeds the failures as `pending` instead. It makes seeding slower and stops it from quietly poisoning the source table.

The better fix came later. Companies overwhelmingly publish `acme.com/careers` as a thin wrapper around a hosted board, so the crawler now scans the rendered HTML for a known board host and registers it automatically. Razorpay's careers page embeds a Greenhouse board whose token is `razorpaysoftwareprivatelimited`. Nobody was ever going to guess that. Discovery turned two test companies from "unparseable marketing page" into 107 structured jobs with working apply links and zero LLM cost.

### Text extraction was deleting the only thing that mattered

`html_to_text` ends at BeautifulSoup's `get_text()`, which discards every `<a href>`. That is exactly right for prose, where a link is noise. It is exactly wrong for a job listing, where the link *is* the content — an extracted role with no URL is a role nobody can apply to.

Measured on a real careers page: zero links surviving before the fix, 85 after.

While measuring that, a more interesting number turned up. Razorpay's careers page is 950KB of HTML that reduces to 1,132 characters of text containing no jobs at all, because the listings are in an embedded board. Three of the four career pages I tested were like this. The parsing was never the hard part; knowing what kind of page you're looking at was.

### The free LLM tier has an opinion about your prompt design

Careers-page extraction is an unusual prompt: its output scales with its input, since every additional role listed is another JSON object generated. Groq's free tier caps at 8,000 tokens per minute across input *and* output, so the budget burns from both ends simultaneously.

Measured on a 90-role page:

| Input | Result |
|---|---|
| 3,000 chars | 20 roles, valid JSON |
| 5,000 chars | 34 roles, valid JSON |
| 8,000 chars | 21 roles, output already truncating |
| 12,000 chars | no valid JSON at all, plus 429s |

Capped at 6,000. The failure mode is what makes this worth writing down: at 12,000 chars the API returned `json_validate_failed` with an empty `failed_generation` field, which reads like a prompt bug and is actually a rate limit wearing a costume.

### A partial scrape must never look like a mass layoff

The ATS sync closes any stored job missing from the incoming set, which is correct: a Greenhouse API either returns the whole board or errors. Reusing that logic for scraped career pages would have been a disaster. One slow render, one layout change, one truncated model response, and thirty live roles get marked closed.

So reconciliation gained an `allow_close` flag, off for anything scraped. Related: scraped postings have no stable vendor id, so one is synthesised from the role's apply URL, falling back to a hash of title and location. The first version didn't collapse internal whitespace — a title gaining a double space between renders would have produced a new id, duplicating every role on every crawl. A test caught it. That test exists because of this specific near-miss.

## Tradeoffs, stated plainly

**In-process caching instead of Redis for dashboard aggregates.** Upstash bills per command, and paying one on every anonymous page view for data that changes at crawl frequency would have been the single largest line in the budget. The cost: two workers can disagree for up to one TTL, and the cache empties on restart. For public aggregates on one free instance, that is fine.

**Rate limiting is in-process too**, for the same reason. It is honest about what it is — it stops casual abuse and is not a security control. Behind a proxy without `--proxy-headers`, every client shares one bucket.

**Newsletter tokens are HMAC-signed, not stored.** Unsubscribe links live inside already-delivered email and have to work indefinitely. A Redis-backed token silently breaks every one of them the moment a free-tier instance evicts a key, which is a legal problem rather than a bug.

**Offset pagination, not cursors.** The largest table is jobs in the low thousands. Cursor pagination earns its complexity when deep offsets get slow, and they don't yet.

**Categorical columns are TEXT with a CHECK constraint**, never Postgres ENUM. Adding a value is an ALTER on a constraint instead of a type migration.

**Match reasons are keyword comparison, never an LLM.** They render once per result, so putting a model there multiplies cost by the result count for a feature nobody would notice.

**The entity matcher is deliberately asymmetric.** Missing a mention costs one signal. A false match publishes someone else's funding round as evidence on a company's page, which is the one thing the product promises not to do. So a name that is also an ordinary word — "Open", "Linear", "Meta" — or one of four characters or fewer must match the company's own casing *and* sit within 120 characters of a corporate cue. A name two companies share identifies neither.

## What this deployment cannot show you

The live version runs on Render's free tier, Neon, Upstash, and free API quotas. Several things are switched off, and it would be dishonest to present the demo as the system.

**No headless browser.** Chromium does not fit in 512MB beside the application, and an OOM kill takes down the entire service rather than one crawl. `PLAYWRIGHT_MAX_CONCURRENT=0` disables the tier, and JS-rendered career pages fail with a clear message instead of being crawled. Given that three of four career pages I tested render client-side, this is the single biggest functional gap.

**No Celery worker, no Beat.** The free tier offers one process that sleeps when idle, which is nowhere for a scheduler to live. GitHub Actions cron now calls HTTP endpoints that run the same jobs inline. It works, and it is strictly worse than a real worker: no parallelism, no retry semantics, and a batch of three sources per tick bounded by a 50-second deadline. Sources are leased before crawling, so nothing is lost when a request runs out of budget — it just waits.

**Google Jobs coverage is a demo, not a feature.** SerpAPI's free tier is eight calls per day. Eight. That is enough to prove the integration works and nothing else.

**The instance sleeps.** The first request after an idle period waits on a cold start, which on Render's free tier runs to something like a minute.

**The data is thin, and that is the honest headline.** Momentum scores are computed from events, events come from crawling, and crawling costs money that is not being spent. The pipeline is real and the scores are real; there simply are not many of them yet. A version of this with a paid worker and a browser would look dramatically more alive, and the difference would be entirely operational rather than architectural.

Also worth saying: `/docs` is public on the deployment. That is a choice for a portfolio project, not something to copy.

## Running it locally

Local is where the system is actually complete — real Celery, real browser tier, no quota theatre.

```bash
uv sync                                    # uv, not pip or poetry
docker compose up -d                       # postgres + redis
cp .env.example .env                       # then fill it in
uv run alembic upgrade head
uv run uvicorn app.main:app --reload       # :8000, docs at /docs
```

Seed from the registry, probing each candidate ATS board:

```bash
uv run python -m scripts.seed --validate
```

Background workers (`--pool=solo` is required on Windows):

```bash
uv run celery -A workers.celery_app worker --loglevel=info --pool=solo --without-mingle --without-gossip
uv run celery -A workers.celery_app beat --loglevel=info
```

Tests and checks:

```bash
uv run pytest                              # 319 pass, 7 skipped
uv run ruff check app/ workers/ tests/
uv run mypy app
```

## Deployment shape

One web service from the Dockerfile. Postgres and Redis stay on Neon and Upstash, so nothing is tied to the host and a redeploy loses nothing. `render.yaml` is the blueprint; `.github/workflows/scheduler.yml` is the replacement for Beat.

Migrations run at container start, because the free tier has no pre-deploy hook and "remember to run alembic before every deploy" works right up until the once it doesn't. That is safe on one instance and would need rethinking on two — concurrent `alembic upgrade head` runs race on the version table.

`SCHEDULER_TOKEN` gates the scheduler endpoints. Unset means disabled with a 503, never unauthenticated: those endpoints spend LLM and third-party quota, so an open trigger is somebody else's shopping spree on your card.

## Gotchas worth knowing before you touch anything

- `app/models.py` must import every model, or Alembic autogenerate silently produces an empty migration.
- A `-pooler` Neon host disables both prepared-statement caches. PgBouncer's transaction pooling otherwise causes intermittent "prepared statement does not exist".
- `str(url)` masks the password as `***`. Always `render_as_string(hide_password=False)`.
- The tsvector expression in `search/service.py` must stay character-identical to the one in `ix_jobs_fts`, or Postgres quietly drops to a sequential scan.
- Grouping on `coalesce(col, 'literal')` fails: SQLAlchemy renders the literal as a bind parameter and `$1` in SELECT will not match `$3` in GROUP BY. Group on the raw column and fold nulls in Python.
- `tests/conftest.py` refuses to run if `TEST_DATABASE_URL` equals `DATABASE_URL`, because the fixtures call `drop_all` on teardown.

## Where it stands

Nine tables, 48 endpoints, 319 passing tests, and a source registry of 886 rows. The pipeline runs end to end: fetch, filter, classify, extract, deduplicate, attribute, score.

The interesting work left is not architectural. It is that the cheapest correct version of this system is now built, and finding out what it looks like with a budget would cost about fifteen dollars a month.
