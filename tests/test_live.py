from fastapi.testclient import TestClient

from app.main import app


def test_v2_live_socket_scaffold_message():
    with TestClient(app) as client:
        with client.websocket_connect("/v2/live/lists/demo-list/ws") as websocket:
            payload = websocket.receive_json()

    assert payload == {"type": "live.scaffold", "list_id": "demo-list"}
