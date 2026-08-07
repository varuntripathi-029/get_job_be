"""ParsedResume normalisation and JSON salvaging from LLM output."""

from app.extraction.llm import parse_json_object
from app.extraction.schemas import ParsedResume


def test_accepts_a_well_formed_payload() -> None:
    parsed = ParsedResume.model_validate(
        {
            "skills": ["Python", "React"],
            "role_families": ["engineering"],
            "seniority": "senior",
            "experience_years": 6.5,
            "locations": ["Bengaluru"],
            "work_mode_preference": "remote",
        }
    )
    assert parsed.skills == ["Python", "React"]
    assert parsed.seniority == "senior"
    assert parsed.experience_years == 6.5


def test_deduplicates_and_trims_skills_case_insensitively() -> None:
    parsed = ParsedResume.model_validate(
        {"skills": ["Python", " python ", "PYTHON", "", "  ", "Go"]}
    )
    assert parsed.skills == ["Python", "Go"]


def test_drops_role_families_outside_the_vocabulary() -> None:
    """The CHECK constraint on jobs.role_family uses the same list, so an
    invented family could never match a job anyway."""
    parsed = ParsedResume.model_validate(
        {"role_families": ["engineering", "wizardry", "data"]}
    )
    assert parsed.role_families == ["engineering", "data"]


def test_unknown_seniority_becomes_none_rather_than_failing() -> None:
    parsed = ParsedResume.model_validate({"seniority": "rockstar ninja"})
    assert parsed.seniority is None


def test_null_like_strings_become_none() -> None:
    for value in ("null", "none", "N/A", "unknown", ""):
        parsed = ParsedResume.model_validate({"work_mode_preference": value})
        assert parsed.work_mode_preference is None


def test_seniority_is_lowercased() -> None:
    assert ParsedResume.model_validate({"seniority": "Senior"}).seniority == "senior"


def test_missing_fields_default_to_empty() -> None:
    parsed = ParsedResume.model_validate({})
    assert parsed.skills == []
    assert parsed.role_families == []
    assert parsed.seniority is None
    assert parsed.experience_years is None


def test_non_string_list_items_are_discarded() -> None:
    parsed = ParsedResume.model_validate({"skills": ["Python", 42, None, "Go"]})
    assert parsed.skills == ["Python", "Go"]


def test_skills_are_capped() -> None:
    parsed = ParsedResume.model_validate(
        {"skills": [f"skill-{i}" for i in range(200)]}
    )
    assert len(parsed.skills) == 60


# --- JSON extraction from model output ---------------------------------------


def test_parses_bare_json() -> None:
    assert parse_json_object('{"a": 1}') == {"a": 1}


def test_strips_markdown_fences() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_recovers_json_buried_in_prose() -> None:
    text = 'Sure! Here is the JSON you asked for:\n{"a": 1}\nHope that helps.'
    assert parse_json_object(text) == {"a": 1}


def test_returns_none_for_unparseable_output() -> None:
    assert parse_json_object("I cannot help with that.") is None
    assert parse_json_object("") is None


def test_returns_none_for_a_json_array() -> None:
    """Every prompt asks for an object; an array means the model ignored it."""
    assert parse_json_object("[1, 2, 3]") is None


def test_locations_are_capped() -> None:
    parsed = ParsedResume.model_validate(
        {"locations": [f"city-{i}" for i in range(50)]}
    )
    assert len(parsed.locations) == 10


def test_out_of_range_experience_years_does_not_kill_the_parse() -> None:
    """A hallucinated 999 years should cost the years field, not the skills."""
    from app.extraction.service import _salvage

    salvaged = _salvage({"skills": ["Python"], "experience_years": 999})
    assert salvaged.skills == ["Python"]
    assert salvaged.experience_years is None
