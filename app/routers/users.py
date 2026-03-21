from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument

from ..auth import get_current_user
from ..db import get_db
from ..schemas import ConfirmedUserOut, DashboardSummary, PendingUserOut, UserOut
from ..utils import serialize_doc

router = APIRouter(prefix="/me", tags=["users"])


def require_admin(current_user: dict):
    if not current_user.get("admin", False):
        raise HTTPException(status_code=403, detail="Admin access required.")


def to_user_object_id(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except InvalidId as exc:
        raise HTTPException(status_code=404, detail="User not found.") from exc


async def get_user_or_404(db, user_id: str) -> dict:
    user_doc = await db.users.find_one({"_id": to_user_object_id(user_id)})
    if not user_doc:
        raise HTTPException(status_code=404, detail="User not found.")
    return user_doc


@router.get("", response_model=UserOut)
async def read_me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/dashboard", response_model=DashboardSummary)
async def read_dashboard_summary(current_user=Depends(get_current_user), db=Depends(get_db)):
    user_id = current_user["id"]

    active_list_count = await db.lists.count_documents(
        {"user_id": user_id, "completed": {"$ne": True}}
    )
    completed_list_count = await db.lists.count_documents(
        {"user_id": user_id, "completed": True}
    )
    templates_count = await db.templates.count_documents({"user_id": user_id})

    list_cursor = (
        db.lists.find({"user_id": user_id, "completed": {"$ne": True}})
        .sort("created_at", -1)
        .limit(5)
    )
    last_created_lists = [serialize_doc(doc) for doc in await list_cursor.to_list(length=5)]
    for doc in last_created_lists:
        doc["completed"] = doc.get("completed", False)
    last_list_ids = [doc["id"] for doc in last_created_lists]
    list_items_counts: dict[str, int] = {}
    if last_list_ids:
        list_items_cursor = db.items.aggregate(
            [
                {"$match": {"user_id": user_id, "list_id": {"$in": last_list_ids}}},
                {"$group": {"_id": "$list_id", "count": {"$sum": 1}}},
            ]
        )
        for row in await list_items_cursor.to_list(length=None):
            list_items_counts[row["_id"]] = row["count"]
    for doc in last_created_lists:
        doc["items_count"] = list_items_counts.get(doc["id"], 0)

    cursor = db.templates.find({"user_id": user_id}).sort("created_at", -1).limit(5)
    last_created_templates = [serialize_doc(doc) for doc in await cursor.to_list(length=5)]
    last_template_ids = [doc["id"] for doc in last_created_templates]
    template_items_counts: dict[str, int] = {}
    if last_template_ids:
        template_items_cursor = db.template_items.aggregate(
            [
                {
                    "$match": {
                        "user_id": user_id,
                        "template_id": {"$in": last_template_ids},
                    }
                },
                {"$group": {"_id": "$template_id", "count": {"$sum": 1}}},
            ]
        )
        for row in await template_items_cursor.to_list(length=None):
            template_items_counts[row["_id"]] = row["count"]
    for doc in last_created_templates:
        doc["items_count"] = template_items_counts.get(doc["id"], 0)

    summary = {
        "active_list_count": active_list_count,
        "completed_list_count": completed_list_count,
        "templates_count": templates_count,
        "last_created_lists": last_created_lists,
        "last_created_templates": last_created_templates,
    }

    if current_user.get("admin", False):
        summary["confirmed_users_count"] = await db.users.count_documents(
            {"approved": True}
        )
        summary["pending_users_count"] = await db.users.count_documents(
            {"approved": {"$ne": True}}
        )

    return summary


@router.get("/admin/pending-users", response_model=list[PendingUserOut])
async def list_pending_users(current_user=Depends(get_current_user), db=Depends(get_db)):
    require_admin(current_user)

    cursor = db.users.find({"approved": {"$ne": True}}).sort("created_at", -1)
    users = [serialize_doc(doc) for doc in await cursor.to_list(length=None)]

    for user in users:
        user["approved"] = bool(user.get("approved", False))

    return users


@router.post("/admin/users/{user_id}/approve", response_model=PendingUserOut)
async def approve_user(user_id: str, current_user=Depends(get_current_user), db=Depends(get_db)):
    require_admin(current_user)

    result = await db.users.find_one_and_update(
        {"_id": to_user_object_id(user_id)},
        {"$set": {"approved": True}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(status_code=404, detail="User not found.")

    user = serialize_doc(result)
    user["approved"] = bool(user.get("approved", False))
    return user


@router.get("/admin/confirmed-users", response_model=list[ConfirmedUserOut])
async def list_confirmed_users(current_user=Depends(get_current_user), db=Depends(get_db)):
    require_admin(current_user)

    cursor = db.users.find({"approved": True}).sort("created_at", -1)
    users = [serialize_doc(doc) for doc in await cursor.to_list(length=None)]
    for user in users:
        user["approved"] = bool(user.get("approved", False))
    return users


@router.post("/admin/users/{user_id}/unconfirm", response_model=ConfirmedUserOut)
async def unconfirm_user(user_id: str, current_user=Depends(get_current_user), db=Depends(get_db)):
    require_admin(current_user)

    result = await db.users.find_one_and_update(
        {"_id": to_user_object_id(user_id)},
        {"$set": {"approved": False}},
        return_document=ReturnDocument.AFTER,
    )
    if not result:
        raise HTTPException(status_code=404, detail="User not found.")

    user = serialize_doc(result)
    user["approved"] = bool(user.get("approved", False))
    return user


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pending_user(
    user_id: str, current_user=Depends(get_current_user), db=Depends(get_db)
):
    require_admin(current_user)

    user_doc = await get_user_or_404(db, user_id)
    if user_doc.get("approved", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Confirmed users cannot be deleted from pending users.",
        )

    serialized_user = serialize_doc(user_doc)
    deleted_user_id = serialized_user["id"]

    await db.users.delete_one({"_id": to_user_object_id(user_id)})
    await db.items.delete_many({"user_id": deleted_user_id})
    await db.lists.delete_many({"user_id": deleted_user_id})
    await db.template_items.delete_many({"user_id": deleted_user_id})
    await db.templates.delete_many({"user_id": deleted_user_id})
    return None
