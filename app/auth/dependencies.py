"""Authentication dependencies for protected routes."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.auth.service import ACCESS_TOKEN_TYPE, get_user_by_id, verify_jwt
from app.common.exceptions import AuthenticationError, AuthorizationError
from app.database import get_db

# auto_error=False so a missing header surfaces as our own 401 shape.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Missing Authorization header.")

    payload = verify_jwt(credentials.credentials, expected_type=ACCESS_TOKEN_TYPE)
    user = await get_user_by_id(db, payload["sub"])

    if user is None:
        raise AuthenticationError("User no longer exists.")
    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise AuthorizationError("This endpoint requires admin privileges.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
