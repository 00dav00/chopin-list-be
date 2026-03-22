import asyncio

from fastapi import APIRouter, Depends, WebSocket, WebSocketException, status
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ...auth import authenticate_google_token
from ...db import get_db
from ...utils import to_object_id

router = APIRouter(prefix="/v2/live", tags=["live-v2"])


class LiveBroker:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, list_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(list_id, set()).add(queue)
        return queue

    def unsubscribe(self, list_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(list_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(list_id, None)

    def publish_nowait(self, list_id: str, event: dict) -> None:
        for queue in list(self._subscribers.get(list_id, ())):
            queue.put_nowait(event)


class MongoChangeStreamEventSource:
    def __init__(self, db, broker: LiveBroker):
        self._db = db
        self._broker = broker
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task:
            return
        self._task = asyncio.create_task(self._watch_lists())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _watch_lists(self) -> None:
        pipeline = [
            {
                "$match": {
                    "operationType": {
                        "$in": ["insert", "update", "replace", "delete"]
                    }
                }
            }
        ]
        while True:
            try:
                async with self._db.lists.watch(
                    pipeline,
                    full_document="updateLookup",
                ) as stream:
                    async for change in stream:
                        event = self.event_from_change(change)
                        if event:
                            self._broker.publish_nowait(event["list_id"], event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"Live change stream paused: {exc}")
                await asyncio.sleep(2)

    @staticmethod
    def event_from_change(change: dict) -> dict | None:
        operation = change.get("operationType")
        if operation not in {"insert", "update", "replace", "delete"}:
            return None

        list_id = None
        full_document = change.get("fullDocument") or {}
        if full_document.get("_id") is not None:
            list_id = str(full_document["_id"])
        else:
            document_key = change.get("documentKey") or {}
            if document_key.get("_id") is not None:
                list_id = str(document_key["_id"])

        if not list_id:
            return None

        return {"type": "list.changed", "list_id": list_id, "operation": operation}


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


def get_live_broker(websocket: WebSocket) -> LiveBroker:
    broker = getattr(websocket.app.state, "live_broker", None)
    if broker is None:
        broker = LiveBroker()
        websocket.app.state.live_broker = broker
    return broker


@router.websocket("/lists/{list_id}/ws")
async def list_live_socket(
    websocket: WebSocket,
    list_id: str,
    current_user=Depends(get_live_current_user),
    db=Depends(get_db),
    broker: LiveBroker = Depends(get_live_broker),
):
    await _ensure_list_access(db, list_id, current_user["id"])
    await websocket.accept()
    await websocket.send_json({"type": "live.ready", "list_id": list_id})

    queue = broker.subscribe(list_id)
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    finally:
        broker.unsubscribe(list_id, queue)
