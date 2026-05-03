"""FastAPI entry point for CarbTrack AU."""

import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import attachments, foods, queries, recipes, sources, staging

load_dotenv()

# Python's stdlib mimetypes tables on python:3.12-slim don't include WebP,
# so StaticFiles served thumbs as text/plain. Register before any mount.
mimetypes.add_type("image/webp", ".webp")

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

# Serve recipe attachments. Production reverse proxy currently passes
# /attachments/* through to FastAPI, so we always mount when the directory
# can be created. Skipped only when the env var points somewhere unwritable
# (e.g. tests use tmp_path via monkeypatch and create the dir themselves).
_ATT_DIR = Path(os.getenv("ATTACHMENTS_DIR", "/app/data/attachments"))
try:
    _ATT_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/attachments", StaticFiles(directory=_ATT_DIR), name="attachments")
except OSError:
    pass


@app.get("/health")
def health():
    return {"status": "ok"}
