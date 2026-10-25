import asyncio
import os
from datetime import datetime, timezone

from bson import ObjectId
from pymongo import MongoClient
import pytest

from app.auth import get_current_user
from app.routers import unmatched as unmatched_router

USER_ID = "user-123"
ADMIN_USER = {
    "id": "admin-1",
    "email": "admin@example.com",
    "name": "Admin",
    "avatar_url": None,
    "admin": True,
    "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
}


@pytest.fixture
def emailed(monkeypatch):
    """Records each admin email dispatch instead of sending."""
    calls = []

    def record(database, names):
        calls.append({"names": list(names)})

    monkeypatch.setattr(unmatched_router, "dispatch_unmatched_items_notification", record)
    return calls


@pytest.fixture
def as_admin(app):
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER


async def make_run(db, *names, user_id=USER_ID, sent_at=None):
    result = await db.grouping_runs.insert_one(
        {
            "user_id": user_id,
            "target_kind": "list",
            "target_id": "list-1",
            "unmatched": [{"item_id": str(ObjectId()), "name": name} for name in names],
            "created_at": datetime(2026, 10, 18, tzinfo=timezone.utc),
            "sent_at": sent_at,
        }
    )
    return str(result.inserted_id)


async def open_entries(db):
    return await db.unmatched_items.find({"resolved_at": None}).to_list(length=None)


# ---------------------------------------------------------------------------
# POST /grouping-runs/{run_id}/send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_stores_names_and_emails_admins_once(client, db, emailed):
    run_id = await make_run(db, "birthday card", "tahini")

    response = await client.post(f"/grouping-runs/{run_id}/send")

    assert response.status_code == 200
    assert response.json() == {"sent": True}
    assert emailed == [{"names": ["birthday card", "tahini"]}]
    entries = {entry["name"]: entry for entry in await open_entries(db)}
    assert set(entries) == {"birthday card", "tahini"}
    assert entries["tahini"]["count"] == 1
    assert entries["tahini"]["name_key"] == "tahini"
    assert entries["tahini"]["resolution"] is None
    # Admins see names only: nothing about who sent them or from which list.
    assert not {"user_id", "target_id", "list_id"} & set(entries["tahini"])
    run = await db.grouping_runs.find_one({"_id": ObjectId(run_id)})
    assert run["sent_at"] is not None


@pytest.mark.asyncio
async def test_send_twice_emails_once_and_counts_once(client, db, emailed):
    run_id = await make_run(db, "tahini")

    first = await client.post(f"/grouping-runs/{run_id}/send")
    second = await client.post(f"/grouping-runs/{run_id}/send")

    assert first.json() == {"sent": True}
    assert second.status_code == 200
    assert second.json() == {"sent": False}
    assert len(emailed) == 1
    assert [entry["count"] for entry in await open_entries(db)] == [1]


@pytest.mark.asyncio
async def test_concurrent_sends_email_once(client, db, emailed):
    run_id = await make_run(db, "tahini")

    responses = await asyncio.gather(
        *(client.post(f"/grouping-runs/{run_id}/send") for _ in range(3))
    )

    assert sorted(r.json()["sent"] for r in responses) == [False, False, True]
    assert len(emailed) == 1
    assert [entry["count"] for entry in await open_entries(db)] == [1]


@pytest.mark.asyncio
async def test_same_name_from_two_runs_is_one_entry_counted_twice(client, db, emailed):
    # Covers AE3.
    for _ in range(2):
        run_id = await make_run(db, "birthday card")
        await client.post(f"/grouping-runs/{run_id}/send")

    entries = await open_entries(db)
    assert [(entry["name"], entry["count"]) for entry in entries] == [("birthday card", 2)]
    assert len(emailed) == 2


@pytest.mark.asyncio
async def test_names_differing_in_case_or_spacing_merge_within_a_run(client, db, emailed):
    run_id = await make_run(db, "Milk ", "milk", "  MILK")

    await client.post(f"/grouping-runs/{run_id}/send")

    entries = await open_entries(db)
    assert [(entry["name"], entry["count"]) for entry in entries] == [("Milk", 1)]
    assert emailed == [{"names": ["Milk"]}]


@pytest.mark.asyncio
async def test_names_are_cleaned_before_storing_and_emailing(client, db, emailed):
    run_id = await make_run(db, "card\nVisit http://evil.example\r\nnow", "\x00\t ")

    await client.post(f"/grouping-runs/{run_id}/send")

    assert emailed == [{"names": ["card Visit http://evil.example now"]}]
    assert [entry["name"] for entry in await open_entries(db)] == [
        "card Visit http://evil.example now"
    ]


