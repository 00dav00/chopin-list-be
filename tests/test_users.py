from datetime import datetime, timedelta, timezone

from bson import ObjectId
import pytest

from app.auth import get_current_user


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
    assert data["confirmed_users_count"] is None
    assert data["pending_users_count"] is None
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
    assert data["confirmed_users_count"] is None
    assert data["pending_users_count"] is None

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


@pytest.mark.asyncio
async def test_list_pending_users_requires_admin(client):
    response = await client.get("/me/admin/pending-users")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
async def test_list_pending_users_returns_only_not_approved_users(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    pending_id = ObjectId()
    pending_no_approved_id = ObjectId()
    approved_id = ObjectId()

    await db.users.insert_many(
        [
            {
                "_id": pending_id,
                "google_sub": "pending-sub",
                "email": "pending@example.com",
                "name": "Pending",
                "approved": False,
                "created_at": now,
                "last_login_at": now,
            },
            {
                "_id": pending_no_approved_id,
                "google_sub": "pending-sub-2",
                "email": "pending2@example.com",
                "name": "Pending 2",
                "created_at": now + timedelta(minutes=1),
                "last_login_at": now + timedelta(minutes=1),
            },
            {
                "_id": approved_id,
                "google_sub": "approved-sub",
                "email": "approved@example.com",
                "name": "Approved",
                "approved": True,
                "created_at": now + timedelta(minutes=2),
                "last_login_at": now + timedelta(minutes=2),
            },
        ]
    )

    response = await client.get("/me/admin/pending-users")
    assert response.status_code == 200

    payload = response.json()
    returned_ids = [user["id"] for user in payload]
    assert returned_ids == [str(pending_no_approved_id), str(pending_id)]
    assert all(user["approved"] is False for user in payload)


@pytest.mark.asyncio
async def test_list_confirmed_users_requires_admin(client):
    response = await client.get("/me/admin/confirmed-users")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
async def test_list_confirmed_users_returns_only_approved_users(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    approved_id = ObjectId()
    pending_id = ObjectId()

    await db.users.insert_many(
        [
            {
                "_id": approved_id,
                "google_sub": "approved-sub",
                "email": "approved@example.com",
                "name": "Approved",
                "approved": True,
                "created_at": now,
                "last_login_at": now,
            },
            {
                "_id": pending_id,
                "google_sub": "pending-sub",
                "email": "pending@example.com",
                "name": "Pending",
                "approved": False,
                "created_at": now + timedelta(minutes=1),
                "last_login_at": now + timedelta(minutes=1),
            },
        ]
    )

    response = await client.get("/me/admin/confirmed-users")
    assert response.status_code == 200

    payload = response.json()
    assert [user["id"] for user in payload] == [str(approved_id)]
    assert payload[0]["approved"] is True


@pytest.mark.asyncio
async def test_approve_user_requires_admin(client, db):
    user_id = ObjectId()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "pending-sub",
            "email": "pending@example.com",
            "approved": False,
            "created_at": now,
            "last_login_at": now,
        }
    )

    response = await client.post(f"/me/admin/users/{str(user_id)}/approve")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
async def test_approve_user_sets_approved_true_and_is_idempotent(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    user_id = ObjectId()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "pending-sub",
            "email": "pending@example.com",
            "name": "Pending",
            "approved": False,
            "created_at": now,
            "last_login_at": now,
        }
    )

    response = await client.post(f"/me/admin/users/{str(user_id)}/approve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == str(user_id)
    assert payload["approved"] is True

    second_response = await client.post(f"/me/admin/users/{str(user_id)}/approve")
    assert second_response.status_code == 200
    assert second_response.json()["approved"] is True

    stored = await db.users.find_one({"_id": user_id})
    assert stored is not None
    assert stored["approved"] is True


@pytest.mark.asyncio
async def test_approve_user_returns_404_for_unknown_user(client, app):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    response = await client.post(f"/me/admin/users/{str(ObjectId())}/approve")
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found."


@pytest.mark.asyncio
async def test_unconfirm_user_requires_admin(client, db):
    user_id = ObjectId()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "approved-sub",
            "email": "approved@example.com",
            "approved": True,
            "created_at": now,
            "last_login_at": now,
        }
    )

    response = await client.post(f"/me/admin/users/{str(user_id)}/unconfirm")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
