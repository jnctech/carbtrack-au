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
        default_source_id = ausnut.id if ausnut else None

        foods_data = json.loads(SEED_FOODS_FILE.read_text(encoding="utf-8"))
        now = _utcnow()
        for entry in foods_data:
            food = Food(
                name=entry["name"],
                brand=entry.get("brand"),
                category=entry.get("category"),
                barcode=entry.get("barcode"),
                source_id=default_source_id,
                source_confidence=entry.get("source_confidence", 0.9),
                carbs_per_100g=entry["carbs_per_100g"],
                sugars_per_100g=entry.get("sugars_per_100g"),
                fibre_per_100g=entry.get("fibre_per_100g"),
                energy_kj=entry.get("energy_kj"),
                protein_per_100g=entry.get("protein_per_100g"),
                fat_per_100g=entry.get("fat_per_100g"),
                sodium_mg=entry.get("sodium_mg"),
                serving_size_g=entry.get("serving_size_g"),
            )
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


def init_db() -> None:
    """Create tables and seed on first run."""
    create_db_and_tables()
    seed_sources()
    seed_foods()


def get_session():
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session
