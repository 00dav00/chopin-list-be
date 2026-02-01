import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient


TEST_DB_NAME = os.environ.get("TEST_DB_NAME") or f"shoplist_test_{uuid.uuid4().hex}"
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DB", TEST_DB_NAME)

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
