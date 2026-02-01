from motor.motor_asyncio import AsyncIOMotorClient

from .config import settings


_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


def get_db():
    return get_client()[settings.mongo_db]


async def init_db() -> None:
    db = get_db()
    await db.users.create_index("google_sub", unique=True)
    await db.lists.create_index([("user_id", 1), ("updated_at", -1)])
    await db.items.create_index([("user_id", 1), ("list_id", 1)])
    await db.items.create_index([("list_id", 1), ("sort_order", 1)])
    await db.templates.create_index([("user_id", 1), ("updated_at", -1)])
    await db.template_items.create_index([("user_id", 1), ("template_id", 1)])
    await db.template_items.create_index([("template_id", 1), ("sort_order", 1)])
