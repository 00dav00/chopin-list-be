from fastapi import APIRouter, Depends, HTTPException, status
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError

from ..auth import get_current_user, require_admin
from ..db import get_db
from ..schemas import GroupCreate, GroupOut, GroupUpdate, ReorderGroups, group_name_key
from ..utils import serialize_doc, to_object_id, utcnow

router = APIRouter(prefix="/me/admin/groups", tags=["groups"])

OTHER_GROUP_ID = "other"
OTHER_GROUP_NAME = "Other"
DUPLICATE_NAME_MESSAGE = "A group with this name already exists."
RESERVED_NAME_MESSAGE = '"Other" is built in and cannot be used as a group name.'


async def list_stored_groups(db) -> list[dict]:
    cursor = db.groups.find({}).sort([("position", 1), ("created_at", 1)])
    return [serialize_doc(doc) for doc in await cursor.to_list(length=None)]


def with_other(groups: list[dict]) -> list[dict]:
    # Deletes leave gaps in stored positions, so len(groups) could collide.
    other = {
        "id": OTHER_GROUP_ID,
        "name": OTHER_GROUP_NAME,
        "position": (groups[-1]["position"] + 1) if groups else 0,
        "built_in": True,
    }
    return [*groups, other]


async def _ensure_name_available(db, name: str, exclude_id=None) -> str:
    key = group_name_key(name)
    if key == group_name_key(OTHER_GROUP_NAME):
        raise HTTPException(status_code=422, detail=RESERVED_NAME_MESSAGE)
    query = {"name_key": key}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    if await db.groups.find_one(query):
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE)
    return key


async def create_group(db, name: str) -> dict:
    """Appends a group just before "Other"; shared with the unmatched-items flow."""
    key = await _ensure_name_available(db, name)
    last = await db.groups.find_one({}, sort=[("position", -1)])
    now = utcnow()
    doc = {
        "name": name,
        "name_key": key,
        "position": (last["position"] + 1) if last else 0,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await db.groups.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


def _group_object_id(group_id: str):
    if group_id == OTHER_GROUP_ID:
        raise HTTPException(status_code=422, detail='"Other" is built in and cannot be changed.')
    return to_object_id(group_id, "group_id")


@router.get("", response_model=list[GroupOut])
async def list_groups(current_user=Depends(get_current_user), db=Depends(get_db)):
    require_admin(current_user)
    return with_other(await list_stored_groups(db))


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def add_group(
    payload: GroupCreate, current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)
    return await create_group(db, payload.name)


@router.patch("/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: str,
    payload: GroupUpdate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    require_admin(current_user)
    object_id = _group_object_id(group_id)
    key = await _ensure_name_available(db, payload.name, exclude_id=object_id)
    try:
        result = await db.groups.find_one_and_update(
            {"_id": object_id},
            {"$set": {"name": payload.name, "name_key": key, "updated_at": utcnow()}},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail=DUPLICATE_NAME_MESSAGE) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Group not found.")
    return serialize_doc(result)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)
    result = await db.groups.delete_one({"_id": _group_object_id(group_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Group not found.")
    return None


@router.post("/reorder", response_model=list[GroupOut])
async def reorder_groups(
    payload: ReorderGroups, current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)
    group_ids = payload.group_ids
    if len(group_ids) != len(set(group_ids)):
        raise HTTPException(status_code=400, detail="Group ids must not contain duplicates.")

    existing_ids = {group["id"] for group in await list_stored_groups(db)}
    if set(group_ids) != existing_ids:
        raise HTTPException(
            status_code=400,
            detail="Group ids must include every group exactly once.",
        )

    now = utcnow()
    operations = [
        UpdateOne(
            {"_id": to_object_id(group_id, "group_id")},
            {"$set": {"position": position, "updated_at": now}},
        )
        for position, group_id in enumerate(group_ids)
    ]
    if operations:
        await db.groups.bulk_write(operations)
    return with_other(await list_stored_groups(db))
