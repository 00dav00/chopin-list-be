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
    assert "created_at" in data


@pytest.mark.asyncio
async def test_dashboard_summary_returns_counts_and_latest_five_templates(client):
    response = await client.get("/me/dashboard")
    assert response.status_code == 200

    data = response.json()
    assert data["list_count"] == 0
    assert data["templates_count"] == 0
    assert data["last_created_lists"] == []
    assert data["last_created_templates"] == []


@pytest.mark.asyncio
async def test_dashboard_summary_filters_by_user_and_orders_by_created_at(client, db, current_user):
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    own_list_names = []
    for index in range(6):
        name = f"List {index}"
        own_list_names.append(name)
        created_at = now + timedelta(minutes=index)
        await db.lists.insert_one(
            {
                "_id": ObjectId(),
                "user_id": current_user["id"],
                "name": name,
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
            "template_id": None,
            "created_at": now + timedelta(hours=1),
            "updated_at": now + timedelta(hours=1),
        }
    )

    own_template_names = []
    for index in range(6):
        name = f"Template {index}"
        own_template_names.append(name)
        created_at = now + timedelta(minutes=index)
        await db.templates.insert_one(
            {
                "_id": ObjectId(),
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

    assert data["list_count"] == 6
    assert data["templates_count"] == 6

    returned_list_names = [item["name"] for item in data["last_created_lists"]]
    assert returned_list_names == own_list_names[::-1][:5]

    returned_names = [template["name"] for template in data["last_created_templates"]]
    assert returned_names == own_template_names[::-1][:5]
