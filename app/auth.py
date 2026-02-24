from fastapi import Depends, Header, HTTPException
from google.auth.transport import requests
from google.oauth2 import id_token
from pymongo import ReturnDocument

from .config import settings
from .db import get_db
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
            "created_at": now,
        },
    }

    user_doc = await db.users.find_one_and_update(
        {"google_sub": id_info.get("sub")},
        update,
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    return serialize_doc(user_doc)
