from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..db import get_db
from ..schemas import ItemOut, ItemUpdate
from ..utils import serialize_doc, to_object_id, utcnow

router = APIRouter(prefix="/items", tags=["items"])
LIST_COMPLETED_MUTATION_MESSAGE = (
    "Completed lists are read-only. Activate the list to edit items."
)


async def _get_item_or_404(db, item_id: str, user_id: str) -> dict:
    item_doc = await db.items.find_one(
        {"_id": to_object_id(item_id, "item_id"), "user_id": user_id}
    )
    if not item_doc:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item_doc


async def _get_list_or_404(db, list_id: str, user_id: str) -> dict:
    list_doc = await db.lists.find_one(
        {"_id": to_object_id(list_id, "list_id"), "user_id": user_id}
    )
    if not list_doc:
        raise HTTPException(status_code=404, detail="List not found.")
    return list_doc


def _ensure_list_is_active(list_doc: dict) -> None:
    if list_doc.get("completed", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=LIST_COMPLETED_MUTATION_MESSAGE,
        )


@router.patch("/{item_id}", response_model=ItemOut)
async def update_item(
    item_id: str,
    payload: ItemUpdate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    item_doc = await _get_item_or_404(db, item_id, current_user["id"])
    list_doc = await _get_list_or_404(db, item_doc["list_id"], current_user["id"])
    _ensure_list_is_active(list_doc)
    updates: dict = {}
    fields = payload.model_fields_set
    if "name" in fields:
        updates["name"] = payload.name
    if "qty" in fields:
        updates["qty"] = payload.qty
    if "sort_order" in fields:
        updates["sort_order"] = payload.sort_order
    if "purchased" in fields:
        updates["purchased"] = payload.purchased
        updates["purchased_at"] = utcnow() if payload.purchased else None

    if not updates:
        return serialize_doc(item_doc)

    updates["updated_at"] = utcnow()
    await db.items.update_one(
        {"_id": to_object_id(item_id, "item_id"), "user_id": current_user["id"]},
        {"$set": updates},
    )
    item_doc = await _get_item_or_404(db, item_id, current_user["id"])
    return serialize_doc(item_doc)


@router.post("/{item_id}/toggle", response_model=ItemOut)
async def toggle_item(
    item_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    item_doc = await _get_item_or_404(db, item_id, current_user["id"])
    list_doc = await _get_list_or_404(db, item_doc["list_id"], current_user["id"])
    _ensure_list_is_active(list_doc)
    new_state = not item_doc.get("purchased", False)
    updates = {
        "purchased": new_state,
        "purchased_at": utcnow() if new_state else None,
        "updated_at": utcnow(),
    }
    await db.items.update_one(
        {"_id": to_object_id(item_id, "item_id"), "user_id": current_user["id"]},
        {"$set": updates},
    )
    item_doc = await _get_item_or_404(db, item_id, current_user["id"])
    return serialize_doc(item_doc)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    item_doc = await _get_item_or_404(db, item_id, current_user["id"])
    list_doc = await _get_list_or_404(db, item_doc["list_id"], current_user["id"])
    _ensure_list_is_active(list_doc)
    await db.items.delete_one(
        {"_id": to_object_id(item_id, "item_id"), "user_id": current_user["id"]}
    )
    return None
