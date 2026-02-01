from bson import ObjectId
import pytest


async def create_list(client, name="List"):
    response = await client.post("/lists", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def create_item(client, list_id, name="Milk", qty=1, unit="box"):
    payload = {"name": name, "qty": qty, "unit": unit}
    response = await client.post(f"/lists/{list_id}/items", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_update_item_partial_fields(client):
    created_list = await create_list(client)
    created_item = await create_item(client, created_list["id"], name="Milk")

    response = await client.patch(
        f"/items/{created_item['id']}", json={"name": "Oat Milk"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Oat Milk"
    assert data["qty"] == created_item["qty"]
    assert data["unit"] == created_item["unit"]


@pytest.mark.asyncio
async def test_update_item_toggle_purchased_sets_purchased_at(client):
    created_list = await create_list(client)
    created_item = await create_item(client, created_list["id"], name="Bread")

    response = await client.patch(
        f"/items/{created_item['id']}", json={"purchased": True}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["purchased"] is True
    assert data["purchased_at"] is not None


@pytest.mark.asyncio
async def test_update_item_unset_purchased_clears_purchased_at(client):
    created_list = await create_list(client)
    created_item = await create_item(client, created_list["id"], name="Bread")

    await client.patch(
        f"/items/{created_item['id']}", json={"purchased": True}
    )
    response = await client.patch(
        f"/items/{created_item['id']}", json={"purchased": False}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["purchased"] is False
    assert data["purchased_at"] is None


@pytest.mark.asyncio
async def test_update_item_404(client):
    missing_id = str(ObjectId())
    response = await client.patch(f"/items/{missing_id}", json={"name": "X"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_toggle_item_flips_state(client):
    created_list = await create_list(client)
    created_item = await create_item(client, created_list["id"], name="Cheese")

    response = await client.post(f"/items/{created_item['id']}/toggle")
    assert response.status_code == 200
    assert response.json()["purchased"] is True

    response = await client.post(f"/items/{created_item['id']}/toggle")
    assert response.status_code == 200
    assert response.json()["purchased"] is False


@pytest.mark.asyncio
async def test_delete_item(client, db):
    created_list = await create_list(client)
    created_item = await create_item(client, created_list["id"], name="Yogurt")

    response = await client.delete(f"/items/{created_item['id']}")
    assert response.status_code == 204

    stored = await db.items.find_one({"_id": ObjectId(created_item["id"])})
    assert stored is None
