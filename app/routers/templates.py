from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..db import get_db
from ..schemas import (
    CreateListFromTemplate,
    ListOut,
    TemplateCreate,
    TemplateDetailOut,
    TemplateItemCreate,
    TemplateItemOut,
    TemplateItemUpdate,
    TemplateOut,
    TemplateUpdate,
)
from ..utils import serialize_doc, to_object_id, utcnow

router = APIRouter(prefix="/templates", tags=["templates"])


async def _get_template_or_404(db, template_id: str, user_id: str) -> dict:
    template_doc = await db.templates.find_one(
        {"_id": to_object_id(template_id, "template_id"), "user_id": user_id}
    )
    if not template_doc:
        raise HTTPException(status_code=404, detail="Template not found.")
    return template_doc


async def _get_template_item_or_404(
    db, template_id: str, item_id: str, user_id: str
) -> dict:
    item_doc = await db.template_items.find_one(
        {
            "_id": to_object_id(item_id, "template_item_id"),
            "template_id": template_id,
            "user_id": user_id,
        }
    )
    if not item_doc:
        raise HTTPException(status_code=404, detail="Template item not found.")
    return item_doc


@router.get("", response_model=list[TemplateOut])
async def list_templates(current_user=Depends(get_current_user), db=Depends(get_db)):
    cursor = db.templates.find({"user_id": current_user["id"]}).sort("updated_at", -1)
    docs = await cursor.to_list(length=None)
    return [serialize_doc(doc) for doc in docs]


@router.post("", response_model=TemplateDetailOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    now = utcnow()
    template_doc = {
        "user_id": current_user["id"],
        "name": payload.name,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.templates.insert_one(template_doc)
    template_id = str(result.inserted_id)
    template_doc["_id"] = result.inserted_id

    item_docs = []
    for item in payload.items:
        item_docs.append(
            {
                "user_id": current_user["id"],
                "template_id": template_id,
                "name": item.name,
                "qty": item.qty,
                "unit": item.unit,
                "sort_order": item.sort_order,
                "created_at": now,
                "updated_at": now,
            }
        )
    if item_docs:
        await db.template_items.insert_many(item_docs)

    items_cursor = db.template_items.find(
        {"template_id": template_id, "user_id": current_user["id"]}
    ).sort([("sort_order", 1), ("created_at", 1)])
    items = [serialize_doc(doc) for doc in await items_cursor.to_list(length=None)]
    response = serialize_doc(template_doc)
    response["items"] = items
    return response


@router.get("/{template_id}", response_model=TemplateDetailOut)
async def get_template(
    template_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    template_doc = await _get_template_or_404(db, template_id, current_user["id"])
    items_cursor = db.template_items.find(
        {"template_id": template_id, "user_id": current_user["id"]}
    ).sort([("sort_order", 1), ("created_at", 1)])
    items = [serialize_doc(doc) for doc in await items_cursor.to_list(length=None)]
    response = serialize_doc(template_doc)
    response["items"] = items
    return response


@router.patch("/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: str,
    payload: TemplateUpdate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    await _get_template_or_404(db, template_id, current_user["id"])
    updates = {}
    if "name" in payload.model_fields_set:
        updates["name"] = payload.name
    if not updates:
        template_doc = await _get_template_or_404(db, template_id, current_user["id"])
        return serialize_doc(template_doc)
    updates["updated_at"] = utcnow()
    await db.templates.update_one(
        {"_id": to_object_id(template_id, "template_id"), "user_id": current_user["id"]},
        {"$set": updates},
    )
    template_doc = await _get_template_or_404(db, template_id, current_user["id"])
    return serialize_doc(template_doc)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_template_or_404(db, template_id, current_user["id"])
    await db.templates.delete_one(
        {"_id": to_object_id(template_id, "template_id"), "user_id": current_user["id"]}
    )
    await db.template_items.delete_many(
        {"template_id": template_id, "user_id": current_user["id"]}
    )
    return None


@router.get("/{template_id}/items", response_model=list[TemplateItemOut])
async def list_template_items(
    template_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    await _get_template_or_404(db, template_id, current_user["id"])
    cursor = db.template_items.find(
        {"template_id": template_id, "user_id": current_user["id"]}
    ).sort([("sort_order", 1), ("created_at", 1)])
    docs = await cursor.to_list(length=None)
    return [serialize_doc(doc) for doc in docs]


@router.post(
    "/{template_id}/items",
    response_model=TemplateItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_template_item(
    template_id: str,
    payload: TemplateItemCreate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    await _get_template_or_404(db, template_id, current_user["id"])
    now = utcnow()
    doc = {
        "user_id": current_user["id"],
        "template_id": template_id,
        "name": payload.name,
        "qty": payload.qty,
        "unit": payload.unit,
        "sort_order": payload.sort_order,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.template_items.insert_one(doc)
    doc["_id"] = result.inserted_id
    return serialize_doc(doc)


@router.patch("/{template_id}/items/{item_id}", response_model=TemplateItemOut)
async def update_template_item(
    template_id: str,
    item_id: str,
    payload: TemplateItemUpdate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    await _get_template_or_404(db, template_id, current_user["id"])
    await _get_template_item_or_404(db, template_id, item_id, current_user["id"])
    updates: dict = {}
    fields = payload.model_fields_set
    if "name" in fields:
        updates["name"] = payload.name
    if "qty" in fields:
        updates["qty"] = payload.qty
    if "unit" in fields:
        updates["unit"] = payload.unit
    if "sort_order" in fields:
        updates["sort_order"] = payload.sort_order
    if not updates:
        item_doc = await _get_template_item_or_404(
            db, template_id, item_id, current_user["id"]
        )
        return serialize_doc(item_doc)
    updates["updated_at"] = utcnow()
    await db.template_items.update_one(
        {
            "_id": to_object_id(item_id, "template_item_id"),
            "template_id": template_id,
            "user_id": current_user["id"],
        },
        {"$set": updates},
    )
    item_doc = await _get_template_item_or_404(
        db, template_id, item_id, current_user["id"]
    )
    return serialize_doc(item_doc)


@router.delete("/{template_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template_item(
    template_id: str,
    item_id: str,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    await _get_template_or_404(db, template_id, current_user["id"])
    await _get_template_item_or_404(db, template_id, item_id, current_user["id"])
    await db.template_items.delete_one(
        {
            "_id": to_object_id(item_id, "template_item_id"),
            "template_id": template_id,
            "user_id": current_user["id"],
        }
    )
    return None


@router.post(
    "/{template_id}/create-list",
    response_model=ListOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_list_from_template(
    template_id: str,
    payload: CreateListFromTemplate,
    current_user=Depends(get_current_user),
    db=Depends(get_db),
):
    template_doc = await _get_template_or_404(db, template_id, current_user["id"])
    template_items = await db.template_items.find(
        {"template_id": template_id, "user_id": current_user["id"]}
    ).to_list(length=None)

    now = utcnow()
    list_doc = {
        "user_id": current_user["id"],
        "name": payload.name or template_doc["name"],
        "template_id": template_id,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.lists.insert_one(list_doc)
    list_id = str(result.inserted_id)
    list_doc["_id"] = result.inserted_id

    if template_items:
        item_docs = []
        for item in template_items:
            item_docs.append(
                {
                    "user_id": current_user["id"],
                    "list_id": list_id,
                    "name": item.get("name"),
                    "qty": item.get("qty"),
                    "unit": item.get("unit"),
                    "sort_order": item.get("sort_order", 0),
                    "purchased": False,
                    "purchased_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await db.items.insert_many(item_docs)

    return serialize_doc(list_doc)
