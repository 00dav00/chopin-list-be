import pytest

from app.tasks import (
    DEFAULT_GROUPS,
    seed_default_groups,
    set_user_admin_by_email,
    toggle_user_approved_by_email,
)


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


@pytest.mark.asyncio
async def test_set_user_admin_by_email_updates_user(db):
    email = "user@example.com"
    await db.users.insert_one(
        {
            "google_sub": "sub-123",
            "email": email,
            "admin": False,
        }
    )

    admin = await set_user_admin_by_email(db=db, email=email, is_admin=True)
    assert admin is True

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["admin"] is True

    admin = await set_user_admin_by_email(db=db, email=email, is_admin=False)
    assert admin is False

    stored = await db.users.find_one({"email": email})
    assert stored is not None
    assert stored["admin"] is False


@pytest.mark.asyncio
async def test_set_user_admin_by_email_returns_none_when_user_not_found(db):
    admin = await set_user_admin_by_email(
        db=db,
        email="missing@example.com",
        is_admin=True,
    )
    assert admin is None


@pytest.mark.asyncio
async def test_seed_default_groups_inserts_defaults_in_order(db):
    inserted = await seed_default_groups(db=db)

    assert inserted == len(DEFAULT_GROUPS)
    docs = await db.groups.find({}).sort("position", 1).to_list(length=None)
    assert [doc["name"] for doc in docs] == DEFAULT_GROUPS
    assert [doc["position"] for doc in docs] == list(range(len(DEFAULT_GROUPS)))
    assert all(doc["name_key"] == doc["name"].casefold() for doc in docs)


@pytest.mark.asyncio
async def test_seed_default_groups_refuses_when_any_group_exists(db):
    await db.groups.insert_one({"name": "Dairy", "name_key": "dairy", "position": 0})

    inserted = await seed_default_groups(db=db)

    assert inserted is None
    assert await db.groups.count_documents({}) == 1


@pytest.mark.asyncio
async def test_seed_default_groups_twice_inserts_once(db):
    first = await seed_default_groups(db=db)
    second = await seed_default_groups(db=db)

    assert first == len(DEFAULT_GROUPS)
    assert second is None
    assert await db.groups.count_documents({}) == len(DEFAULT_GROUPS)


def test_default_groups_are_unique_and_never_other():
    keys = [name.casefold() for name in DEFAULT_GROUPS]
    assert len(keys) == len(set(keys))
    assert "other" not in keys
