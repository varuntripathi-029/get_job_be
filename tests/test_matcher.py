"""Match explanations. Pure functions over model instances — no database."""

from app.jobs.models import Job
from app.resumes.matcher import explain_match
from app.resumes.models import Resume


def make_resume(**overrides) -> Resume:
    defaults = {
        "parsed_skills": ["Python", "React", "PostgreSQL", "Docker"],
        "parsed_role_families": ["engineering"],
        "parsed_seniority": "senior",
        "parsed_experience_years": 6.0,
        "parsed_locations": ["Bengaluru"],
        "work_mode_preference": "remote",
    }
    return Resume(**{**defaults, **overrides})


def make_job(**overrides) -> Job:
    defaults = {
        "title": "Senior Backend Engineer",
        "skills": ["Python", "PostgreSQL", "Kubernetes"],
        "role_family": "engineering",
        "seniority": "senior",
        "work_mode": "remote",
        "location_normalized": "Bengaluru, India",
    }
    return Job(**{**defaults, **overrides})


def test_reports_overlapping_skills_with_a_count() -> None:
    reasons = explain_match(make_resume(), make_job(), 0.82)
    assert any("2 matching skills" in r for r in reasons)
    assert any("Python" in r and "PostgreSQL" in r for r in reasons)


def test_skill_overlap_is_case_insensitive() -> None:
    resume = make_resume(parsed_skills=["python", "DOCKER"])
    job = make_job(skills=["Python", "docker"])
    reasons = explain_match(resume, job, 0.8)
    assert any("2 matching skills" in r for r in reasons)


def test_skill_list_is_truncated_with_a_remainder_count() -> None:
    shared = ["Python", "React", "Go", "Rust", "Docker", "Kafka"]
    reasons = explain_match(
        make_resume(parsed_skills=shared), make_job(skills=shared), 0.9
    )
    assert any("+2 more" in r for r in reasons)


def test_singular_wording_for_one_skill() -> None:
    resume = make_resume(parsed_skills=["Python"])
    job = make_job(skills=["Python"])
    reasons = explain_match(resume, job, 0.7)
    assert any("1 matching skill:" in r for r in reasons)


def test_role_family_match_is_reported() -> None:
    reasons = explain_match(make_resume(), make_job(), 0.8)
    assert any("engineering role matches" in r for r in reasons)


def test_seniority_is_inferred_from_years_when_labels_differ() -> None:
    resume = make_resume(parsed_seniority=None, parsed_experience_years=6.0)
    reasons = explain_match(resume, make_job(seniority="senior"), 0.8)
    assert any("6 years of experience" in r for r in reasons)


def test_seniority_outside_the_plausible_band_is_not_claimed() -> None:
    """A 1-year candidate should not be told a principal role matches them."""
    resume = make_resume(parsed_seniority=None, parsed_experience_years=1.0)
    reasons = explain_match(resume, make_job(seniority="principal"), 0.8)
    assert not any("level matches" in r for r in reasons)


def test_location_match_is_reported() -> None:
    reasons = explain_match(make_resume(), make_job(), 0.8)
    assert any("Bengaluru" in r and "where you're based" in r for r in reasons)


def test_work_mode_match_is_reported() -> None:
    reasons = explain_match(make_resume(), make_job(), 0.8)
    assert any("Remote work matches your preference" in r for r in reasons)


def test_falls_back_to_the_similarity_score_when_nothing_else_matches() -> None:
    """An empty reason list next to a shown match reads like a bug."""
    resume = make_resume(
        parsed_skills=[],
        parsed_role_families=[],
        parsed_seniority=None,
        parsed_experience_years=None,
        parsed_locations=[],
        work_mode_preference=None,
    )
    job = make_job(
        skills=[],
        role_family=None,
        seniority=None,
        work_mode=None,
        location_normalized=None,
    )
    reasons = explain_match(resume, job, 0.71)
    assert reasons == ["Your profile is a 71% semantic match for this role"]


def test_handles_null_columns_without_raising() -> None:
    """A resume parsed before the LLM was configured has NULLs everywhere."""
    resume = Resume(parsed_skills=None, parsed_role_families=None)
    job = Job(title="Engineer", skills=None)
    assert explain_match(resume, job, 0.6)
