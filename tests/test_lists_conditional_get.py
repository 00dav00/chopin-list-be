"""Tests for ETag/304/Cache-Control on GET /lists/{list_id} and the
``mark_list_touched`` helper that backs its 304 correctness.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.routers.lists import _list_etag
from app.utils import mark_list_touched


CACHE_CONTROL = "private, no-cache"


async def _create_list(client, name="Groceries"):
    response = await client.post("/lists", json={"name": name})
    assert response.status_code == 201
    return response.json()


async def _create_item(client, list_id, name="Milk", sort_order=0):
    response = await client.post(
        f"/lists/{list_id}/items",
        json={"name": name, "sort_order": sort_order},
    )
    assert response.status_code == 201
    return response.json()


# ---------------------------------------------------------------------------
# mark_list_touched — helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_list_touched_bumps_updated_at(client, db, current_user):
    listing = await _create_list(client, name="A")
    pre = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    target = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await mark_list_touched(db, listing["id"], current_user["id"], target)
    post = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    # BSON strips tzinfo on round-trip; compare against naive UTC.
    assert post["updated_at"] == target.replace(tzinfo=None)
    assert post["updated_at"] != pre["updated_at"]


@pytest.mark.asyncio
async def test_mark_list_touched_scoped_by_user_id(client, db):
    """Helper must not bump a list owned by a different user."""
    listing = await _create_list(client, name="A")
    pre = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    target = datetime(2030, 1, 1, tzinfo=timezone.utc)
    # Wrong user id: helper should match nothing and leave updated_at alone.
    await mark_list_touched(db, listing["id"], "other-user", target)
    post = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    assert post["updated_at"] == pre["updated_at"]


# ---------------------------------------------------------------------------
# ETag / 304 / Cache-Control — endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_list_emits_etag_and_cache_control_on_200(client):
    listing = await _create_list(client)
    response = await client.get(f"/lists/{listing['id']}")
    assert response.status_code == 200
    etag = response.headers.get("etag")
    assert etag is not None
    assert etag.startswith('W/"')
    assert etag.endswith('"')
    assert response.headers.get("cache-control") == CACHE_CONTROL


@pytest.mark.asyncio
async def test_get_list_returns_304_when_if_none_match_matches(client):
    listing = await _create_list(client)
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
    listing = await _create_list(client)
    initial = await client.get(f"/lists/{listing['id']}")
    old_etag = initial.headers["etag"]

    # Item create flows through mark_list_touched in the create_item path.
    await _create_item(client, listing["id"], name="Milk")

    response = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": old_etag},
    )
    assert response.status_code == 200
    new_etag = response.headers["etag"]
    assert new_etag != old_etag


@pytest.mark.asyncio
async def test_get_list_falls_through_to_200_on_malformed_if_none_match(client):
    listing = await _create_list(client)
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
    listing = await _create_list(client)
    initial = await client.get(f"/lists/{listing['id']}")
    old_etag = initial.headers["etag"]

    # Bump updated_at directly via the helper to simulate a write.
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


@pytest.mark.asyncio
async def test_item_delete_bumps_etag(client):
    """Item delete path goes through mark_list_touched → ETag changes."""
    listing = await _create_list(client)
    item = await _create_item(client, listing["id"], name="Milk")
    pre = await client.get(f"/lists/{listing['id']}")
    old_etag = pre.headers["etag"]

    response = await client.delete(f"/items/{item['id']}")
    assert response.status_code == 204

    post = await client.get(f"/lists/{listing['id']}")
    assert post.status_code == 200
    assert post.headers["etag"] != old_etag


@pytest.mark.asyncio
async def test_item_toggle_bumps_etag(client):
    """Item toggle path goes through mark_list_touched → ETag changes."""
    listing = await _create_list(client)
    item = await _create_item(client, listing["id"], name="Milk")
    pre = await client.get(f"/lists/{listing['id']}")
    old_etag = pre.headers["etag"]

    response = await client.post(f"/items/{item['id']}/toggle")
    assert response.status_code == 200

    post = await client.get(f"/lists/{listing['id']}")
    assert post.status_code == 200
    assert post.headers["etag"] != old_etag


@pytest.mark.asyncio
async def test_item_update_bumps_etag(client):
    """Item update path goes through mark_list_touched → ETag changes."""
    listing = await _create_list(client)
    item = await _create_item(client, listing["id"], name="Milk")
    pre = await client.get(f"/lists/{listing['id']}")
    old_etag = pre.headers["etag"]

    response = await client.patch(
        f"/items/{item['id']}", json={"name": "Whole Milk"}
    )
    assert response.status_code == 200

    post = await client.get(f"/lists/{listing['id']}")
    assert post.status_code == 200
    assert post.headers["etag"] != old_etag


@pytest.mark.asyncio
async def test_item_reorder_bumps_etag(client):
    """Item reorder path goes through mark_list_touched → ETag changes."""
    listing = await _create_list(client)
    a = await _create_item(client, listing["id"], name="Milk", sort_order=0)
    b = await _create_item(client, listing["id"], name="Eggs", sort_order=1)
    pre = await client.get(f"/lists/{listing['id']}")
    old_etag = pre.headers["etag"]

    response = await client.post(
        f"/lists/{listing['id']}/items/reorder",
        json={"item_ids": [b["id"], a["id"]]},
    )
    assert response.status_code == 200

    post = await client.get(f"/lists/{listing['id']}")
    assert post.status_code == 200
    assert post.headers["etag"] != old_etag


@pytest.mark.asyncio
async def test_create_list_from_template_skips_mark_list_touched(client, db):
    """Templates bulk-insert exception — verify the documented carve-out.

    The create-from-template path inserts the list with ``updated_at=now``
    and bulk-inserts items also with ``updated_at=now``. We deliberately
    do NOT call mark_list_touched after the bulk-insert; the list's
    ``updated_at`` reflects the post-bulk-insert state because list and
    items share the same ``now`` timestamp. The first GET returns a
    usable ETag that 304s correctly until the next item write.
    """
    template_resp = await client.post("/templates", json={"name": "Staples"})
    assert template_resp.status_code == 201
    template = template_resp.json()
    item_resp = await client.post(
        f"/templates/{template['id']}/items",
        json={"name": "Bread", "sort_order": 0},
    )
    assert item_resp.status_code == 201

    # Create list from template.
    create_resp = await client.post(
        f"/templates/{template['id']}/create-list",
        json={"name": "From staples"},
    )
    assert create_resp.status_code == 201
    listing = create_resp.json()

    # Capture ETag from a fresh GET — should reflect the post-bulk-insert state.
    initial = await client.get(f"/lists/{listing['id']}")
    assert initial.status_code == 200
    etag = initial.headers["etag"]

    # Re-GET with the same ETag → 304 (no items mutated; list's updated_at
    # already matches the bulk-inserted items' timestamp).
    cond = await client.get(
        f"/lists/{listing['id']}",
        headers={"If-None-Match": etag},
    )
    assert cond.status_code == 304
