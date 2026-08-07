"""Newsletter routes — public subscribe / confirm / unsubscribe."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import ACCESS_TOKEN_TYPE, get_user_by_id, verify_jwt
from app.database import get_db
from app.newsletter import service
from app.newsletter.schemas import (
    SubscribeRequest,
    SubscribeResponse,
    TokenRequest,
)

router = APIRouter(prefix="/newsletter", tags=["newsletter"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Subscribing works signed out, but links to the account when a valid token is
# present — hence an optional bearer rather than the CurrentUser dependency.
_optional_bearer = HTTPBearer(auto_error=False)


async def _optional_user_id(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_optional_bearer)
    ],
    db: DbSession,
):
    if credentials is None:
        return None
    try:
        payload = verify_jwt(credentials.credentials, expected_type=ACCESS_TOKEN_TYPE)
        user = await get_user_by_id(db, payload["sub"])
    except Exception:  # noqa: BLE001 — a bad token just means "anonymous" here
        return None
    return user.id if user else None


@router.post("/subscribe", response_model=SubscribeResponse)
async def subscribe(
    data: SubscribeRequest,
    db: DbSession,
    user_id: Annotated[object, Depends(_optional_user_id)] = None,
) -> SubscribeResponse:
    await service.subscribe(db, str(data.email), user_id)  # type: ignore[arg-type]
    # Always the same response, whether or not the address was already known.
    return SubscribeResponse(message=service.SUBSCRIBE_MESSAGE)


@router.post("/confirm", response_model=SubscribeResponse)
async def confirm(data: TokenRequest, db: DbSession) -> SubscribeResponse:
    subscriber = await service.confirm_subscription(db, data.token)
    return SubscribeResponse(
        message=f"Subscription confirmed for {subscriber.email}."
    )


@router.post("/unsubscribe", response_model=SubscribeResponse)
async def unsubscribe(data: TokenRequest, db: DbSession) -> SubscribeResponse:
    subscriber = await service.unsubscribe(db, data.token)
    return SubscribeResponse(
        message=f"{subscriber.email} has been unsubscribed."
    )
