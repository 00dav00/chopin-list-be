from fastapi import APIRouter, Depends, HTTPException, status
from pymongo import ReturnDocument

from ..auth import get_current_user, require_admin
from ..classifier import clean_name
from ..db import get_db
from ..notifications import dispatch_unmatched_items_notification
from ..schemas import (
    GroupCreate,
    GroupOut,
    SendRunOut,
    UnmatchedEntryOut,
    group_name_key,
)
from ..utils import serialize_doc, to_object_id, utcnow
from .groups import create_group

router = APIRouter(tags=["unmatched"])

UNMATCHED_LIST_LIMIT = 200
RESOLVED_GROUP_CREATED = "group_created"
RESOLVED_DISMISSED = "dismissed"


def _distinct_clean_names(unmatched: list[dict]) -> list[str]:
    names: dict[str, str] = {}
    for entry in unmatched:
        name = clean_name(entry.get("name") or "")
        if name:
            names.setdefault(group_name_key(name), name)
    return list(names.values())


@router.post("/grouping-runs/{run_id}/send", response_model=SendRunOut)
async def send_grouping_run(
    run_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    """Shares a run's unmatched names with admins, once. Names never come from
    the request body, so this can't be used to email arbitrary text."""
    user_id = current_user["id"]
    run_object_id = to_object_id(run_id, "run_id")
    now = utcnow()
    run = await db.grouping_runs.find_one_and_update(
        {"_id": run_object_id, "user_id": user_id, "sent_at": None},
        {"$set": {"sent_at": now}},
    )
    if run is None:
        if await db.grouping_runs.find_one({"_id": run_object_id, "user_id": user_id}):
            return {"sent": False}
        raise HTTPException(status_code=404, detail="Grouping run not found.")

    names = _distinct_clean_names(run.get("unmatched", []))
    for name in names:
        await db.unmatched_items.update_one(
            {"name_key": group_name_key(name), "resolved_at": None},
            {
                "$set": {"name": name, "last_sent_at": now},
                "$inc": {"count": 1},
                "$setOnInsert": {"created_at": now, "resolution": None},
            },
            upsert=True,
        )
    if names:
        dispatch_unmatched_items_notification(db, names)
    return {"sent": True}


async def _open_entry_or_404(db, entry_id: str) -> dict:
    entry = await db.unmatched_items.find_one(
        {"_id": to_object_id(entry_id, "entry_id"), "resolved_at": None}
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Unmatched item not found.")
    return entry


async def _resolve(db, entry: dict, resolution: str) -> None:
    await db.unmatched_items.update_one(
        {"_id": entry["_id"], "resolved_at": None},
        {"$set": {"resolved_at": utcnow(), "resolution": resolution}},
    )


@router.get("/me/admin/unmatched-items", response_model=list[UnmatchedEntryOut])
async def list_unmatched_items(
    current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)
    cursor = (
        db.unmatched_items.find({"resolved_at": None})
        .sort("last_sent_at", -1)
        .limit(UNMATCHED_LIST_LIMIT)
    )
    return [serialize_doc(doc) for doc in await cursor.to_list(length=None)]


@router.post(
    "/me/admin/unmatched-items/{entry_id}/create-group",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_group_from_unmatched(
    entry_id: str,
    payload: GroupCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    require_admin(current_user)
    entry = await _open_entry_or_404(db, entry_id)
    # A name collision raises here and leaves the entry open, as intended.
    group = await create_group(db, payload.name)
    await _resolve(db, entry, RESOLVED_GROUP_CREATED)
    return group


@router.post(
    "/me/admin/unmatched-items/{entry_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dismiss_unmatched_item(
    entry_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)
    entry = await _open_entry_or_404(db, entry_id)
    await _resolve(db, entry, RESOLVED_DISMISSED)
    return None
