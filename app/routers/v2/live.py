from fastapi import APIRouter, WebSocket

router = APIRouter(prefix="/v2/live", tags=["live-v2"])


@router.websocket("/lists/{list_id}/ws")
async def list_live_socket(websocket: WebSocket, list_id: str):
    await websocket.accept()
    await websocket.send_json({"type": "live.scaffold", "list_id": list_id})
    await websocket.close(code=1000)
