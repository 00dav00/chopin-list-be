import pytest

from app.tasks import toggle_user_approved_by_email


@pytest.mark.asyncio
async def test_toggle_user_approved_by_email_updates_user(db):
    email = "user@example.com"
    await db.users.insert_one(
        {
            "google_sub": "sub-123",
            "email": email,
            "approved": False,
        }
    )

    approved = await toggle_user_approved_by_email(db=db, email=email)
    assert approved is True

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["approved"] is True

    approved = await toggle_user_approved_by_email(db=db, email=email)
    assert approved is False

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["approved"] is False


@pytest.mark.asyncio
async def test_toggle_user_approved_by_email_returns_none_when_user_not_found(db):
    approved = await toggle_user_approved_by_email(db=db, email="missing@example.com")
    assert approved is None
