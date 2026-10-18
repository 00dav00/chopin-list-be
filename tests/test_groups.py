from datetime import datetime, timezone

from bson import ObjectId
import pytest

from app.auth import get_current_user

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
def as_admin(app):
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER


async def _create(client, name):
    response = await client.post("/me/admin/groups", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _names(groups):
    return [group["name"] for group in groups]


@pytest.mark.asyncio
async def test_list_groups_without_groups_returns_only_other(client, as_admin):
    response = await client.get("/me/admin/groups")

    assert response.status_code == 200
    assert response.json() == [
        {"id": "other", "name": "Other", "position": 0, "built_in": True}
    ]


@pytest.mark.asyncio
async def test_new_groups_are_appended_before_other(client, as_admin):
    dairy = await _create(client, "Dairy")
    fruit = await _create(client, "Fruit")

    assert dairy["position"] == 0
    assert fruit["position"] == 1
    assert fruit["built_in"] is False
    response = await client.get("/me/admin/groups")
    assert _names(response.json()) == ["Dairy", "Fruit", "Other"]


@pytest.mark.asyncio
async def test_create_group_collapses_whitespace_in_name(client, as_admin):
    group = await _create(client, "  Fish   &  seafood ")

    assert group["name"] == "Fish & seafood"


@pytest.mark.asyncio
async def test_create_group_appends_after_highest_position(client, as_admin):
    dairy = await _create(client, "Dairy")
    fruit = await _create(client, "Fruit")
    await client.delete(f"/me/admin/groups/{dairy['id']}")

    bakery = await _create(client, "Bakery")

    assert bakery["position"] == fruit["position"] + 1


@pytest.mark.asyncio
async def test_create_group_rejects_duplicate_name_ignoring_case(client, as_admin, db):
    await _create(client, "Dairy")

    response = await client.post("/me/admin/groups", json={"name": " dairy "})

    assert response.status_code == 409
    assert response.json()["detail"] == "A group with this name already exists."
    assert await db.groups.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Other", "other", " OTHER "])
async def test_create_group_rejects_reserved_other(client, as_admin, db, name):
    response = await client.post("/me/admin/groups", json={"name": name})

    assert response.status_code == 422
    assert await db.groups.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
async def test_create_group_rejects_invalid_name_length(client, as_admin, db, name):
    response = await client.post("/me/admin/groups", json={"name": name})

    assert response.status_code == 422
    assert await db.groups.count_documents({}) == 0


@pytest.mark.asyncio
async def test_create_group_accepts_sixty_characters(client, as_admin):
    group = await _create(client, "x" * 60)

    assert len(group["name"]) == 60


@pytest.mark.asyncio
async def test_create_group_rejects_position_field(client, as_admin):
    response = await client.post(
        "/me/admin/groups", json={"name": "Dairy", "position": 0}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rename_group_keeps_id_and_position(client, as_admin):
    dairy = await _create(client, "Dairy")

    response = await client.patch(
        f"/me/admin/groups/{dairy['id']}", json={"name": "Dairy & eggs"}
    )

    assert response.status_code == 200
    renamed = response.json()
    assert renamed["id"] == dairy["id"]
    assert renamed["position"] == dairy["position"]
    assert renamed["name"] == "Dairy & eggs"


@pytest.mark.asyncio
async def test_rename_group_to_own_name_with_new_case_is_allowed(client, as_admin):
    dairy = await _create(client, "Dairy")

    response = await client.patch(
        f"/me/admin/groups/{dairy['id']}", json={"name": "DAIRY"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "DAIRY"


@pytest.mark.asyncio
async def test_rename_group_rejects_other_groups_name(client, as_admin):
    await _create(client, "Dairy")
    fruit = await _create(client, "Fruit")

    response = await client.patch(
        f"/me/admin/groups/{fruit['id']}", json={"name": "dairy"}
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_rename_group_rejects_other(client, as_admin):
    dairy = await _create(client, "Dairy")

    response = await client.patch(
        f"/me/admin/groups/{dairy['id']}", json={"name": "Other"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rename_unknown_group_returns_404(client, as_admin):
    response = await client.patch(
        f"/me/admin/groups/{ObjectId()}", json={"name": "Dairy"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_built_in_other_cannot_be_renamed_or_deleted(client, as_admin):
    rename = await client.patch("/me/admin/groups/other", json={"name": "Misc"})
    delete = await client.delete("/me/admin/groups/other")

    assert rename.status_code == 422
    assert delete.status_code == 422


@pytest.mark.asyncio
async def test_delete_group_removes_it(client, as_admin):
    dairy = await _create(client, "Dairy")
    await _create(client, "Fruit")

    response = await client.delete(f"/me/admin/groups/{dairy['id']}")

    assert response.status_code == 204
    listing = await client.get("/me/admin/groups")
    assert _names(listing.json()) == ["Fruit", "Other"]


@pytest.mark.asyncio
async def test_delete_unknown_group_returns_404(client, as_admin):
    response = await client.delete(f"/me/admin/groups/{ObjectId()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reorder_groups_renumbers_positions(client, as_admin):
    dairy = await _create(client, "Dairy")
    fruit = await _create(client, "Fruit")
    bakery = await _create(client, "Bakery")

    response = await client.post(
        "/me/admin/groups/reorder",
        json={"group_ids": [fruit["id"], bakery["id"], dairy["id"]]},
    )

    assert response.status_code == 200
    groups = response.json()
    assert _names(groups) == ["Fruit", "Bakery", "Dairy", "Other"]
    assert [group["position"] for group in groups] == [0, 1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "other"])
async def test_reorder_groups_rejects_wrong_id_set(client, as_admin, db, variant):
    dairy = await _create(client, "Dairy")
    fruit = await _create(client, "Fruit")
    ids = {
        "missing": [fruit["id"]],
        "extra": [fruit["id"], dairy["id"], str(ObjectId())],
        "duplicate": [fruit["id"], dairy["id"], dairy["id"]],
        "other": [fruit["id"], dairy["id"], "other"],
    }[variant]

    response = await client.post("/me/admin/groups/reorder", json={"group_ids": ids})

    assert response.status_code == 400
    listing = await client.get("/me/admin/groups")
    assert _names(listing.json()) == ["Dairy", "Fruit", "Other"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/me/admin/groups", None),
        ("POST", "/me/admin/groups", {"name": "Dairy"}),
        ("PATCH", f"/me/admin/groups/{ObjectId()}", {"name": "Dairy"}),
        ("DELETE", f"/me/admin/groups/{ObjectId()}", None),
        ("POST", "/me/admin/groups/reorder", {"group_ids": []}),
    ],
)
async def test_groups_endpoints_require_admin(client, db, method, path, body):
    response = await client.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required."
    assert await db.groups.count_documents({}) == 0
