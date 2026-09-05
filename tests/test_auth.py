import asyncio
import logging

import pytest
from fastapi import HTTPException

from app import auth, notifications


@pytest.mark.asyncio
async def test_missing_authorization_header_returns_401(db):
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization=None, db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_bearer_prefix_no_token_returns_401(db):
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer ", db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401(db, monkeypatch):
    def raise_error(*args, **kwargs):
        raise ValueError("bad token")

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", raise_error)

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer invalid", db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_invalid_issuer_returns_401(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "https://invalid.example.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer valid", db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_creates_user_on_hold_and_returns_403(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer valid", db=db)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account pending approval."

    stored = await db.users.find_one({"google_sub": "sub123"})
    assert stored is not None
    assert stored["approved"] is False
    assert stored["admin"] is False
    assert stored["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_get_current_user_returns_approved_user(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    await db.users.insert_one(
        {
            "google_sub": "sub123",
            "approved": True,
            "email": "old@example.com",
        }
    )
    user = await auth.get_current_user(authorization="Bearer valid", db=db)

    assert user["email"] == "user@example.com"
    assert user["name"] == "Test User"
    assert user["admin"] is False
    assert "id" in user


@pytest.mark.asyncio
async def test_get_current_user_preserves_existing_admin_status(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    await db.users.insert_one(
        {
            "google_sub": "sub123",
            "approved": True,
            "admin": True,
            "email": "old@example.com",
        }
    )
    user = await auth.get_current_user(authorization="Bearer valid", db=db)

    assert user["admin"] is True


@pytest.mark.asyncio
async def test_get_current_user_rejects_unapproved_existing_user(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    await db.users.insert_one(
        {
            "google_sub": "sub123",
            "approved": False,
            "email": "old@example.com",
        }
    )
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer valid", db=db)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account pending approval."


@pytest.mark.asyncio
async def test_get_current_user_ignores_admin_flag_from_google_payload(db, monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
            "admin": True,
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)

    await db.users.insert_one(
        {
            "google_sub": "sub123",
            "approved": True,
            "admin": False,
            "email": "old@example.com",
        }
    )

    user = await auth.get_current_user(authorization="Bearer valid", db=db)
    assert user["admin"] is False

    stored = await db.users.find_one({"google_sub": "sub123"})
    assert stored is not None
    assert stored["admin"] is False


# ---------------------------------------------------------------------------
# New-user admin notification -- trigger half. The module's own behaviour is in
# tests/test_notifications.py (AGENTS.md: one test file per module). These drive
# the real Mongo upsert: the discriminator compares two stored timestamps, so a
# fabricated document would pass against a broken comparison and prove nothing.
# ---------------------------------------------------------------------------


@pytest.fixture
def google_payload(monkeypatch):
    def fake_verify(*args, **kwargs):
        return {
            "sub": "sub123",
            "email": "user@example.com",
            "name": "Test User",
            "picture": "https://example.com/avatar.png",
            "iss": "accounts.google.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_oauth2_token", fake_verify)


@pytest.fixture
def dispatched(monkeypatch):
    """Record notification dispatches without scheduling a real send."""
    calls = []
    monkeypatch.setattr(
        auth,
        "dispatch_new_user_notification",
        lambda name, email: calls.append((name, email)),
    )
    return calls


@pytest.mark.asyncio
async def test_first_sign_in_dispatches_admin_notification(
    db, google_payload, dispatched
):
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user(authorization="Bearer valid", db=db)
    assert exc.value.status_code == 403

    assert dispatched == [("Test User", "user@example.com")]


@pytest.mark.asyncio
async def test_reauthentication_does_not_dispatch_admin_notification(
    db, google_payload, dispatched
):
    # First sign-in creates the pending account and 403s by design.
    with pytest.raises(HTTPException):
        await auth.get_current_user(authorization="Bearer valid", db=db)
    assert len(dispatched) == 1

    # An admin approves them.
    await db.users.update_one(
        {"google_sub": "sub123"}, {"$set": {"approved": True}}
    )

    # Both timestamps are millisecond-truncated by BSON, so a re-auth inside the
    # same millisecond as the insert would misfire. Step past it so this asserts
    # the discriminator rather than the clock.
    await asyncio.sleep(0.01)

    # They come back. No second notification.
    await auth.get_current_user(authorization="Bearer valid", db=db)
    assert len(dispatched) == 1

    stored = await db.users.find_one({"google_sub": "sub123"})
    assert stored["created_at"] != stored["last_login_at"]


@pytest.mark.asyncio
async def test_broken_smtp_still_yields_403_not_5xx(
    db, google_payload, monkeypatch, caplog
):
    """AC: an unreachable relay must not change the auth outcome."""
    monkeypatch.setattr(notifications.settings, "mail_dry_run", False)
    monkeypatch.setattr(notifications.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(notifications.settings, "smtp_port", 587)
    monkeypatch.setattr(notifications.settings, "mail_from", "no-reply@example.com")
    monkeypatch.setattr(
        notifications.settings, "mail_admin_to", "admin@example.com"
    )

    async def explode(*args, **kwargs):
        raise ConnectionRefusedError("relay is down")

    monkeypatch.setattr(notifications.aiosmtplib, "send", explode)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as exc:
            await auth.get_current_user(authorization="Bearer valid", db=db)
        # The normal pending-approval 403, not a 500.
        assert exc.value.status_code == 403
        assert exc.value.detail == "Account pending approval."

        # Drain the fire-and-forget send so its failure is observable here.
        for task in set(notifications._pending_sends):
            await task

    assert "Admin notification failed" in caplog.text
