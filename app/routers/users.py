from fastapi import APIRouter, Depends

from ..auth import get_current_user
from ..db import get_db
from ..schemas import DashboardSummary, UserOut
from ..utils import serialize_doc

router = APIRouter(prefix="/me", tags=["users"])


@router.get("", response_model=UserOut)
async def read_me(current_user=Depends(get_current_user)):
    return current_user


@router.get("/dashboard", response_model=DashboardSummary)
async def read_dashboard_summary(current_user=Depends(get_current_user), db=Depends(get_db)):
    user_id = current_user["id"]

    list_count = await db.lists.count_documents({"user_id": user_id})
    templates_count = await db.templates.count_documents({"user_id": user_id})

    list_cursor = db.lists.find({"user_id": user_id}).sort("created_at", -1).limit(5)
    last_created_lists = [serialize_doc(doc) for doc in await list_cursor.to_list(length=5)]

    cursor = db.templates.find({"user_id": user_id}).sort("created_at", -1).limit(5)
    last_created_templates = [serialize_doc(doc) for doc in await cursor.to_list(length=5)]

    return {
        "list_count": list_count,
        "templates_count": templates_count,
        "last_created_lists": last_created_lists,
        "last_created_templates": last_created_templates,
    }
