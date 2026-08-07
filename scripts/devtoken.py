"""Mint a local access token without going through Google.

Run: `uv run python -m scripts.devtoken`

Production sign-in is Google OAuth only, which needs a frontend to obtain a
Google ID token. That does not exist yet, so there is no way to exercise an
authenticated endpoint from Swagger. This issues a token directly with the same
signing key the API verifies against.

Refuses to run unless ENVIRONMENT=development. The whole point of the OAuth-only
design is that no credential path exists besides Google; this must never become
one in a deployed environment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

import app.models  # noqa: F401  — registers every model on Base.metadata
from app.auth.models import User
from app.auth.service import issue_token_pair
from app.config import settings
from app.database import AsyncSessionLocal

# A real TLD is required: UserResponse validates as EmailStr, so "dev@localhost"
# mints a token fine and then 500s on GET /auth/me. example.com is reserved by
# RFC 2606 and can never belong to anyone.
DEFAULT_EMAIL = "dev@example.com"


async def mint(email: str, *, admin: bool) -> tuple[User, str, str]:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))

        if user is None:
            # Mirrors get_or_create_user: the first account bootstraps to admin
            # so there is somebody who can approve sources.
            total = await db.scalar(select(func.count()).select_from(User)) or 0
            user = User(
                email=email,
                name="Local Developer",
                # Namespaced so it can never collide with a real Google subject.
                google_id=f"dev-local|{email}",
                role="admin" if (admin or total == 0) else "user",
                is_active=True,
            )
            db.add(user)
        elif admin:
            user.role = "admin"

        user.last_login_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        access, refresh = issue_token_pair(user)
        return user, access, refresh


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument(
        "--admin", action="store_true", help="Force the admin role."
    )
    args = parser.parse_args()

    if settings.environment.lower() not in ("development", "test", "local"):
        print(
            f"refusing to mint a token with ENVIRONMENT={settings.environment!r}. "
            "This bypasses Google sign-in and is for local use only.",
            file=sys.stderr,
        )
        return 1

    user, access, refresh = asyncio.run(mint(args.email, admin=args.admin))

    minutes = settings.jwt_access_token_expire_minutes
    print(f"user    : {user.email}  role={user.role}  id={user.id}")
    print(f"expires : {minutes} minutes")
    print()
    print("Paste this into Swagger's Authorize box (the value only, no 'Bearer'):")
    print()
    print(access)
    print()
    print("curl:")
    print(f'  curl.exe -H "Authorization: Bearer {access[:24]}..." \\')
    print("       http://localhost:8000/auth/me")
    print()
    print(f"refresh token (POST /auth/refresh): {refresh[:24]}...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
