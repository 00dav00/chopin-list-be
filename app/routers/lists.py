from fastapi import APIRouter, Depends, HTTPException, status
from pymongo import UpdateOne

from ..auth import get_current_user
from ..db import get_db
from ..schemas import (
    ItemCreate,
    ItemOut,
    ListCreate,
    ListOut,
    ListUpdate,
    ReorderListItems,
)
from ..utils import serialize_doc, to_object_id, utcnow

router = APIRouter(prefix="/lists", tags=["lists"])
LIST_COMPLETED_MUTATION_MESSAGE = (
    "Completed lists are read-only. Activate the list to edit items."
)


async def _get_list_or_404(db, list_id: str, user_id: str) -> dict:
    list_doc = await db.lists.find_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": user_id}
    )
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found.")
    return list_doc


async def _get_items_count_by_list_ids(db, list_ids: list[str], user_id: str) -> dict[str, int]:
    if not list_ids:
        return {}
    pipeline = [
        {"$match": {"user_id": user_id, "list_id": {"$in": list_ids}}},
        {"$group": {"_id": "$list_id", "count": {"$sum": 1}}},
    ]
    grouped = await db.items.aggregate(pipeline).to_list(length=None)
    return {row["_id"]: row["count"] for row in grouped}


def _ensure_list_is_active(list_doc: dict) -> None:
    if list_doc.get("completed", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=LIST_COMPLETED_MUTATION_MESSAGE,
        )


async def _serialize_list_with_items_count(db, list_doc: dict, user_id: str) -> dict:
    response = serialize_doc(list_doc)
    response["completed"] = response.get("completed", False)
    response["items_count"] = await db.items.count_documents(
        {"list_id": response["id"], "user_id": user_id}
    )
    return response


@router.get("", response_model=list[ListOut])
async def list_lists(current_user=Depends(get_current_user), db=Depends(get_db)):
    cursor = db.lists.find(
        {"user_id": current_user["id"], "completed": {"$ne": True}}
    ).sort("updated_at", -1)
    docs = await cursor.to_list(length=None)
    response = [serialize_doc(doc) for doc in docs]
    for doc in response:
        doc["completed"] = doc.get("completed", False)
    items_count_by_list_id = await _get_items_count_by_list_ids(
        db, [doc["id"] for doc in response], current_user["id"]
    )
    for doc in response:
        doc["items_count"] = items_count_by_list_id.get(doc["id"], 0)
    return response


@router.get("/completed", response_model=list[ListOut])
async def list_completed_lists(current_user=Depends(get_current_user), db=Depends(get_db)):
    cursor = db.lists.find({"user_id": current_user["id"], "completed": True}).sort(
        "updated_at", -1
    )
    docs = await cursor.to_list(length=None)
    response = [serialize_doc(doc) for doc in docs]
    for doc in response:
        doc["completed"] = True
    items_count_by_list_id = await _get_items_count_by_list_ids(
        db, [doc["id"] for doc in response], current_user["id"]
    )
    for doc in response:
        doc["items_count"] = items_count_by_list_id.get(doc["id"], 0)
    return response


@router.post("", response_model=ListOut, status_code=status.HTTP_201_CREATED)
async def create_list(
    payload: ListCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    now = utcnow()
    doc = {
        "user_id": current_user["id"],
        "name": payload.name,
        "completed": False,
        "template_id": payload.template_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.lists.insert_one(doc)
    doc["_id"] = result.inserted_id
    response = serialize_doc(doc)
    response["completed"] = False
    response["items_count"] = 0
    return response


@router.get("/{list_id}", response_model=ListOut)
async def get_list(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return await _serialize_list_with_items_count(db, list_doc, current_user["id"])


@router.post("/{list_id}/complete", response_model=ListOut)
async def complete_list(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_list_or_404(db, list_id, current_user["id"])
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]},
        {"$set": {"completed": True, "updated_at": utcnow()}},
    )
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return await _serialize_list_with_items_count(db, list_doc, current_user["id"])


@router.post("/{list_id}/activate", response_model=ListOut)
async def activate_list(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_list_or_404(db, list_id, current_user["id"])
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]},
        {"$set": {"completed": False, "updated_at": utcnow()}},
    )
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return await _serialize_list_with_items_count(db, list_doc, current_user["id"])


@router.patch("/{list_id}", response_model=ListOut)
async def update_list(
    list_id: str,
    payload: ListUpdate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    await _get_list_or_404(db, list_id, current_user["id"])
    updates = {}
    if payload.name is not None:
        updates["name"] = payload.name
    if not updates:
        list_doc = await _get_list_or_404(db, list_id, current_user["id"])
        return await _serialize_list_with_items_count(db, list_doc, current_user["id"])
    updates["updated_at"] = utcnow()
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]},
        {"$set": updates},
    )
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return await _serialize_list_with_items_count(db, list_doc, current_user["id"])


@router.delete("/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_list_or_404(db, list_id, current_user["id"])
    await db.lists.delete_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]}
    )
    await db.items.delete_many({"list_id": list_id, "user_id": current_user["id"]})
    return None


@router.get("/{list_id}/items", response_model=list[ItemOut])
async def list_items(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_list_or_404(db, list_id, current_user["id"])
    cursor = db.items.find({"list_id": list_id, "user_id": current_user["id"]}).sort(
        [("sort_order", 1), ("created_at", 1)]
    )
    docs = await cursor.to_list(length=None)
    return [serialize_doc(doc) for doc in docs]


@router.post("/{list_id}/items", response_model=ItemOut, status_code=201)
async def create_item(
    list_id: str,
    payload: ItemCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    _ensure_list_is_active(list_doc)
    now = utcnow()
    doc = {
        "user_id": current_user["id"],
        "list_id": list_id,
        "name": payload.name,
        "qty": payload.qty,
        "purchased": False,
        "purchased_at": None,
        "sort_order": payload.sort_order,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.items.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


@router.post("/{list_id}/items/reorder", response_model=list[ItemOut])
async def reorder_items(
    list_id: str,
    payload: ReorderListItems,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    _ensure_list_is_active(list_doc)
    item_ids = payload.item_ids
    if len(item_ids) != len(set(item_ids)):
        raise HTTPException(
            status_code=400, detail="Item ids must not contain duplicates."
        )

    existing_items = await db.items.find(
        {"list_id": list_id, "user_id": current_user["id"]}
    ).to_list(length=None)
    existing_item_ids = {str(item["_id"]) for item in existing_items}
    if set(item_ids) != existing_item_ids:
        raise HTTPException(
            status_code=400,
            detail="Item ids must include every item in the list exactly once.",
        )

    now = utcnow()
    operations = []
    for sort_order, item_id in enumerate(item_ids):
        operations.append(
            UpdateOne(
                {
                    "_id": to_object_id(item_id, "item_id"),
                    "list_id": list_id,
                    "user_id": current_user["id"],
                },
                {"$set": {"sort_order": sort_order, "updated_at": now}},
            )
        )
    if operations:
        await db.items.bulk_write(operations)
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]},
        {"$set": {"updated_at": now}},
    )

    cursor = db.items.find({"list_id": list_id, "user_id": current_user["id"]}).sort(
        [("sort_order", 1), ("created_at", 1)]
    )
    docs = await cursor.to_list(length=None)
    return [serialize_doc(doc) for doc in docs]