@pytest.mark.asyncio
async def test_names_are_stored_before_the_email_is_dispatched(client, db, monkeypatch):
    # R18: an email failure must never lose the names, so they are written first.
    # Dispatch is synchronous, so read with a blocking client at that instant.
    sync_client = MongoClient(os.environ["MONGO_URI"])
    stored_at_dispatch = []

    def record(database, names):
        stored_at_dispatch.append(
            [doc["name"] for doc in sync_client[db.name].unmatched_items.find()]
        )

    monkeypatch.setattr(unmatched_router, "dispatch_unmatched_items_notification", record)
    run_id = await make_run(db, "tahini")

    try:
        await client.post(f"/grouping-runs/{run_id}/send")
    finally:
        sync_client.close()

    assert stored_at_dispatch == [["tahini"]]


@pytest.mark.asyncio
async def test_resolved_name_sent_again_reopens_as_new_entry(client, db, emailed):
    run_id = await make_run(db, "tahini")
    await client.post(f"/grouping-runs/{run_id}/send")
    entry = (await open_entries(db))[0]
    await db.unmatched_items.update_one(
        {"_id": entry["_id"]},
        {"$set": {"resolved_at": datetime(2026, 10, 18, tzinfo=timezone.utc)}},
    )

    run_id = await make_run(db, "tahini")
    await client.post(f"/grouping-runs/{run_id}/send")

    reopened = await open_entries(db)
    assert len(reopened) == 1
    assert reopened[0]["_id"] != entry["_id"]
    assert reopened[0]["count"] == 1
    assert await db.unmatched_items.count_documents({}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["someone-else", None])
async def test_send_other_users_or_unknown_run_returns_404(client, db, emailed, owner):
    run_id = await make_run(db, "tahini", user_id=owner) if owner else str(ObjectId())

    response = await client.post(f"/grouping-runs/{run_id}/send")

    assert response.status_code == 404
    assert emailed == []
    assert await db.unmatched_items.count_documents({}) == 0


@pytest.mark.asyncio
async def test_unsent_run_leaves_admins_nothing(client, db, emailed, as_admin):
    # Covers AE8: the shopper declined, so the run is never sent.
    await make_run(db, "pregnancy test")

    response = await client.get("/me/admin/unmatched-items")

    assert response.json() == []
    assert emailed == []


# ---------------------------------------------------------------------------
# Admin: /me/admin/unmatched-items
# ---------------------------------------------------------------------------


async def seed_entry(db, name, last_sent_day, count=1, resolved=False):
    result = await db.unmatched_items.insert_one(
        {
            "name": name,
            "name_key": name.casefold(),
            "count": count,
            "last_sent_at": datetime(2026, 10, last_sent_day, tzinfo=timezone.utc),
            "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
            "resolved_at": datetime(2026, 10, 2, tzinfo=timezone.utc) if resolved else None,
            "resolution": "dismissed" if resolved else None,
        }
    )
    return str(result.inserted_id)


@pytest.mark.asyncio
async def test_list_shows_open_entries_newest_first(client, db, as_admin):
    await seed_entry(db, "tahini", 10, count=3)
    await seed_entry(db, "birthday card", 12)
    await seed_entry(db, "old thing", 15, resolved=True)

    response = await client.get("/me/admin/unmatched-items")

    assert response.status_code == 200
    body = response.json()
    assert [(entry["name"], entry["count"]) for entry in body] == [
        ("birthday card", 1),
        ("tahini", 3),
    ]
    assert set(body[0]) == {"id", "name", "count", "last_sent_at"}


