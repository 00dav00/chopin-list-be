import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient


TEST_DB_NAME = os.environ.get("TEST_DB_NAME") or f"shoplist_test_{uuid.uuid4().hex}"
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", TEST_DB_NAME)
# Force the FE origin to a fixed sentinel so `test_main.py` can assert against
# a literal. `setdefault` is not enough here: CI exports CHOPIN_LIST_FE_URL,
# which would otherwise win and break the CORS preflight contract test.
os.environ["CHOPIN_LIST_FE_URL"] = "http://chopin-test-fe.invalid"
# Unset, not blanked: a real key in a developer's .env would otherwise switch
# every notification test onto the live Brevo API.
os.environ.pop("BREVO_API_KEY", None)
# Same for Bedrock: no test may reach the real model, whatever .env holds.
for _aws_var in (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_PROFILE",
):
    os.environ.pop(_aws_var, None)
# And for Scaleway, plus the provider choice, so each test picks its own.
for _model_var in ("SCALEWAY_API_KEY", "SCALEWAY_MODEL", "MODEL_PROVIDER"):
    os.environ.pop(_model_var, None)

from app.auth import get_current_user  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402

TEST_USER = {
    "id": "user-123",
    "email": "user@example.com",
    "name": "Test User",
    "avatar_url": "https://example.com/avatar.png",
    "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    "last_login_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
}


@pytest.fixture
def current_user():
    return TEST_USER


@pytest_asyncio.fixture
async def mongo_client():
    client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    yield client
    client.close()


@pytest_asyncio.fixture(scope="session")
def db_name():
    return TEST_DB_NAME


@pytest_asyncio.fixture
async def db(mongo_client, db_name):
    database = mongo_client[db_name]
    yield database
    for name in await database.list_collection_names():
        await database.drop_collection(name)


@pytest_asyncio.fixture
async def app(db):
    async def override_get_db():
        return db

    def override_get_current_user():
        return TEST_USER

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_user] = override_get_current_user
    yield fastapi_app
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def block_outbound_http(monkeypatch):
    """Fail any real outbound HTTP request made through httpx.

    Patches the network transport class only. ASGITransport, which every router
    test's client uses, is a separate class and is untouched.
    """

    async def refuse(transport, request):
        raise AssertionError(f"real outbound HTTP in a test: {request.url}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)


@pytest.fixture(autouse=True)
def block_real_bedrock_client(monkeypatch):
    """Fail any attempt to build a real Bedrock client; tests inject their own."""
    from app import bedrock

    def refuse(*args, **kwargs):
        raise AssertionError("real Bedrock client created in a test")

    monkeypatch.setattr(bedrock, "_create_client", refuse)
    monkeypatch.setattr(bedrock, "_client", None)


class FakeClassifier:
    """Stands in for app.classifier.classify; assigns groups by item name.

    Set `gate` to an asyncio.Event to hold the answer until the test sets it;
    `entered` fires once the call is in flight, so edits land "during the wait".
    """

    def __init__(self):
        self.assignments: dict[str, str] = {}
        self.calls: list[tuple[list[str], list[str]]] = []
        self.error: Exception | None = None
        self.gate: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def classify(self, item_names, group_names):
        self.calls.append((list(item_names), list(group_names)))
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return {
            name: self.assignments[name] if self.assignments.get(name) in group_names else "Other"
            for name in item_names
        }


@pytest.fixture
def fake_classifier(monkeypatch):
    from app import classifier

    fake = FakeClassifier()
    monkeypatch.setattr(classifier, "classify", fake.classify)
    return fake


class FakeBrevo:
    """Stands in for the network: records requests, answers with `respond`."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.respond = lambda request: httpx.Response(
            201, json={"messageId": "<test@smtp-relay.mailin.fr>"}
        )

    def payload(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


@pytest.fixture
def brevo_api(monkeypatch):
    """Opt in to outbound HTTP, answered by a FakeBrevo instead of the network."""
    fake = FakeBrevo()

    async def handle(transport, request):
        fake.requests.append(request)
        return fake.respond(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    return fake
