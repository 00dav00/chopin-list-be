from fastapi import APIRouter, Depends, WebSocket, WebSocketException, status

from ...auth import authenticate_google_token
from ...db import get_db
from ...utils import to_object_id

router = APIRouter(prefix="/v2/live", tags=["live-v2"])


async def get_live_current_user(websocket: WebSocket, db=Depends(get_db)):
    token = websocket.query_params.get("token", "").strip()
    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing Google ID token.",
        )

    try:
        return await authenticate_google_token(token, db)
    except Exception as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid websocket credentials.",
        ) from exc


async def _ensure_list_access(db, list_id: str, user_id: str) -> None:
    try:
        list_doc = await db.lists.find_one(
            {"_id": to_object_id(list_id, "list_id"), "user_id": user_id}
        )
    except Exception as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="List not found.",
        ) from exc

    if not list_doc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="List not found.",
        )


@router.websocket("/lists/{list_id}/ws")
async def list_live_socket(
    websocket: WebSocket,
    list_id: str,
    current_user=Depends(get_live_current_user),
    db=Depends(get_db),
):
    await _ensure_list_access(db, list_id, current_user["id"])
    await websocket.accept()
    await websocket.send_json({"type": "live.ready", "list_id": list_id})
