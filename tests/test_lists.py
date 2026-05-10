from datetime import datetime, timedelta, timezone

from bson import ObjectId
import pytest
import time_machine

from app.routers.lists import _list_etag
from app.utils import mark_list_touched


CACHE_CONTROL = "private, no-cache"


def _strip_utc_suffix(value: str) -> str:
    if value.endswith("Z"):
        return value[:-1]
    if value.endswith("+00:00"):
        return value[:-6]
    return value


async def create_list(client, name="Groceries", template_id=None):
    payload = {"name": name}
    if template_id is not None:
        payload["template_id"] = template_id
    response = await client.post("/lists", json=payload)
    assert response.status_code == 201
    return response.json()


async def create_item(client, list_id, name="Milk", sort_order=0):
    payload = {"name": name, "sort_order": sort_order}
    response = await client.post(f"/lists/{list_id}/items", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_list_lists_empty(client):
    response = await client.get("/lists")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_list(client, db, current_user):
    data = await create_list(client, name="Weekly")
    assert data["name"] == "Weekly"
    assert data["completed"] is False
    assert data["items_count"] == 0
    stored = await db.lists.find_one({"_id": ObjectId(data["id"])})
    assert stored is not None
    assert stored["user_id"] == current_user["id"]


@pytest.mark.asyncio
async def test_create_list_rejects_completed_field(client):
    response = await client.post("/lists", json={"name": "Weekly", "completed": True})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_list_404(client):
    missing_id = str(ObjectId())
    response = await client.get(f"/lists/{missing_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_list_success(client):
    created = await create_list(client, name="Errands")
    await create_item(client, created["id"], name="Soap")
    response = await client.get(f"/lists/{created['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created["id"]
    assert data["name"] == "Errands"
    assert data["items_count"] == 1


@pytest.mark.asyncio
async def test_create_item_updates_parent_list_updated_at(client):
    created = await create_list(client, name="Errands")

    response = await client.post(
        f"/lists/{created['id']}/items", json={"name": "Soap", "sort_order": 0}
    )
    assert response.status_code == 201

    refreshed = await client.get(f"/lists/{created['id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["updated_at"] != created["updated_at"]


@pytest.mark.asyncio
async def test_list_lists_includes_items_count(client):
    first = await create_list(client, name="One")
    second = await create_list(client, name="Two")
    await create_item(client, first["id"], name="Milk")
    await create_item(client, first["id"], name="Bread")
    await create_item(client, second["id"], name="Coffee")

    response = await client.get("/lists")
    assert response.status_code == 200
    data = response.json()
    counts_by_id = {item["id"]: item["items_count"] for item in data}
    assert counts_by_id[first["id"]] == 2
    assert counts_by_id[second["id"]] == 1


@pytest.mark.asyncio
async def test_list_out_schema_accepts_and_serialises_unpurchased_items_count():
    from app.schemas import ListOut
    from datetime import datetime, timezone

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = {
        "id": "abc123",
        "user_id": "user-1",
        "name": "Groceries",
        "completed": False,
        "template_id": None,
        "items_count": 5,
        "created_at": now,
        "updated_at": now,
    }

    with_count = ListOut(**{**base, "unpurchased_items_count": 3})
    assert with_count.unpurchased_items_count == 3
    dumped = with_count.model_dump()
    assert dumped["unpurchased_items_count"] == 3

    without_count = ListOut(**base)
    assert without_count.unpurchased_items_count is None
    dumped_none = without_count.model_dump()
    assert dumped_none["unpurchased_items_count"] is None

    with_zero = ListOut(**{**base, "unpurchased_items_count": 0})
    assert with_zero.unpurchased_items_count == 0


@pytest.mark.asyncio
async def test_list_lists_includes_unpurchased_items_count(client):
    lst = await create_list(client, name="Shopping")
    item_a = await create_item(client, lst["id"], name="Milk")
    item_b = await create_item(client, lst["id"], name="Eggs")
    await create_item(client, lst["id"], name="Bread")

    # Purchase two of the three items
    await client.post(f"/items/{item_a['id']}/toggle")
    await client.post(f"/items/{item_b['id']}/toggle")

    response = await client.get("/lists")
    assert response.status_code == 200
    data = response.json()
    found = next(d for d in data if d["id"] == lst["id"])
    assert found["items_count"] == 3
    assert found["unpurchased_items_count"] == 1


@pytest.mark.asyncio
async def test_list_lists_unpurchased_items_count_zero_when_all_purchased(client):
    lst = await create_list(client, name="Quick run")
    item = await create_item(client, lst["id"], name="Salt")
    await client.post(f"/items/{item['id']}/toggle")

    response = await client.get("/lists")
    assert response.status_code == 200
    data = response.json()
    found = next(d for d in data if d["id"] == lst["id"])
    assert found["unpurchased_items_count"] == 0


@pytest.mark.asyncio
async def test_list_lists_unpurchased_items_count_equals_items_count_when_none_purchased(client):
    lst = await create_list(client, name="Full basket")
    await create_item(client, lst["id"], name="Juice")
    await create_item(client, lst["id"], name="Yogurt")

    response = await client.get("/lists")
    assert response.status_code == 200
    data = response.json()
    found = next(d for d in data if d["id"] == lst["id"])
    assert found["items_count"] == 2
    assert found["unpurchased_items_count"] == 2


@pytest.mark.asyncio
async def test_list_lists_excludes_completed_and_completed_endpoint_lists_only_completed(client):
    active = await create_list(client, name="Active")
    completed = await create_list(client, name="Done")

    complete_response = await client.post(f"/lists/{completed['id']}/complete")
    assert complete_response.status_code == 200

    active_response = await client.get("/lists")
    assert active_response.status_code == 200
    active_ids = {item["id"] for item in active_response.json()}
    assert active["id"] in active_ids
    assert completed["id"] not in active_ids

    completed_response = await client.get("/lists/completed")
    assert completed_response.status_code == 200
    completed_data = completed_response.json()
    assert [item["id"] for item in completed_data] == [completed["id"]]
    assert completed_data[0]["completed"] is True


@pytest.mark.asyncio
async def test_complete_and_activate_list_are_idempotent(client):
    created = await create_list(client, name="Status")

    first_complete = await client.post(f"/lists/{created['id']}/complete")
    assert first_complete.status_code == 200
    assert first_complete.json()["completed"] is True

    second_complete = await client.post(f"/lists/{created['id']}/complete")
    assert second_complete.status_code == 200
    assert second_complete.json()["completed"] is True

    first_activate = await client.post(f"/lists/{created['id']}/activate")
    assert first_activate.status_code == 200
    assert first_activate.json()["completed"] is False

    second_activate = await client.post(f"/lists/{created['id']}/activate")
    assert second_activate.status_code == 200
    assert second_activate.json()["completed"] is False


@pytest.mark.asyncio
async def test_completed_list_blocks_item_mutations_and_activate_unlocks(client):
    created = await create_list(client, name="Locked")
    item = await create_item(client, created["id"], name="Milk")
    await create_item(client, created["id"], name="Eggs")
    complete_response = await client.post(f"/lists/{created['id']}/complete")
    assert complete_response.status_code == 200

    create_response = await client.post(
        f"/lists/{created['id']}/items", json={"name": "Bread", "sort_order": 2}
    )
    assert create_response.status_code == 409
    assert (
        create_response.json()["detail"]
        == "Completed lists are read-only. Activate the list to edit items."
    )

    reorder_response = await client.post(
        f"/lists/{created['id']}/items/reorder", json={"item_ids": [item["id"]]}
    )
    assert reorder_response.status_code == 409

    update_response = await client.patch(f"/items/{item['id']}", json={"name": "Whole Milk"})
    assert update_response.status_code == 409

    toggle_response = await client.post(f"/items/{item['id']}/toggle")
    assert toggle_response.status_code == 409

    delete_response = await client.delete(f"/items/{item['id']}")
    assert delete_response.status_code == 409

    activate_response = await client.post(f"/lists/{created['id']}/activate")
    assert activate_response.status_code == 200
    assert activate_response.json()["completed"] is False

    unlocked_create = await client.post(
        f"/lists/{created['id']}/items", json={"name": "Bread", "sort_order": 2}
    )
    assert unlocked_create.status_code == 201

    unlocked_update = await client.patch(f"/items/{item['id']}", json={"name": "Whole Milk"})
    assert unlocked_update.status_code == 200

    unlocked_toggle = await client.post(f"/items/{item['id']}/toggle")
    assert unlocked_toggle.status_code == 200

    unlocked_delete = await client.delete(f"/items/{item['id']}")
    assert unlocked_delete.status_code == 204


@pytest.mark.asyncio
@time_machine.travel("2026-02-01T10:44:29.867000Z", tick=False)
async def test_update_list_no_changes_returns_existing(client):
    created = await create_list(client, name="Chores")
    response = await client.patch(f"/lists/{created['id']}", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created["id"]
    assert _strip_utc_suffix(data["updated_at"]) == _strip_utc_suffix(
        created["updated_at"]
    )


@pytest.mark.asyncio
async def test_update_list_changes_name_and_updated_at(client):
    created = await create_list(client, name="Chores")
    response = await client.patch(
        f"/lists/{created['id']}", json={"name": "House"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "House"
    assert data["updated_at"] != created["updated_at"]


@pytest.mark.asyncio
async def test_update_list_rejects_completed_field(client):
    created = await create_list(client, name="Chores")
    response = await client.patch(f"/lists/{created['id']}", json={"completed": True})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_list_deletes_items(client, db):
    created = await create_list(client, name="Trip")
    await create_item(client, created["id"], name="Socks")

    response = await client.delete(f"/lists/{created['id']}")
    assert response.status_code == 204

    stored_list = await db.lists.find_one({"_id": ObjectId(created["id"])})
    stored_item = await db.items.find_one({"list_id": created["id"]})
    assert stored_list is None
    assert stored_item is None


@pytest.mark.asyncio
async def test_list_items_sorted(client):
    created = await create_list(client, name="Order")
    await create_item(client, created["id"], name="Second", sort_order=2)
    await create_item(client, created["id"], name="First", sort_order=1)

    response = await client.get(f"/lists/{created['id']}/items")
    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["First", "Second"]


@pytest.mark.asyncio
async def test_create_item_in_list(client):
    created = await create_list(client, name="Grocery")
    item = await create_item(client, created["id"], name="Eggs", sort_order=3)
    assert item["name"] == "Eggs"
    assert item["purchased"] is False


@pytest.mark.asyncio
async def test_reorder_items_updates_sort_order_by_position(client, db):
    created = await create_list(client, name="Reorder")
    first = await create_item(client, created["id"], name="First", sort_order=10)
    second = await create_item(client, created["id"], name="Second", sort_order=20)
    third = await create_item(client, created["id"], name="Third", sort_order=30)

    response = await client.post(
        f"/lists/{created['id']}/items/reorder",
        json={"item_ids": [third["id"], first["id"], second["id"]]},
    )
    assert response.status_code == 200
    data = response.json()
    assert [item["id"] for item in data] == [third["id"], first["id"], second["id"]]
    assert [item["sort_order"] for item in data] == [0, 1, 2]

    stored = await db.items.find({"list_id": created["id"]}).sort("sort_order", 1).to_list(
        length=None
    )
    assert [str(item["_id"]) for item in stored] == [third["id"], first["id"], second["id"]]
    assert [item["sort_order"] for item in stored] == [0, 1, 2]


@pytest.mark.asyncio
async def test_reorder_items_requires_complete_item_id_list(client):
    created = await create_list(client, name="Reorder")
    first = await create_item(client, created["id"], name="First", sort_order=1)
    await create_item(client, created["id"], name="Second", sort_order=2)

    response = await client.post(
        f"/lists/{created['id']}/items/reorder",
        json={"item_ids": [first["id"]]},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Item ids must include every item in the list exactly once."


@pytest.mark.asyncio
async def test_reorder_items_rejects_duplicate_ids(client):
    created = await create_list(client, name="Reorder")
    first = await create_item(client, created["id"], name="First", sort_order=1)
    second = await create_item(client, created["id"], name="Second", sort_order=2)

    response = await client.post(
        f"/lists/{created['id']}/items/reorder",
        json={"item_ids": [first["id"], first["id"], second["id"]]},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Item ids must not contain duplicates."


# ---------------------------------------------------------------------------
# ETag / 304 / Cache-Control on GET /lists/{list_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_list_emits_etag_and_cache_control_on_200(client):
    listing = await create_list(client)
    response = await client.get(f"/lists/{listing['id']}")
    assert response.status_code == 200
    etag = response.headers.get("etag")
    assert etag is not None
    assert etag.startswith('W/"')
    assert etag.endswith('"')
    assert response.headers.get("cache-control") == CACHE_CONTROL


@pytest.mark.asyncio
async def test_get_list_returns_304_when_if_none_match_matches(client):
    listing = await create_list(client)
    initial = await client.get(f"/lists/{listing['id']}")
    etag = initial.headers["etag"]

    response = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": etag},
    )
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers.get("etag") == etag
    assert response.headers.get("cache-control") == CACHE_CONTROL


@pytest.mark.asyncio
async def test_get_list_returns_200_after_item_write_bumps_updated_at(client):
    """Item write through mark_list_touched bumps updated_at → ETag changes."""
    listing = await create_list(client)
    initial = await client.get(f"/lists/{listing['id']}")
    old_etag = initial.headers["etag"]

    await create_item(client, listing["id"], name="Milk")

    response = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": old_etag},
    )
    assert response.status_code == 200
    new_etag = response.headers["etag"]
    assert new_etag != old_etag


@pytest.mark.asyncio
async def test_get_list_falls_through_to_200_on_malformed_if_none_match(client):
    listing = await create_list(client)
    response = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": "not-a-real-etag"},
    )
    assert response.status_code == 200
    assert response.headers.get("cache-control") == CACHE_CONTROL


@pytest.mark.asyncio
async def test_get_list_returns_200_when_if_none_match_matches_older_updated_at(
    client, db, current_user
):
    listing = await create_list(client)
    initial = await client.get(f"/lists/{listing['id']}")
    old_etag = initial.headers["etag"]

    later = datetime.now(timezone.utc) + timedelta(seconds=60)
    await mark_list_touched(db, listing["id"], current_user["id"], later)

    response = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": old_etag},
    )
    assert response.status_code == 200
    assert response.headers["etag"] != old_etag


def test_list_etag_returns_none_when_updated_at_is_none():
    """Defensive: ``_list_etag(None)`` → ``None`` so the endpoint omits
    the ``ETag`` header entirely. Reachable end-to-end only if the schema
    is ever loosened to accept ``updated_at=None`` on the read model;
    until then this tests the helper directly so the defense survives
    refactors of the endpoint."""
    assert _list_etag(None) is None
