from bson import ObjectId
import pytest
import time_machine


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
    stored = await db.lists.find_one({"_id": ObjectId(data["id"])})
    assert stored is not None
    assert stored["user_id"] == current_user["id"]


@pytest.mark.asyncio
async def test_get_list_404(client):
    missing_id = str(ObjectId())
    response = await client.get(f"/lists/{missing_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_list_success(client):
    created = await create_list(client, name="Errands")
    response = await client.get(f"/lists/{created['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created["id"]
    assert data["name"] == "Errands"


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
