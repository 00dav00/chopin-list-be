from fastapi import Depends, Header, HTTPException
from google.auth.transport import requests
from google.oauth2 import id_token
from pymongo import ReturnDocument

from .config import settings
from .db import get_db
from .notifications import dispatch_new_user_notification
from .utils import serialize_doc, utcnow


async def get_current_user(
    authorization: str | None = Header(default=None),
    db=Depends(get_db),
):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Google ID token.")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing Google ID token.")

    return await authenticate_google_token(token, db)


async def authenticate_google_token(token: str, db) -> dict:
    try:
        id_info = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            settings.google_client_id,
        )
    except Exception as exc:
        print(f"Error verifying Google ID token: {exc}")
        raise HTTPException(status_code=401, detail="Invalid Google ID token.") from exc

    issuer = id_info.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise HTTPException(status_code=401, detail="Invalid token issuer.")

    now = utcnow()
    update = {
        "$set": {
            "email": id_info.get("email"),
            "name": id_info.get("name"),
            "avatar_url": id_info.get("picture"),
            "last_login_at": now,
        },
        "$setOnInsert": {
            "google_sub": id_info.get("sub"),
            "approved": False,
            "admin": False,
            "created_at": now,
        },
    }

    user_doc = await db.users.find_one_and_update(
        {"google_sub": id_info.get("sub")},
        update,
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    user_doc.setdefault("admin", False)

    # First-ever sign-in: notify admins that someone is waiting for approval.
    # `created_at` and `last_login_at` are both written from the same `now` in
    # the single upsert above, so BSON truncates them identically and they
    # compare exactly equal on insert -- and never again, because every re-auth
    # advances `last_login_at` alone. Do NOT compare `created_at` against a
    # freshly computed `utcnow()`: that value is microsecond-precision while the
    # stored one is milliseconds, so the comparison is false even on insert and
    # the notification silently never fires.
    #
    # Known limitation: a re-auth landing in the same millisecond as the insert
    # would misfire. That needs sub-millisecond concurrency on one google_sub;
    # documented rather than engineered around.
    #
    # Dispatch happens before the 403 below and is fire-and-forget -- it must
    # not delay, alter, or fail this request. See app/notifications.py for why
    # this is not a FastAPI BackgroundTask.
    created_at = user_doc.get("created_at")
    if created_at is not None and created_at == user_doc.get("last_login_at"):
        dispatch_new_user_notification(user_doc.get("name"), user_doc.get("email"))

    if not user_doc.get("approved", True):
        raise HTTPException(status_code=403, detail="Account pending approval.")

    return serialize_doc(user_doc)
