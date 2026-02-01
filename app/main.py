from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db
from .routers import items, lists, templates, users

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Shoplist API", version="1.0.0", lifespan=lifespan)


@app.get("/", tags=["meta"])
async def root():
    return {"status": "ok"}


app.include_router(users.router)
app.include_router(lists.router)
app.include_router(items.router)
app.include_router(templates.router)
