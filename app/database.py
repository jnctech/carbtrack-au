"""Database engine setup, table creation, and seed-on-first-run logic.

Seeds sources table from app/seed/sources.json on first run only.
Guard: seed only when sources table has zero rows.
"""

import json
import logging
import os
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import (
    Food,
    FoodSourceRef,
    Recipe,
    RecipeAttachment,
    RecipeIngredient,
    Source,
    _utcnow,
)

__all__ = [
    "engine",
    "init_db",
    "get_session",
    "create_db_and_tables",
    "migrate_schema",
    "seed_sources",
    "seed_foods",
    "seed_icon_keys",
    "Recipe",
    "RecipeIngredient",
    "RecipeAttachment",
]

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


ICON_KEY_BACKFILL = {
    "Weet-Bix, original": "cereal_weetbix",
    "Corn flakes": "cereal_cornflakes",
    "White bread, sliced": "bread_white",
    "Full cream milk": "dairy_milk_fc",
    "Greek yoghurt, natural": "dairy_yoghurt_greek",
    "Oat milk": "dairy_milk_oat",
    "Banana, raw": "fruit_banana",
    "Apple, raw, unpeeled": "fruit_apple_red",
    "Orange, raw": "fruit_orange",
    "Strawberry, raw": "fruit_strawberry",
    "Mango, raw": "fruit_mango",
    "Watermelon, raw": "fruit_watermelon",
    "Avocado, raw": "fruit_avocado",
    "Potato, white, boiled": "veg_potato_white",
    "Sweet potato, boiled": "veg_sweet_potato",
    "Broccoli, boiled": "veg_broccoli",
    "Carrot, raw": "veg_carrot",
    "Pumpkin, boiled": "veg_pumpkin",
    "Corn, sweet, canned": "veg_corn_sweet",
    "Peas, green, boiled": "veg_peas",
    "Broccolini, boiled": "veg_broccolini",
}


ATTACHMENTS_DIR = Path(os.getenv("ATTACHMENTS_DIR", "/app/data/attachments"))


def migrate_schema() -> None:
    """Add columns to existing tables when SQLModel.metadata.create_all cannot.

    `create_all` skips tables that already exist, so new columns on existing
    tables (e.g. `foods.icon_key`) need a manual `ALTER TABLE`. Idempotent —
    inspects current columns first.
    """
    inspector = inspect(engine)
    if "foods" not in inspector.get_table_names():
        return

    food_columns = {col["name"] for col in inspector.get_columns("foods")}
    if "icon_key" not in food_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE foods ADD COLUMN icon_key VARCHAR"))
        logger.info("Added foods.icon_key column")


def seed_icon_keys() -> None:
    """Backfill icon_key for the 21 confirmed seed foods. Idempotent."""
    with Session(engine) as session:
        updated = 0
        for name, icon in ICON_KEY_BACKFILL.items():
            food = session.exec(select(Food).where(Food.name == name)).first()
            if food is None or food.icon_key == icon:
                continue
            food.icon_key = icon
            session.add(food)
            updated += 1
        if updated:
            session.commit()
            logger.info("Backfilled icon_key on %d foods", updated)


def init_db() -> None:
    """Create tables, run migrations, and seed on first run."""
    create_db_and_tables()
    migrate_schema()
    seed_sources()
    seed_foods()
    seed_icon_keys()
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    (ATTACHMENTS_DIR / "thumbs").mkdir(parents=True, exist_ok=True)


def get_session():
    """FastAPI dependency — yields a database session."""
    with Session(engine) as session:
        yield session
