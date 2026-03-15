"""FastAPI entry point for CarbTrack AU."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import foods, queries, sources, staging

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

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