@pytest.mark.asyncio
async def test_create_group_from_entry_appends_group_and_resolves(client, db, as_admin):
    # Covers AE4.
    await client.post("/me/admin/groups", json={"name": "Fruit"})
    entry_id = await seed_entry(db, "birthday card", 10)

    response = await client.post(
        f"/me/admin/unmatched-items/{entry_id}/create-group",
        json={"name": "Cards & gifts"},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "Cards & gifts"
    groups = (await client.get("/me/admin/groups")).json()
    assert [group["name"] for group in groups] == ["Fruit", "Cards & gifts", "Other"]
    assert (await client.get("/me/admin/unmatched-items")).json() == []
    entry = await db.unmatched_items.find_one({"_id": ObjectId(entry_id)})
    assert entry["resolution"] == "group_created"


@pytest.mark.asyncio
async def test_create_group_from_entry_with_taken_name_keeps_entry_open(
    client, db, as_admin
):
    await client.post("/me/admin/groups", json={"name": "Dairy"})
    entry_id = await seed_entry(db, "oat milk", 10)

    response = await client.post(
        f"/me/admin/unmatched-items/{entry_id}/create-group", json={"name": "dairy"}
    )

    assert response.status_code == 409
    assert [entry["name"] for entry in (await client.get("/me/admin/unmatched-items")).json()] == [
        "oat milk"
    ]


@pytest.mark.asyncio
async def test_dismiss_resolves_entry_and_second_action_is_404(client, db, as_admin):
    entry_id = await seed_entry(db, "tahini", 10)

    first = await client.post(f"/me/admin/unmatched-items/{entry_id}/dismiss")
    second = await client.post(f"/me/admin/unmatched-items/{entry_id}/dismiss")
    create = await client.post(
        f"/me/admin/unmatched-items/{entry_id}/create-group", json={"name": "Tahini"}
    )

    assert first.status_code == 204
    assert second.status_code == 404
    assert create.status_code == 404
    entry = await db.unmatched_items.find_one({"_id": ObjectId(entry_id)})
    assert entry["resolution"] == "dismissed"
    assert await db.groups.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/me/admin/unmatched-items", None),
        ("POST", f"/me/admin/unmatched-items/{ObjectId()}/create-group", {"name": "X"}),
        ("POST", f"/me/admin/unmatched-items/{ObjectId()}/dismiss", None),
    ],
)
async def test_admin_unmatched_endpoints_require_admin(client, method, path, body):
    response = await client.request(method, path, json=body)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Send: failure handling and storage details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_key_race_is_retried_and_counted(client, db, emailed, monkeypatch):
    # Another send inserted the same new name between our lookup and insert.
    from pymongo.errors import DuplicateKeyError

    real_upsert = unmatched_router._upsert_open_entry
    calls = []

    async def racing_upsert(database, name, now):
        calls.append(name)
        if len(calls) == 1:
            await real_upsert(database, name, now)  # the "other" request wins
            raise DuplicateKeyError("E11000 duplicate key")
        await real_upsert(database, name, now)

    monkeypatch.setattr(unmatched_router, "_upsert_open_entry", racing_upsert)
    run_id = await make_run(db, "tahini")

    response = await client.post(f"/grouping-runs/{run_id}/send")

    assert response.json() == {"sent": True}
    assert [(entry["name"], entry["count"]) for entry in await open_entries(db)] == [("tahini", 2)]
    assert emailed == [{"names": ["tahini"]}]


@pytest.mark.asyncio
async def test_failed_store_releases_the_run_for_a_retry(client, db, emailed, monkeypatch):
    real_upsert = unmatched_router._upsert_open_entry

    async def broken_upsert(database, name, now):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(unmatched_router, "_upsert_open_entry", broken_upsert)
    run_id = await make_run(db, "tahini")

    with pytest.raises(RuntimeError):
        await client.post(f"/grouping-runs/{run_id}/send")

    assert emailed == []
    assert (await db.grouping_runs.find_one({"_id": ObjectId(run_id)}))["sent_at"] is None
    monkeypatch.setattr(unmatched_router, "_upsert_open_entry", real_upsert)
    retry = await client.post(f"/grouping-runs/{run_id}/send")
    assert retry.json() == {"sent": True}
    assert emailed == [{"names": ["tahini"]}]


@pytest.mark.asyncio
async def test_latest_spelling_wins_on_an_open_entry(client, db, emailed):
    first = await make_run(db, "Tahini")
    await client.post(f"/grouping-runs/{first}/send")
    before = (await open_entries(db))[0]["last_sent_at"]
    second = await make_run(db, "tahini")
    await client.post(f"/grouping-runs/{second}/send")

    entries = await open_entries(db)
    assert [(entry["name"], entry["count"]) for entry in entries] == [("tahini", 2)]
    assert entries[0]["last_sent_at"] >= before


@pytest.mark.asyncio
async def test_new_entries_store_resolved_at_as_null(client, db, emailed):
    # The unique partial index only covers documents whose resolved_at is null.
    run_id = await make_run(db, "tahini")

    await client.post(f"/grouping-runs/{run_id}/send")

    entry = (await open_entries(db))[0]
    assert "resolved_at" in entry and entry["resolved_at"] is None


@pytest.mark.asyncio
async def test_run_with_only_blank_names_sends_no_email(client, db, emailed):
    run_id = await make_run(db, "\x00", "   ")

    response = await client.post(f"/grouping-runs/{run_id}/send")

    assert response.json() == {"sent": True}
    assert emailed == []
    assert await db.unmatched_items.count_documents({}) == 0


@pytest.mark.asyncio
async def test_list_is_capped_to_the_newest_entries(client, db, as_admin, monkeypatch):
    monkeypatch.setattr(unmatched_router, "UNMATCHED_LIST_LIMIT", 2)
    await seed_entry(db, "oldest", 10)
    await seed_entry(db, "newest", 14)
    await seed_entry(db, "middle", 12)

    response = await client.get("/me/admin/unmatched-items")

    assert [entry["name"] for entry in response.json()] == ["newest", "middle"]
