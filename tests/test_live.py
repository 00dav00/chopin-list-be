import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.db as db_module
from app.db import get_db
from app.main import app
from app.routers.v2.live import get_live_current_user


class _FakeLists:
    def __init__(self, owned_list_id: str | None, owner_id: str):
        self.owned_list_id = owned_list_id
        self.owner_id = owner_id

    async def find_one(self, query: dict):
        if (
            self.owned_list_id
            and query.get("_id") == ObjectId(self.owned_list_id)
            and query.get("user_id") == self.owner_id
        ):
            return {"_id": query["_id"], "user_id": query["user_id"]}
        return None


class _FakeDB:
    def __init__(self, owned_list_id: str | None, owner_id: str = "user-123"):
        self.lists = _FakeLists(owned_list_id=owned_list_id, owner_id=owner_id)


def _new_test_client() -> TestClient:
    db_module._client = None
    return TestClient(app)


def test_v2_live_socket_scaffold_message():
    list_id = str(ObjectId())
    app.dependency_overrides[get_live_current_user] = lambda: {"id": "user-123"}
    app.dependency_overrides[get_db] = lambda: _FakeDB(owned_list_id=list_id)

    with _new_test_client() as client:
        with client.websocket_connect(f"/v2/live/lists/{list_id}/ws") as websocket:
            payload = websocket.receive_json()

    app.dependency_overrides.clear()
    assert payload == {"type": "live.ready", "list_id": list_id}


def test_v2_live_socket_rejects_missing_token():
    app.dependency_overrides[get_db] = lambda: _FakeDB(owned_list_id=None)

    with _new_test_client() as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/v2/live/lists/demo-list/ws"):
                pass

    app.dependency_overrides.clear()
    assert exc_info.value.code == 1008


def test_v2_live_socket_rejects_non_owned_list():
    list_id = str(ObjectId())
    app.dependency_overrides[get_live_current_user] = lambda: {"id": "user-123"}
    app.dependency_overrides[get_db] = lambda: _FakeDB(owned_list_id=None)

    with _new_test_client() as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/v2/live/lists/{list_id}/ws"):
                pass

    app.dependency_overrides.clear()
    assert exc_info.value.code == 1008
