"""Offset pagination helpers shared by list endpoints."""

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 100


class PageParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    """FastAPI dependency for `?limit=&offset=`."""
    return PageParams(limit=limit, offset=offset)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=items, total=total, limit=params.limit, offset=params.offset
        )