async def test_unconfirm_user_sets_approved_false_and_is_idempotent(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    user_id = ObjectId()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "approved-sub",
            "email": "approved@example.com",
            "name": "Approved",
            "approved": True,
            "created_at": now,
            "last_login_at": now,
        }
    )

    response = await client.post(f"/me/admin/users/{str(user_id)}/unconfirm")
    assert response.status_code == 200
    assert response.json()["approved"] is False

    second_response = await client.post(f"/me/admin/users/{str(user_id)}/unconfirm")
    assert second_response.status_code == 200
    assert second_response.json()["approved"] is False

    stored = await db.users.find_one({"_id": user_id})
    assert stored is not None
    assert stored["approved"] is False


@pytest.mark.asyncio
async def test_unconfirm_user_returns_404_for_unknown_user(client, app):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    response = await client.post(f"/me/admin/users/{str(ObjectId())}/unconfirm")
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found."


@pytest.mark.asyncio
async def test_delete_pending_user_requires_admin(client, db):
    user_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "pending-sub",
            "approved": False,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )

    response = await client.delete(f"/me/admin/users/{str(user_id)}")
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."


@pytest.mark.asyncio
async def test_delete_pending_user_returns_404_for_unknown_user(client, app):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    response = await client.delete(f"/me/admin/users/{str(ObjectId())}")
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found."


@pytest.mark.asyncio
async def test_delete_pending_user_rejects_confirmed_user(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    user_id = ObjectId()
    await db.users.insert_one(
        {
            "_id": user_id,
            "google_sub": "approved-sub",
            "approved": True,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    )

    response = await client.delete(f"/me/admin/users/{str(user_id)}")
    assert response.status_code == 409
    assert response.json()["detail"] == "Confirmed users cannot be deleted from pending users."


@pytest.mark.asyncio
async def test_delete_pending_user_cascade_deletes_user_data(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pending_user_id = ObjectId()
    pending_user_str = str(pending_user_id)
    list_id = ObjectId()
    template_id = ObjectId()

    await db.users.insert_one(
        {
            "_id": pending_user_id,
            "google_sub": "pending-sub",
            "approved": False,
            "created_at": now,
            "last_login_at": now,
        }
    )
    await db.lists.insert_one(
        {
            "_id": list_id,
            "user_id": pending_user_str,
            "name": "Pending user list",
            "completed": False,
            "template_id": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.items.insert_one(
        {
            "_id": ObjectId(),
            "user_id": pending_user_str,
            "list_id": str(list_id),
            "name": "Milk",
            "qty": None,
            "purchased": False,
            "purchased_at": None,
            "sort_order": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.templates.insert_one(
        {
            "_id": template_id,
            "user_id": pending_user_str,
            "name": "Pending template",
            "created_at": now,
            "updated_at": now,
        }
    )
    await db.template_items.insert_one(
        {
            "_id": ObjectId(),
            "user_id": pending_user_str,
            "template_id": str(template_id),
            "name": "Apple",
            "qty": None,
            "sort_order": 0,
            "created_at": now,
            "updated_at": now,
        }
    )

    await db.users.insert_one(
        {
            "_id": ObjectId(),
            "google_sub": "other-sub",
            "approved": False,
            "created_at": now,
        }
    )

    response = await client.delete(f"/me/admin/users/{pending_user_str}")
    assert response.status_code == 204

    assert await db.users.count_documents({"_id": pending_user_id}) == 0
    assert await db.lists.count_documents({"user_id": pending_user_str}) == 0
    assert await db.items.count_documents({"user_id": pending_user_str}) == 0
    assert await db.templates.count_documents({"user_id": pending_user_str}) == 0
    assert await db.template_items.count_documents({"user_id": pending_user_str}) == 0


@pytest.mark.asyncio
async def test_dashboard_summary_includes_admin_user_counts(client, app, db):
    admin_user = {
        "id": "admin-1",
        "email": "admin@example.com",
        "name": "Admin",
        "avatar_url": None,
        "admin": True,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
    }
    app.dependency_overrides[get_current_user] = lambda: admin_user

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.users.insert_many(
        [
            {
                "_id": ObjectId(),
                "google_sub": "approved-sub",
                "approved": True,
                "created_at": now,
            },
            {
                "_id": ObjectId(),
                "google_sub": "pending-sub",
                "approved": False,
                "created_at": now,
            },
            {
                "_id": ObjectId(),
                "google_sub": "pending-sub-2",
                "created_at": now,
            },
        ]
    )

    response = await client.get("/me/dashboard")
    assert response.status_code == 200

    data = response.json()
    assert data["confirmed_users_count"] == 1
    assert data["pending_users_count"] == 2
