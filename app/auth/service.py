"""Google ID token verification, JWT issuance, and user provisioning."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AuthenticationError
from app.config import settings

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# Google signs ID tokens with one of these issuers.
_GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


def verify_google_token(token: str) -> dict[str, Any]:
    """Verify a Google ID token and return its payload.

    Raises AuthenticationError if the token is invalid, expired, or was issued
    for a different client.
    """
    if not settings.google_client_id:
        raise AuthenticationError(
            "GOOGLE_CLIENT_ID is not configured; cannot verify Google tokens."
        )

    try:
        payload = google_id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            settings.google_client_id,
        )
    except ValueError as exc:
        # verify_oauth2_token raises ValueError for every failure mode:
        # bad signature, expired, wrong audience.
        logger.warning("google_token_verification_failed: %s", exc)
        raise AuthenticationError("Invalid or expired Google token.") from exc

    if payload.get("iss") not in _GOOGLE_ISSUERS:
        raise AuthenticationError("Unexpected token issuer.")
    if not payload.get("sub"):
        raise AuthenticationError("Google token is missing the 'sub' claim.")
    if not payload.get("email"):
        raise AuthenticationError("Google token is missing an email address.")

    return payload


async def get_or_create_user(
    db: AsyncSession, google_payload: dict[str, Any]
) -> tuple[User, bool]:
    """Find the user by Google `sub`, creating them on first sign-in.

    Returns (user, was_created). The very first user to sign in becomes admin so
    the instance has someone who can approve sources; everyone after is 'user'.
    """
    google_id = str(google_payload["sub"])
    email = str(google_payload["email"]).lower()
    name = str(google_payload.get("name") or email.split("@")[0])
    avatar_url = google_payload.get("picture")

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if user is None:
        # An account may pre-exist with this email from a prior identity; adopt it.
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            user.google_id = google_id

    created = False
    if user is None:
        admin_exists = await db.scalar(
            select(func.count()).select_from(User).where(User.role == "admin")
        )
        role = "user" if admin_exists else "admin"
        if role == "admin":
            logger.warning(
                "bootstrapping_first_admin email=%s — no admin existed yet", email
            )

        user = User(
            email=email,
            name=name,
            google_id=google_id,
            avatar_url=avatar_url,
            role=role,
        )
        db.add(user)
        created = True

    if not user.is_active:
        raise AuthenticationError("This account has been deactivated.")

    user.name = name
    user.avatar_url = avatar_url
    user.last_login_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(user)
    return user, created


def _create_token(user_id: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(
        payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm
    )


def create_access_token(user_id: str | uuid.UUID) -> str:
    return _create_token(
        str(user_id),
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )


def create_refresh_token(user_id: str | uuid.UUID) -> str:
    return _create_token(
        str(user_id),
        REFRESH_TOKEN_TYPE,
        timedelta(days=settings.jwt_refresh_token_expire_days),
    )


def verify_jwt(token: str, *, expected_type: str | None = None) -> dict[str, Any]:
    """Decode and validate one of our own JWTs.

    `expected_type` guards against a refresh token being replayed as an access
    token (and vice versa).
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token.") from exc

    if expected_type and payload.get("type") != expected_type:
        raise AuthenticationError(
            f"Expected a {expected_type} token, got {payload.get('type')!r}."
        )
    if not payload.get("sub"):
        raise AuthenticationError("Token is missing a subject.")

    return payload


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    try:
        parsed = uuid.UUID(user_id)
    except ValueError:
        return None
    return await db.get(User, parsed)


def issue_token_pair(user: User) -> tuple[str, str]:
    return create_access_token(user.id), create_refresh_token(user.id)
