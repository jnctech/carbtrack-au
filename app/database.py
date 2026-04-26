"""Database engine setup, table creation, and seed-on-first-run logic.

Seeds sources table from app/seed/sources.json on first run only.
Guard: seed only when sources table has zero rows.
"""

import json
import logging
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Food, FoodSourceRef, Source, _utcnow

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/carbtrack.db")

# Ensure SQLite data directory exists (matches Docker volume mount at /app/data)
if DATABASE_URL.startswith("sqlite"):
    _db_path = DATABASE_URL.replace("sqlite:///", "")
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SEED_FILE = Path(__file__).parent / "seed" / "sources.json"
SEED_FOODS_FILE = Path(__file__).parent / "seed" / "seed_foods.json"


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def seed_sources() -> None:
    """Seed sources table from sources.json if table is empty."""
    with Session(engine) as session:
        existing = session.exec(
            select(Source).limit(1)
        ).first()
        if existing is not None:
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


def seed_foods() -> None:
    """Seed foods table from seed_foods.json if table is empty.

    Looks up AUSNUT 2011-13 source_id for branded/generic foods.
    All seed foods have source_confidence >= 0.9 (Tier 1 sourced).
    """
    with Session(engine) as session:
        existing = session.exec(select(Food).limit(1)).first()
        if existing is not None:
            logger.info("Foods table already seeded — skipping")
            return

        if not SEED_FOODS_FILE.exists():
            logger.warning("Seed foods file not found: %s", SEED_FOODS_FILE)
            return

        # Look up AUSNUT source for source_id
        ausnut = session.exec(
            select(Source).where(Source.name == "AUSNUT 2011-13")
        ).first()
        if not ausnut:
            logger.warning(
                "AUSNUT 2011-13 source not found — cannot seed foods. "
                "Ensure seed_sources() runs first."
            )
            return
        default_source_id = ausnut.id

        foods_data = json.loads(SEED_FOODS_FILE.read_text(encoding="utf-8"))
        now = _utcnow()
        for entry in foods_data:
            food = Food(**entry, source_id=default_source_id)
            session.add(food)
            session.flush()  # populate food.id for FoodSourceRef

            # Create audit trail — ensures conflict detection works for future imports
            if default_source_id is not None:
                session.add(
                    FoodSourceRef(
                        food_id=food.id,
                        source_id=default_source_id,
                        reported_carbs=entry["carbs_per_100g"],
                        queried_at=now,
                    )
                )
        session.commit()
        logger.info("Seeded %d foods from %s", len(foods_data), SEED_FOODS_FILE.name)


def top_up_seed_foods() -> None:
    """Insert any seed entries missing from a non-empty foods table.

    Idempotent: matches existing rows by (name, brand, barcode) including
    soft-deleted (active=false) rows so admins can permanently retire an
    item by deactivating without it reappearing on restart. Skips entirely
    when the table is empty (seed_foods() handles initial population).
    """
    with Session(engine) as session:
        if session.exec(select(Food).limit(1)).first() is None:
            return  # fresh DB — seed_foods() owns this case

        if not SEED_FOODS_FILE.exists():
            return

        ausnut = session.exec(
            select(Source).where(Source.name == "AUSNUT 2011-13")
        ).first()
        if ausnut is None:
            return

        foods_data = json.loads(SEED_FOODS_FILE.read_text(encoding="utf-8"))
        now = _utcnow()
        added = 0
        for entry in foods_data:
            statement = select(Food).where(Food.name == entry["name"])
            statement = statement.where(
                Food.brand == entry["brand"]
                if entry.get("brand") is not None
                else Food.brand.is_(None)  # type: ignore[union-attr]
            )
            statement = statement.where(
                Food.barcode == entry["barcode"]
                if entry.get("barcode") is not None
                else Food.barcode.is_(None)  # type: ignore[union-attr]
            )
            if session.exec(statement.limit(1)).first() is not None:
                continue

            food = Food(**entry, source_id=ausnut.id)
            session.add(food)
            session.flush()
            session.add(
                FoodSourceRef(
                    food_id=food.id,
                    source_id=ausnut.id,
                    reported_carbs=entry["carbs_per_100g"],
                    queried_at=now,
                )
            )
            added += 1

        if added:
            session.commit()
            logger.info("Top-up added %d new generic foods from %s", added, SEED_FOODS_FILE.name)


def init_db() -> None:
    """Create tables and seed on first run; top-up new generic foods on later runs."""
    create_db_and_tables()
    seed_sources()
    seed_foods()
    top_up_seed_foods()


def get_session():
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session
