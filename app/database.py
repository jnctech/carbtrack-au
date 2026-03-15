"""Database engine setup, table creation, and seed-on-first-run logic.

Seeds sources table from app/seed/sources.json on first run only.
Guard: seed only when sources table has zero rows.
"""

import json
import logging
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Source

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./carbtrack.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SEED_FILE = Path(__file__).parent / "seed" / "sources.json"


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def seed_sources() -> None:
    """Seed sources table from sources.json if table is empty."""
    with Session(engine) as session:
        count = session.exec(
            select(Source).limit(1)
        ).first()
        if count is not None:
            logger.info("Sources table already seeded — skipping")
            return

        if not SEED_FILE.exists():
            logger.warning("Seed file not found: %s", SEED_FILE)
            return

        sources_data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
        for entry in sources_data:
            source = Source(**entry)
            session.add(source)
        session.commit()
        logger.info("Seeded %d sources from %s", len(sources_data), SEED_FILE.name)


def init_db() -> None:
    """Create tables and seed on first run."""
    create_db_and_tables()
    seed_sources()


def get_session():
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session
