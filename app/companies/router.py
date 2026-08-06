"""Company routes — public reads, admin writes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AdminUser
from app.common.pagination import Page, PageParams, page_params
from app.companies import service
from app.companies.schemas import (
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
    CompanyWithScore,
)
from app.database import get_db

router = APIRouter(prefix="/companies", tags=["companies"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
Pagination = Annotated[PageParams, Depends(page_params)]


@router.get("", response_model=Page[CompanyResponse])
async def list_companies(
    db: DbSession,
    params: Pagination,
    industry: Annotated[str | None, Query()] = None,
    stage: Annotated[str | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[CompanyResponse]:
    companies, total = await service.list_companies(
        db, params, industry=industry, stage=stage, is_active=is_active, search=search
    )
    return Page.build(
        [CompanyResponse.model_validate(c) for c in companies], total, params
    )


@router.get("/{slug}", response_model=CompanyWithScore)
async def get_company(slug: str, db: DbSession) -> CompanyWithScore:
    company = await service.get_company_by_slug(db, slug)
    payload = CompanyWithScore.model_validate(company)

    score = await service.get_latest_score(db, company.id)
    if score is not None:
        payload.momentum_score = score.momentum_score
        payload.momentum_label = score.momentum_label
        payload.scored_at = score.scored_at

    return payload


@router.post(
    "", response_model=CompanyResponse, status_code=status.HTTP_201_CREATED
)
async def create_company(
    data: CompanyCreate, db: DbSession, _admin: AdminUser
) -> CompanyResponse:
    company = await service.create_company(db, data)
    return CompanyResponse.model_validate(company)


@router.patch("/{slug}", response_model=CompanyResponse)
async def update_company(
    slug: str, data: CompanyUpdate, db: DbSession, _admin: AdminUser
) -> CompanyResponse:
    company = await service.update_company(db, slug, data)
    return CompanyResponse.model_validate(company)
