from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_object_id(value: str, name: str) -> ObjectId:
    try:
        return ObjectId(value)
    except InvalidId as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {name}.") from exc


def serialize_doc(doc: dict) -> dict:
    if not doc:
        return {}
    data = dict(doc)
    data["id"] = str(data.pop("_id"))
    return data
