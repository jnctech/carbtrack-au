"""FastAPI entry point for CarbTrack AU."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import attachments, foods, queries, recipes, sources, staging

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="CarbTrack AU",
    description="Australian food carbohydrate database for Type 1 Diabetes management",
    version="0.1.0",
    lifespan=lifespan,
)


app.include_router(sources.router)
app.include_router(foods.router)
app.include_router(staging.router)
app.include_router(queries.router)
app.include_router(recipes.router)
app.include_router(attachments.router)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Local-dev only: serve uploaded recipe attachments straight from FastAPI.
# In production the reverse proxy serves /attachments/* directly from the
# Docker volume, so this mount is skipped (directory absent in test env).
_ATT_DIR = Path(os.getenv("ATTACHMENTS_DIR", "/app/data/attachments"))
if _ATT_DIR.exists():
    app.mount("/attachments", StaticFiles(directory=_ATT_DIR), name="attachments")


@app.get("/health")
def health():
    return {"status": "ok"}
