"""Signed newsletter token behaviour."""

import time
import uuid

import pytest

from app.newsletter import tokens


def test_roundtrip_returns_the_subscriber_id() -> None:
    subscriber_id = uuid.uuid4()
    token = tokens.generate(subscriber_id, "unsubscribe")
    assert tokens.verify(token, "unsubscribe") == subscriber_id


def test_confirm_token_is_rejected_by_the_unsubscribe_endpoint() -> None:
    """Purpose is signed, so a confirmation link cannot unsubscribe anyone."""
    token = tokens.generate(uuid.uuid4(), "confirm")
    with pytest.raises(tokens.InvalidToken):
        tokens.verify(token, "unsubscribe")


def test_tampering_with_the_payload_invalidates_the_signature() -> None:
    real_id = uuid.uuid4()
    token = tokens.generate(real_id, "unsubscribe")
    payload, signature = token.split(".")

    forged = tokens.generate(uuid.uuid4(), "unsubscribe").split(".")[0]
    with pytest.raises(tokens.InvalidToken):
        tokens.verify(f"{forged}.{signature}", "unsubscribe")


def test_expired_token_is_rejected() -> None:
    token = tokens.generate(uuid.uuid4(), "confirm", ttl_seconds=-1)
    with pytest.raises(tokens.InvalidToken, match="expired"):
        tokens.verify(token, "confirm")


def test_unexpired_token_is_accepted() -> None:
    subscriber_id = uuid.uuid4()
    token = tokens.generate(subscriber_id, "confirm", ttl_seconds=60)
    assert tokens.verify(token, "confirm") == subscriber_id


def test_unsubscribe_tokens_never_expire() -> None:
    """Unsubscribe links live in already-delivered mail, so they must keep
    working indefinitely."""
    subscriber_id = uuid.uuid4()
    token = tokens.generate(subscriber_id, "unsubscribe")
    payload = token.split(".")[0]
    # No `exp` claim at all, so no clock can invalidate it.
    assert "exp" not in tokens._b64decode(payload).decode()
    assert tokens.verify(token, "unsubscribe") == subscriber_id


@pytest.mark.parametrize(
    "garbage", ["", "no-dot", "a.b", "....", "!!!.???"]
)
def test_malformed_tokens_raise_rather_than_crash(garbage: str) -> None:
    with pytest.raises(tokens.InvalidToken):
        tokens.verify(garbage, "confirm")


def test_urls_point_at_the_configured_frontend() -> None:
    subscriber_id = uuid.uuid4()
    assert tokens.confirm_url(subscriber_id).startswith(
        "http://localhost:5173/newsletter/confirm?token="
    )
    assert tokens.unsubscribe_url(subscriber_id).startswith(
        "http://localhost:5173/newsletter/unsubscribe?token="
    )


def test_tokens_are_stable_across_calls() -> None:
    """No randomness, so re-rendering an email produces the same link."""
    subscriber_id = uuid.uuid4()
    assert tokens.generate(subscriber_id, "unsubscribe") == tokens.generate(
        subscriber_id, "unsubscribe"
    )


def test_expiry_is_in_the_future_for_a_positive_ttl() -> None:
    before = int(time.time())
    token = tokens.generate(uuid.uuid4(), "confirm", ttl_seconds=3600)
    payload = tokens._b64decode(token.split(".")[0]).decode()
    assert '"exp"' in payload
    assert str(before + 3600)[:6] in payload
