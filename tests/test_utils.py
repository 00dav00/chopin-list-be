from datetime import datetime, timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.utils import mark_list_touched, serialize_doc, to_object_id, utcnow


def test_utcnow_is_timezone_aware():
    now = utcnow()
    assert now.tzinfo is timezone.utc


def test_to_object_id_valid():
    value = ObjectId()
    result = to_object_id(str(value), "list_id")
    assert result == value


def test_to_object_id_invalid_raises_400():
    with pytest.raises(HTTPException) as exc:
        to_object_id("not-a-valid-objectid", "list_id")
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid list_id."


def test_serialize_doc_empty():
    assert serialize_doc({}) == {}


def test_serialize_doc_moves_id():
    value = ObjectId()
    doc = {"_id": value, "name": "Groceries"}
    result = serialize_doc(doc)
    assert result["id"] == str(value)
    assert result["name"] == "Groceries"
    assert "_id" not in result


async def _create_list(client, name="A"):
    response = await client.post("/lists", json={"name": name})
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_mark_list_touched_bumps_updated_at(client, db, current_user):
    listing = await _create_list(client)
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
    listing = await _create_list(client)
    pre = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    target = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await mark_list_touched(db, listing["id"], "other-user", target)
    post = await db.lists.find_one({"_id": ObjectId(listing["id"])})
    assert post["updated_at"] == pre["updated_at"]
