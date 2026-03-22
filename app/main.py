from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import get_db, init_db
from .routers import items, lists, templates, users
from .routers.v2 import live as live_v2
from .routers.v2.live import LiveBroker, MongoChangeStreamEventSource

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    app.state.live_broker = LiveBroker()
    app.state.live_event_source = MongoChangeStreamEventSource(
        get_db(), app.state.live_broker
    )
    await app.state.live_event_source.start()
    try:
        yield
    finally:
        await app.state.live_event_source.stop()


app = FastAPI(title="Shoplist API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.chopin_list_fe_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["meta"])
async def root():
    return {"status": "ok"}


app.include_router(users.router)
app.include_router(lists.router)
app.include_router(items.router)
app.include_router(templates.router)
app.include_router(live_v2.router)
