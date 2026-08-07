"""Offset pagination shared by every list endpoint.

Offset, not cursor: the largest table here is jobs, in the low thousands. Cursor
pagination earns its complexity when deep offsets get slow, which needs orders of
magnitude more rows than this project will hold.

Every list endpoint returns `PaginatedResponse`, so the frontend has one shape to
handle rather than one per resource.
"""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        return self.per_page


def get_pagination(
    page: Annotated[int, Query(ge=1, description="1-indexed page number.")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=MAX_PER_PAGE, description="Items per page.")
    ] = DEFAULT_PER_PAGE,
) -> PaginationParams:
    """FastAPI dependency for `?page=&per_page=`."""
    return PaginationParams(page=page, per_page=per_page)


Pagination = Annotated[PaginationParams, get_pagination]


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def build(
        cls, items: list[T], total: int, params: PaginationParams
    ) -> "PaginatedResponse[T]":
        # ceil without importing math; total 0 yields 0 pages rather than 1, so
        # "page 1 of 0" never appears in an empty result.
        total_pages = -(-total // params.per_page) if total else 0
        return cls(
            items=items,
            total=total,
            page=params.page,
            per_page=params.per_page,
            total_pages=total_pages,
            has_next=params.page < total_pages,
            has_prev=params.page > 1 and total > 0,
        )


async def count_query(db: AsyncSession, stmt: Select) -> int:
    """Total rows a SELECT would return, ignoring its ordering.

    `order_by(None)` matters: Postgres rejects ORDER BY on columns not in the
    subquery's select list, which a joined+ordered statement often has.
    """
    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    return int(total or 0)


async def paginate[T](
    db: AsyncSession,
    stmt: Select,
    params: PaginationParams,
    mapper,
) -> PaginatedResponse[T]:
    """Count, slice, and wrap a statement in one round trip pair.

    `mapper` turns a result row into the response model.
    """
    total = await count_query(db, stmt)
    result = await db.execute(stmt.limit(params.limit).offset(params.offset))
    return PaginatedResponse.build([mapper(row) for row in result.all()], total, params)
