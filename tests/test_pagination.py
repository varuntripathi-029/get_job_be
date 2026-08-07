"""Pagination arithmetic."""

from app.common.pagination import PaginatedResponse, PaginationParams


def test_offset_is_derived_from_page() -> None:
    assert PaginationParams(page=1, per_page=20).offset == 0
    assert PaginationParams(page=2, per_page=20).offset == 20
    assert PaginationParams(page=5, per_page=10).offset == 40


def test_total_pages_rounds_up() -> None:
    params = PaginationParams(page=1, per_page=10)
    assert PaginatedResponse.build([], 0, params).total_pages == 0
    assert PaginatedResponse.build([], 1, params).total_pages == 1
    assert PaginatedResponse.build([], 10, params).total_pages == 1
    assert PaginatedResponse.build([], 11, params).total_pages == 2
    assert PaginatedResponse.build([], 100, params).total_pages == 10


def test_empty_result_reports_zero_pages_not_one() -> None:
    """'Page 1 of 0' is the honest rendering of an empty list."""
    page = PaginatedResponse.build([], 0, PaginationParams(page=1, per_page=20))
    assert page.total_pages == 0
    assert page.has_next is False
    assert page.has_prev is False


def test_has_next_and_prev_across_pages() -> None:
    first = PaginatedResponse.build([], 45, PaginationParams(page=1, per_page=20))
    middle = PaginatedResponse.build([], 45, PaginationParams(page=2, per_page=20))
    last = PaginatedResponse.build([], 45, PaginationParams(page=3, per_page=20))

    assert (first.has_prev, first.has_next) == (False, True)
    assert (middle.has_prev, middle.has_next) == (True, True)
    assert (last.has_prev, last.has_next) == (True, False)


def test_page_beyond_the_end_reports_no_next() -> None:
    page = PaginatedResponse.build([], 10, PaginationParams(page=9, per_page=20))
    assert page.has_next is False
    assert page.has_prev is True


def test_items_are_carried_through() -> None:
    page = PaginatedResponse.build(
        ["a", "b"], 2, PaginationParams(page=1, per_page=20)
    )
    assert page.items == ["a", "b"]
    assert page.total == 2
    assert page.per_page == 20
