from datetime import timezone

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.utils import serialize_doc, to_object_id, utcnow


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
