import pytest
from fastapi import HTTPException

from app import auth


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
