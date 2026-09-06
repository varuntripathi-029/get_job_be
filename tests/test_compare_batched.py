"""compare_companies assembles its data set-based across all companies at once.

DB-backed, so these skip when no test Postgres is reachable (see conftest).
They exist to catch the window-function batching (score history, top events)
returning the wrong rows per company — the risk a per-company loop never had.
"""

from datetime import UTC, datetime, timedelta

from app.companies.models import Company
from app.companies.service import compare_companies
from app.extraction.models import Event
from app.jobs.models import Job
from app.scoring.models import CompanyScore


async def _company(db, name: str, slug: str) -> Company:
    company = Company(name=name, slug=slug, canonical_domain=f"{slug}.example.com")
    db.add(company)
    await db.flush()
    return company


async def test_compare_batches_history_jobs_and_events_per_company(db_session) -> None:
    now = datetime.now(UTC)
    alpha = await _company(db_session, "Alpha", "alpha")
    beta = await _company(db_session, "Beta", "beta")

    # Alpha: two scores (so history has order to get wrong), three active jobs
    # across two families, and one recent event.
    db_session.add_all(
        [
            CompanyScore(
                company_id=alpha.id,
                score_version="v1",
                momentum_score=10.0,
                momentum_label="rising",
                scored_at=now - timedelta(days=5),
            ),
            CompanyScore(
                company_id=alpha.id,
                score_version="v1",
                momentum_score=20.0,
                momentum_label="hot",
                scored_at=now - timedelta(days=1),
            ),
            Job(company_id=alpha.id, title="Backend Eng", role_family="engineering"),
            Job(company_id=alpha.id, title="Frontend Eng", role_family="engineering"),
            Job(company_id=alpha.id, title="Data Scientist", role_family="data"),
            Job(
                company_id=alpha.id,
                title="Closed Role",
                role_family="engineering",
                is_active=False,
            ),
            Event(
                company_id=alpha.id,
                event_type="funding",
                title="Alpha raised a round",
                observed_at=now - timedelta(days=2),
            ),
        ]
    )
    # Beta: one score, one job, no events — the "absent from the map" case each
    # batched helper must default rather than KeyError on.
    db_session.add_all(
        [
            CompanyScore(
                company_id=beta.id,
                score_version="v1",
                momentum_score=5.0,
                momentum_label="quiet",
                scored_at=now - timedelta(days=1),
            ),
            Job(company_id=beta.id, title="Recruiter", role_family="hr"),
        ]
    )
    await db_session.commit()

    # Requested beta-first, to prove output order follows the request, not the DB.
    result = await compare_companies(db_session, ["beta", "alpha"])

    assert [c.slug for c in result] == ["beta", "alpha"]
    beta_out, alpha_out = result

    # Latest score wins per company.
    assert alpha_out.momentum_score == 20.0
    assert alpha_out.momentum_label == "hot"
    assert beta_out.momentum_score == 5.0

    # Job families counted per company; closed roles excluded.
    assert alpha_out.active_jobs_by_family == {"engineering": 2, "data": 1}
    assert alpha_out.active_jobs == 3
    assert beta_out.active_jobs_by_family == {"hr": 1}

    # History is oldest-first and scoped to the right company.
    assert [p.momentum_score for p in alpha_out.score_history] == [10.0, 20.0]
    assert [p.momentum_score for p in beta_out.score_history] == [5.0]

    # Events land on the company they belong to, and Beta's empty case defaults.
    assert alpha_out.recent_events == 1
    assert [e.title for e in alpha_out.top_events] == ["Alpha raised a round"]
    assert beta_out.recent_events == 0
    assert beta_out.top_events == []
