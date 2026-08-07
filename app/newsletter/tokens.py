"""Signed, stateless tokens for newsletter confirm / unsubscribe links.

The spec called for storing these in Redis. They are HMAC-signed instead, and the
difference matters most for unsubscribe: those links live forever inside emails
already sitting in people's inboxes. A Redis-backed token stops working the
moment a free-tier instance evicts a key or is reprovisioned, which silently
breaks unsubscribe for every email already delivered — a legal problem under
CAN-SPAM and GDPR, not just a bug.

A signed token needs no storage, cannot be enumerated, and survives losing Redis
entirely. The tradeoff is that it cannot be revoked without rotating
JWT_SECRET_KEY; for an unsubscribe link, never expiring is the desired behaviour.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from typing import Literal

from app.config import settings

TokenPurpose = Literal["confirm", "unsubscribe"]

_DIGEST = hashlib.sha256


class InvalidToken(ValueError):
    """Token was malformed, tampered with, expired, or issued for another purpose."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload: bytes) -> bytes:
    return hmac.new(
        settings.jwt_secret_key.encode(), payload, _DIGEST
    ).digest()


def generate(
    subscriber_id: uuid.UUID,
    purpose: TokenPurpose,
    *,
    ttl_seconds: int | None = None,
) -> str:
    """Mint a token binding a subscriber to one purpose.

    `purpose` is inside the signed payload so a confirmation token cannot be
    replayed against the unsubscribe endpoint or vice versa.
    """
    claims: dict[str, object] = {"sub": str(subscriber_id), "purpose": purpose}
    if ttl_seconds is not None:
        claims["exp"] = int(time.time()) + ttl_seconds

    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64encode(payload)}.{_b64encode(_sign(payload))}"


def verify(token: str, purpose: TokenPurpose) -> uuid.UUID:
    """Return the subscriber id, or raise InvalidToken."""
    try:
        payload_b64, signature_b64 = token.split(".", 1)
        payload = _b64decode(payload_b64)
        signature = _b64decode(signature_b64)
    except (ValueError, TypeError) as exc:
        raise InvalidToken("This link is malformed.") from exc

    # compare_digest, not ==, so verification time does not leak the signature.
    if not hmac.compare_digest(signature, _sign(payload)):
        raise InvalidToken("This link is not valid.")

    try:
        claims = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise InvalidToken("This link is malformed.") from exc

    if claims.get("purpose") != purpose:
        raise InvalidToken("This link is not valid for this action.")

    expiry = claims.get("exp")
    if expiry is not None and time.time() > expiry:
        raise InvalidToken("This link has expired. Request a new one.")

    try:
        return uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise InvalidToken("This link is malformed.") from exc


def confirm_url(subscriber_id: uuid.UUID) -> str:
    token = generate(
        subscriber_id,
        "confirm",
        ttl_seconds=settings.newsletter_confirm_ttl_hours * 3600,
    )
    return f"{settings.frontend_url.rstrip('/')}/newsletter/confirm?token={token}"


def unsubscribe_url(subscriber_id: uuid.UUID) -> str:
    # No TTL: this link must still work years after the email was sent.
    token = generate(subscriber_id, "unsubscribe")
    return f"{settings.frontend_url.rstrip('/')}/newsletter/unsubscribe?token={token}"
