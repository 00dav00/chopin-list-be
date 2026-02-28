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


async def _get_list_or_404(db, list_id: str, user_id: str) -> dict:
    list_doc = await db.lists.find_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": user_id}
    )
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found.")
    return list_doc


@router.get("", response_model=list[ListOut])
async def list_lists(current_user=Depends(get_current_user), db=Depends(get_db)):
    cursor = db.lists.find({"user_id": current_user["id"]}).sort("updated_at", -1)
    docs = await cursor.to_list(length=None)
    return [serialize_doc(doc) for doc in docs]


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
        "template_id": payload.template_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.lists.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


@router.get("/{list_id}", response_model=ListOut)
async def get_list(
    list_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return serialize_doc(list_doc)


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
        return serialize_doc(list_doc)
    updates["updated_at"] = utcnow()
    await db.lists.update_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": current_user["id"]},
        {"$set": updates},
    )
    list_doc = await _get_list_or_404(db, list_id, current_user["id"])
    return serialize_doc(list_doc)


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
    await _get_list_or_404(db, list_id, current_user["id"])
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
    await _get_list_or_404(db, list_id, current_user["id"])
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
