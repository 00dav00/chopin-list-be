from datetime import datetime, timedelta, timezone

from bson import ObjectId
import pytest


@pytest.mark.asyncio
async def test_read_me_returns_current_user(client, current_user):
    response = await client.get("/me")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == current_user["id"]
    assert data["email"] == current_user["email"]
    assert data["name"] == current_user["name"]
    assert data["admin"] is False
    assert "created_at" in data


@pytest.mark.asyncio
async def test_dashboard_summary_returns_counts_and_latest_five_templates(client):
    response = await client.get("/me/dashboard")
    assert response.status_code == 200

    data = response.json()
    assert data["active_list_count"] == 0
    assert data["completed_list_count"] == 0
    assert data["templates_count"] == 0
    assert data["last_created_lists"] == []
    assert data["last_created_templates"] == []


@pytest.mark.asyncio
async def test_dashboard_summary_filters_by_user_and_orders_by_created_at(client, db, current_user):
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    own_list_names = []
    own_list_ids = []
    for index in range(6):
        name = f"List {index}"
        own_list_names.append(name)
        list_id = ObjectId()
        own_list_ids.append(str(list_id))
        created_at = now + timedelta(minutes=index)
        completed = index in {4, 5}
        await db.lists.insert_one(
            {
                "_id": list_id,
                "user_id": current_user["id"],
                "name": name,
                "completed": completed,
                "template_id": None,
                "created_at": created_at,
                "updated_at": created_at,
            }
        )

    await db.lists.insert_one(
        {
            "_id": ObjectId(),
            "user_id": "other-user",
            "name": "Other",
            "completed": False,
            "template_id": None,
            "created_at": now + timedelta(hours=1),
            "updated_at": now + timedelta(hours=1),
        }
    )

    own_template_names = []
    own_template_ids = []
    for index in range(6):
        name = f"Template {index}"
        own_template_names.append(name)
        template_id = ObjectId()
        own_template_ids.append(str(template_id))
        created_at = now + timedelta(minutes=index)
        await db.templates.insert_one(
            {
                "_id": template_id,
                "user_id": current_user["id"],
                "name": name,
                "created_at": created_at,
                "updated_at": created_at,
            }
        )

    await db.templates.insert_one(
        {
            "_id": ObjectId(),
            "user_id": "other-user",
            "name": "Other Template",
            "created_at": now + timedelta(hours=1),
            "updated_at": now + timedelta(hours=1),
        }
    )

    response = await client.get("/me/dashboard")
    assert response.status_code == 200
    data = response.json()

    assert data["active_list_count"] == 4
    assert data["completed_list_count"] == 2
    assert data["templates_count"] == 6

    returned_list_names = [item["name"] for item in data["last_created_lists"]]
    assert returned_list_names == ["List 3", "List 2", "List 1", "List 0"]
    returned_list_items_count = [item["items_count"] for item in data["last_created_lists"]]
    assert returned_list_items_count == [0, 0, 0, 0]

    returned_names = [template["name"] for template in data["last_created_templates"]]
    assert returned_names == own_template_names[::-1][:5]
    returned_template_items_count = [
        template["items_count"] for template in data["last_created_templates"]
    ]
    assert returned_template_items_count == [0, 0, 0, 0, 0]

    await db.items.insert_many(
        [
            {
                "_id": ObjectId(),
                "user_id": current_user["id"],
                "list_id": own_list_ids[3],
                "name": "Milk",
                "qty": None,
                "purchased": False,
                "purchased_at": None,
                "sort_order": 0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "_id": ObjectId(),
                "user_id": current_user["id"],
                "list_id": own_list_ids[3],
                "name": "Eggs",
                "qty": None,
                "purchased": False,
                "purchased_at": None,
                "sort_order": 1,
                "created_at": now,
                "updated_at": now,
            },
            {
                "_id": ObjectId(),
                "user_id": "other-user",
                "list_id": own_list_ids[2],
                "name": "Other",
                "qty": None,
                "purchased": False,
                "purchased_at": None,
                "sort_order": 0,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )
    await db.template_items.insert_many(
        [
            {
                "_id": ObjectId(),
                "user_id": current_user["id"],
                "template_id": own_template_ids[5],
                "name": "Apple",
                "qty": None,
                "sort_order": 0,
                "created_at": now,
                "updated_at": now,
            },
            {
                "_id": ObjectId(),
                "user_id": "other-user",
                "template_id": own_template_ids[4],
                "name": "Other",
                "qty": None,
                "sort_order": 0,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )

    response = await client.get("/me/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert [item["items_count"] for item in data["last_created_lists"]] == [2, 0, 0, 0]
    assert [template["items_count"] for template in data["last_created_templates"]] == [
        1,
        0,
        0,
        0,
        0,
    ]
