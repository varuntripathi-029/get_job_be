"""Careers-page extraction: identity stability and the mass-close guard.

The two failure modes that would quietly corrupt the jobs table, neither of
which the ATS path can hit.
"""

from app.crawler.fetchers.html_text import html_to_linked_text, html_to_text
from app.jobs.careers import (
    MAX_JOBS_PER_PAGE,
    normalise_career_jobs,
    synthetic_external_id,
)
from app.sources.service import discover_ats_board

PAGE = "https://acme.example/careers"


class TestSyntheticExternalId:
    def test_url_identifies_the_role(self):
        a = synthetic_external_id("Staff Engineer", "Remote", "https://x.test/j/1")
        b = synthetic_external_id("Staff Engineer", "Remote", "https://x.test/j/1")
        assert a == b

    def test_title_churn_does_not_change_id_when_url_is_stable(self):
        # A re-render that adds whitespace or changes casing must not create a
        # second row for the same posting.
        a = synthetic_external_id("Staff Engineer", "Remote", "https://x.test/j/1")
        b = synthetic_external_id("staff   engineer", "REMOTE", "https://x.test/j/1")
        assert a == b

    def test_whitespace_collapsed_without_a_url(self):
        a = synthetic_external_id("Staff Engineer", "Remote", None)
        b = synthetic_external_id("  Staff   Engineer ", " remote ", None)
        assert a == b

    def test_distinct_roles_get_distinct_ids(self):
        a = synthetic_external_id("Staff Engineer", "Remote", None)
        b = synthetic_external_id("Staff Designer", "Remote", None)
        assert a != b

    def test_same_role_in_two_cities_is_two_postings(self):
        a = synthetic_external_id("Staff Engineer", "Bengaluru", None)
        b = synthetic_external_id("Staff Engineer", "Mumbai", None)
        assert a != b

    def test_prefixed_so_it_cannot_collide_with_a_vendor_id(self):
        assert synthetic_external_id("Engineer", None, None).startswith("cp_")


class TestNormaliseCareerJobs:
    def test_extracts_title_location_and_link(self):
        rows = normalise_career_jobs(
            [{"title": "Backend Engineer", "location": "Pune",
              "url": "https://acme.example/jobs/be"}],
            PAGE,
        )
        assert len(rows) == 1
        assert rows[0]["title"] == "Backend Engineer"
        assert rows[0]["location_raw"] == "Pune"
        assert rows[0]["application_url"] == "https://acme.example/jobs/be"

    def test_falls_back_to_the_careers_page_when_a_role_has_no_link(self):
        rows = normalise_career_jobs([{"title": "Backend Engineer"}], PAGE)
        assert rows[0]["application_url"] == PAGE

    def test_rejects_a_non_url_in_the_url_field(self):
        # The model sometimes echoes the role name into `url`.
        rows = normalise_career_jobs(
            [{"title": "Backend Engineer", "url": "Backend Engineer"}], PAGE
        )
        assert rows[0]["application_url"] == PAGE

    def test_drops_navigation_shaped_titles(self):
        rows = normalise_career_jobs(
            [{"title": "Go"}, {"title": ""}, {"title": None}, {"title": "x" * 500}],
            PAGE,
        )
        assert rows == []

    def test_deduplicates_repeated_roles(self):
        rows = normalise_career_jobs(
            [
                {"title": "Backend Engineer", "url": "https://acme.example/j/1"},
                {"title": "Backend Engineer", "url": "https://acme.example/j/1"},
            ],
            PAGE,
        )
        assert len(rows) == 1

    def test_caps_a_runaway_response(self):
        rows = normalise_career_jobs(
            [{"title": f"Engineer {i}"} for i in range(MAX_JOBS_PER_PAGE + 50)], PAGE
        )
        assert len(rows) <= MAX_JOBS_PER_PAGE

    def test_ignores_non_dict_entries(self):
        rows = normalise_career_jobs(["Backend Engineer", 42, None], PAGE)
        assert rows == []


class TestLinkedText:
    HTML = """
    <html><body>
      <nav><a href="/about">About</a></nav>
      <ul>
        <li><a href="/jobs/be">Backend Engineer</a> — Pune</li>
        <li><a href="https://acme.example/jobs/fe">Frontend Engineer</a> — Remote</li>
      </ul>
      <a href="mailto:jobs@acme.example">Email us</a>
    </body></html>
    """

    def test_plain_extraction_loses_every_link(self):
        assert "](" not in html_to_text(self.HTML)

    def test_links_are_preserved_and_absolutised(self):
        text = html_to_linked_text(self.HTML, PAGE)
        assert "[Backend Engineer](https://acme.example/jobs/be)" in text
        assert "[Frontend Engineer](https://acme.example/jobs/fe)" in text

    def test_location_stays_beside_its_role(self):
        # The row's text must survive intact, or the model cannot pair a role
        # with where it is.
        text = html_to_linked_text(self.HTML, PAGE)
        assert "Pune" in text

    def test_mailto_is_not_an_apply_link(self):
        assert "mailto:" not in html_to_linked_text(self.HTML, PAGE)


class TestBoardDiscovery:
    def test_finds_an_embedded_greenhouse_board(self):
        html = '<iframe src="https://job-boards.greenhouse.io/acme"></iframe>'
        assert discover_ats_board(html) == "https://job-boards.greenhouse.io/acme"

    def test_finds_a_lever_board_in_inline_json(self):
        html = '<script>{"board":"https://jobs.lever.co/acme"}</script>'
        assert discover_ats_board(html) == "https://jobs.lever.co/acme"

    def test_ignores_non_board_routes_on_a_board_host(self):
        html = '<a href="https://boards.greenhouse.io/embed/job_app?for=x">a</a>'
        assert discover_ats_board(html) is None

    def test_ignores_asset_urls(self):
        html = '<script src="https://jobs.ashbyhq.com/api/analytics.js"></script>'
        assert discover_ats_board(html) is None

    def test_returns_none_for_a_page_with_no_board(self):
        assert discover_ats_board("<p>no openings currently</p>") is None

    def test_handles_empty_input(self):
        assert discover_ats_board("") is None
