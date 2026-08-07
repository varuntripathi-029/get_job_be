"""Auth routes — Google sign-in, token refresh, current user."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.dependencies import CurrentUser
from app.auth.schemas import (
    GoogleAuthRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.common.exceptions import AuthenticationError
from app.common.rate_limit import rate_limit_auth
from app.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post(
    "/google",
    response_model=TokenResponse,
    summary="Sign in with a Google ID token",
    dependencies=[Depends(rate_limit_auth)],
)
async def google_sign_in(payload: GoogleAuthRequest, db: DbSession) -> TokenResponse:
    """Exchange a Google ID token for a HireSignal token pair."""
    google_payload = service.verify_google_token(payload.credential)
    user, _created = await service.get_or_create_user(db, google_payload)
    access_token, refresh_token = service.issue_token_pair(user)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token",
    dependencies=[Depends(rate_limit_auth)],
)
async def refresh_tokens(payload: RefreshRequest, db: DbSession) -> TokenResponse:
    """Exchange a valid refresh token for a fresh pair."""
    claims = service.verify_jwt(
        payload.refresh_token, expected_type=service.REFRESH_TOKEN_TYPE
    )
    user = await service.get_user_by_id(db, claims["sub"])

    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is deactivated.")

    access_token, refresh_token = service.issue_token_pair(user)
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def read_current_user(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
