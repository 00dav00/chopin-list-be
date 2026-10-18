from bson import ObjectId
import pytest
import time_machine


def _strip_utc_suffix(value: str) -> str:
    if value.endswith("Z"):
        return value[:-1]
    if value.endswith("+00:00"):
        return value[:-6]
    return value


async def create_template(client, name="Template", items=None):
    payload = {"name": name, "items": items or []}
    response = await client.post("/templates", json=payload)
    assert response.status_code == 201
    return response.json()


async def create_template_item(client, template_id, name="Item", sort_order=0):
    payload = {"name": name, "sort_order": sort_order}
    response = await client.post(f"/templates/{template_id}/items", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_list_templates_empty(client):
    response = await client.get("/templates")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_create_template_with_items(client):
    items = [
        {"name": "Second", "sort_order": 2},
        {"name": "First", "sort_order": 1},
    ]
    data = await create_template(client, name="Prep", items=items)
    assert data["name"] == "Prep"
    assert data["items_count"] == 2
    assert [item["name"] for item in data["items"]] == ["First", "Second"]


@pytest.mark.asyncio
async def test_get_template_includes_items_sorted(client):
    items = [
        {"name": "Second", "sort_order": 2},
        {"name": "First", "sort_order": 1},
    ]
    created = await create_template(client, name="Prep", items=items)
    response = await client.get(f"/templates/{created['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["items_count"] == 2
    assert [item["name"] for item in data["items"]] == ["First", "Second"]


@pytest.mark.asyncio
async def test_list_templates_includes_items_count(client):
    first = await create_template(
        client,
        name="Prep",
        items=[{"name": "Milk", "sort_order": 1}, {"name": "Eggs", "sort_order": 2}],
    )
    second = await create_template(client, name="Quick")

    response = await client.get("/templates")
    assert response.status_code == 200
    data = response.json()
    counts_by_id = {item["id"]: item["items_count"] for item in data}
    assert counts_by_id[first["id"]] == 2
    assert counts_by_id[second["id"]] == 0


@pytest.mark.asyncio
@time_machine.travel("2026-02-01T10:44:30.112000Z", tick=False)
async def test_update_template_no_changes_returns_existing(client):
    created = await create_template(client, name="Sunday")
    response = await client.patch(f"/templates/{created['id']}", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == created["id"]
    assert data["items_count"] == 0
    assert _strip_utc_suffix(data["updated_at"]) == _strip_utc_suffix(
        created["updated_at"]
    )


@pytest.mark.asyncio
async def test_update_template_changes_name_and_updated_at(client):
    created = await create_template(client, name="Sunday")
    response = await client.patch(
        f"/templates/{created['id']}", json={"name": "Saturday"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Saturday"
    assert data["items_count"] == 0
    assert data["updated_at"] != created["updated_at"]


@pytest.mark.asyncio
async def test_delete_template_deletes_template_items(client, db):
    items = [{"name": "Milk", "sort_order": 1}]
    created = await create_template(client, name="Weekly", items=items)

    response = await client.delete(f"/templates/{created['id']}")
    assert response.status_code == 204

    stored_template = await db.templates.find_one({"_id": ObjectId(created["id"])})
    stored_items = await db.template_items.find_one({"template_id": created["id"]})
    assert stored_template is None
    assert stored_items is None


@pytest.mark.asyncio
async def test_list_template_items_sorted(client):
    created = await create_template(client, name="Sorted")
    await create_template_item(client, created["id"], name="Second", sort_order=2)
    await create_template_item(client, created["id"], name="First", sort_order=1)

    response = await client.get(f"/templates/{created['id']}/items")
    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["First", "Second"]


@pytest.mark.asyncio
async def test_create_template_item(client):
    created = await create_template(client, name="Basics")
    item = await create_template_item(client, created["id"], name="Rice")
    assert item["name"] == "Rice"


@pytest.mark.asyncio
async def test_update_template_item_partial(client):
    created = await create_template(client, name="Basics")
    item = await create_template_item(client, created["id"], name="Rice")

    response = await client.patch(
        f"/templates/{created['id']}/items/{item['id']}",
        json={"name": "Brown Rice"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Brown Rice"


@pytest.mark.asyncio
async def test_delete_template_item(client, db):
    created = await create_template(client, name="Basics")
    item = await create_template_item(client, created["id"], name="Rice")

    response = await client.delete(
        f"/templates/{created['id']}/items/{item['id']}"
    )
    assert response.status_code == 204

    stored = await db.template_items.find_one({"_id": ObjectId(item["id"])})
    assert stored is None


@pytest.mark.asyncio
async def test_reorder_template_items_happy_path(client):
    created = await create_template(client, name="Reorder")
    item_a = await create_template_item(client, created["id"], name="A", sort_order=0)
    item_b = await create_template_item(client, created["id"], name="B", sort_order=1)
    item_c = await create_template_item(client, created["id"], name="C", sort_order=2)

    new_order = [item_c["id"], item_a["id"], item_b["id"]]
    response = await client.post(
        f"/templates/{created['id']}/items/reorder",
        json={"item_ids": new_order},
    )
    assert response.status_code == 200
    items = response.json()
    assert [item["name"] for item in items] == ["C", "A", "B"]


@pytest.mark.asyncio
async def test_reorder_template_items_missing_item_returns_400(client):
    created = await create_template(client, name="Reorder")
    item_a = await create_template_item(client, created["id"], name="A", sort_order=0)
    item_b = await create_template_item(client, created["id"], name="B", sort_order=1)

    response = await client.post(
        f"/templates/{created['id']}/items/reorder",
        json={"item_ids": [item_a["id"]]},  # missing item_b
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reorder_template_items_duplicate_ids_returns_400(client):
    created = await create_template(client, name="Reorder")
    item_a = await create_template_item(client, created["id"], name="A", sort_order=0)
    item_b = await create_template_item(client, created["id"], name="B", sort_order=1)

    response = await client.post(
        f"/templates/{created['id']}/items/reorder",
        json={"item_ids": [item_a["id"], item_a["id"]]},  # duplicate
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reorder_template_items_unknown_id_returns_400(client):
    created = await create_template(client, name="Reorder")
    item_a = await create_template_item(client, created["id"], name="A", sort_order=0)
    unknown_id = str(ObjectId())

    response = await client.post(
        f"/templates/{created['id']}/items/reorder",
        json={"item_ids": [item_a["id"], unknown_id]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_reorder_template_items_nonexistent_template_returns_404(client):
    nonexistent_id = str(ObjectId())
    response = await client.post(
        f"/templates/{nonexistent_id}/items/reorder",
        json={"item_ids": []},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_list_from_template_copies_items(client, db):
    items = [
        {"name": "Bananas", "sort_order": 2},
        {"name": "Apples", "sort_order": 1},
    ]
    created = await create_template(client, name="Fruit", items=items)

    response = await client.post(
        f"/templates/{created['id']}/create-list", json={"name": "Fruit Run"}
    )
    assert response.status_code == 201
    list_data = response.json()
    assert list_data["name"] == "Fruit Run"
    assert list_data["completed"] is False
    assert list_data["items_count"] == 2

    stored_items = await db.items.find({"list_id": list_data["id"]}).to_list(
        length=None
    )
    assert len(stored_items) == 2
    stored_names = sorted(item["name"] for item in stored_items)
    assert stored_names == ["Apples", "Bananas"]


@pytest.mark.asyncio
async def test_create_list_from_template_skips_mark_list_touched(client):
    """Templates bulk-insert exception to the conditional-GET contract.

    The create-from-template path inserts the list with ``updated_at=now``
    and bulk-inserts items also with ``updated_at=now``. We deliberately
    do NOT call ``mark_list_touched`` after the bulk-insert; the list's
    ``updated_at`` reflects the post-bulk-insert state because list and
    items share the same ``now`` timestamp. The first GET returns a
    usable ETag that 304s correctly until the next item write.
    """
    template = await create_template(client, name="Staples")
    item_resp = await client.post(
        f"/templates/{template['id']}/items",
        json={"name": "Bread", "sort_order": 0},
    )
    assert item_resp.status_code == 201

    create_resp = await client.post(
        f"/templates/{template['id']}/create-list",
        json={"name": "From staples"},
    )
    assert create_resp.status_code == 201
    listing = create_resp.json()

    initial = await client.get(f"/lists/{listing['id']}")
    assert initial.status_code == 200
    etag = initial.headers["etag"]

    cond = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": etag},
    )
    assert cond.status_code == 304


# ---------------------------------------------------------------------------
# Group by kind: POST /templates/{template_id}/group (service logic in test_grouping.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_template_orders_all_items_and_leaves_version(client, db, fake_classifier):
    from app.schemas import group_name_key

    await db.groups.insert_many(
        [
            {"name": "Fruit", "name_key": group_name_key("Fruit"), "position": 0},
            {"name": "Dairy", "name_key": group_name_key("Dairy"), "position": 1},
        ]
    )
    template = await create_template(
        client,
        items=[
            {"name": "milk", "sort_order": 0},
            {"name": "card", "sort_order": 1},
            {"name": "apples", "sort_order": 2},
        ],
    )
    before = await db.templates.find_one({"_id": ObjectId(template["id"])})
    fake_classifier.assignments = {"milk": "Dairy", "apples": "Fruit"}

    response = await client.post(f"/templates/{template['id']}/group")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["items"]] == ["apples", "milk", "card"]
    assert [entry["name"] for entry in body["unmatched"]] == ["card"]
    assert all("group_id" not in item for item in body["items"])
    after = await db.templates.find_one({"_id": ObjectId(template["id"])})
    assert after["updated_at"] == before["updated_at"]
    run = await db.grouping_runs.find_one({"_id": ObjectId(body["run_id"])})
    assert run["target_kind"] == "template"


@pytest.mark.asyncio
async def test_group_empty_template_returns_422(client, db, fake_classifier):
    template = await create_template(client)

    response = await client.post(f"/templates/{template['id']}/group")

    assert response.status_code == 422
    assert fake_classifier.calls == []


@pytest.mark.asyncio
async def test_group_unknown_template_returns_404(client, fake_classifier):
    response = await client.post(f"/templates/{ObjectId()}/group")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_list_from_template_copies_group_id(client, db):
    template = await create_template(client, items=[{"name": "milk", "sort_order": 0}])
    await db.template_items.update_many(
        {"template_id": template["id"]}, {"$set": {"group_id": "group-dairy"}}
    )

    response = await client.post(f"/templates/{template['id']}/create-list", json={})

    items = await db.items.find({"list_id": response.json()["id"]}).to_list(length=None)
    assert [item["group_id"] for item in items] == ["group-dairy"]
