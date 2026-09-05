"""Keka ATS adapter: detection, board-id discovery, and job normalisation.

Pure functions and fixed payloads, no network. The sample mirrors the real
shape of `careerportalinfo` and the `embedjobs/default/active` feed observed on
a live Keka careers site.
"""

from app.crawler.fetchers.ats import (
    KEKA_JOB_DETAIL,
    _keka,
    _keka_board_id,
    api_url_for,
    detect_provider,
)
from app.sources.service import detect_ats_provider, detect_fetch_tier

BOARD_ID = "13e8b67d-1258-4080-bd6e-07359a0662a7"

# Trimmed to the fields the portal actually returns; the board id lives only in
# the document paths, never in a key of its own.
PORTAL_INFO = {
    "name": "Blitz",
    "companyWebsite": "https://www.blitznow.com",
    "shortName": "blitz",
    "logoPath": f"/ats/documents/{BOARD_ID}/careerportal/logo.png",
    "careersBackgroundPath": f"/ats/documents/{BOARD_ID}/careerportal/bg.png",
    "careersPortalColorCode": "#0066FF",
}

ACTIVE_JOB = {
    "id": 159617,
    "title": "Senior Program Manager - Customer Experience",
    "description": "<p>Own the <b>customer experience</b> metrics.</p>",
    "departmentIdentifier": "278009f0-7562-422a-94a3-10d48888c343",
    "departmentName": "Central Operations",
    "jobLocations": [
        {"id": 1, "name": "Bangalore HQ", "city": "Bengaluru", "state": "Karnataka"}
    ],
    "jobType": 2,
    "experience": "2 - 3 years",
    "publishedOn": "2026-09-03T10:55:41.357Z",
}


class TestKekaDetection:
    def test_detect_provider_reads_the_tenant(self) -> None:
        assert detect_provider("https://blitznow.keka.com/careers/") == (
            "keka",
            "blitznow",
        )

    def test_www_is_not_mistaken_for_a_tenant(self) -> None:
        # Falls through the `www` filter; there is no other keka match to make.
        assert detect_provider("https://www.keka.com/pricing") is None

    def test_api_url_for_declines_keka(self) -> None:
        # Keka has no static API URL — it must go through ATSFetcher, and the
        # seed script relies on this returning None.
        assert api_url_for("https://blitznow.keka.com/careers/") is None

    def test_source_layer_routes_keka_to_ats_api(self) -> None:
        provider, _marker = detect_ats_provider("https://blitznow.keka.com/careers/")
        assert provider == "keka"
        assert detect_fetch_tier("https://blitznow.keka.com/careers/") == "ats_api"


class TestKekaBoardId:
    def test_pulls_the_guid_out_of_a_document_path(self) -> None:
        assert _keka_board_id(PORTAL_INFO) == BOARD_ID

    def test_none_when_no_document_path_is_present(self) -> None:
        assert _keka_board_id({"name": "Blitz", "colorCode": "#fff"}) is None


class TestKekaNormalise:
    def test_maps_every_field_reconcile_needs(self) -> None:
        row = _keka(ACTIVE_JOB, "blitznow")
        assert row["external_id"] == "159617"
        assert row["title"] == "Senior Program Manager - Customer Experience"
        assert row["department"] == "Central Operations"
        assert row["location_raw"] == "Bengaluru, Karnataka"
        assert row["published_at"] == "2026-09-03T10:55:41.357Z"

    def test_description_html_is_reduced_to_text(self) -> None:
        row = _keka(ACTIVE_JOB, "blitznow")
        assert "<" not in row["description_text"]
        assert "customer experience" in row["description_text"].lower()

    def test_apply_url_is_the_public_job_detail_page(self) -> None:
        row = _keka(ACTIVE_JOB, "blitznow")
        assert row["application_url"] == KEKA_JOB_DETAIL.format(
            tenant="blitznow", job_id=159617
        )

    def test_missing_location_does_not_raise(self) -> None:
        row = _keka({"id": 1, "title": "Role", "jobLocations": []}, "blitznow")
        assert row["location_raw"] is None
        assert row["external_id"] == "1"

    def test_location_falls_back_to_name_when_city_absent(self) -> None:
        job = {"id": 2, "title": "Role", "jobLocations": [{"name": "Remote, India"}]}
        assert _keka(job, "blitznow")["location_raw"] == "Remote, India"
