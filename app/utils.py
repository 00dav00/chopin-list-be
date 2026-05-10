from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase


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


async def mark_list_touched(
    db: AsyncIOMotorDatabase, list_id: str, user_id: str, now: datetime
) -> None:
    """Bump ``lists.updated_at`` for the list owned by ``user_id``.

    Load-bearing for ``GET /lists/{list_id}`` ETag/304 correctness: every
    item-write that mutates list contents MUST go through this helper so
    the conditional-GET path returns the correct response. An item write
    that fails to bump ``updated_at`` would produce a stale 304 to a
    viewer with the prior ETag.

    Scoped by ``user_id`` — cannot bump another user's list with the same
    id. No-ops silently if the list does not exist (keeps the helper safe
    to call before/after deletes).

    Failure stance: this helper is awaited synchronously after the primary
    write at every callsite. Mongo runs without transactions/replica-set
    in this deployment, so the primary-write + helper-call pair is not
    atomic — but a helper failure raises and propagates to the request
    handler, surfacing as a 5xx to the client. Visible failure beats
    silent stale-304: the alternative (catch-and-log-and-continue) would
    leave a viewer holding a stale ETag with no signal.
    """
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": user_id},
        {"$max": {"updated_at": now}},
    )
