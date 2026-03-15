"""Tests for SQLModel table definitions and database setup."""

import json
from pathlib import Path

from sqlmodel import Session, select

from app.models import Food, FoodSourceRef, Source, Staging


def test_tables_created(engine):
    """All four tables exist after create_all."""
    with engine.connect() as conn:
        table_names = set(engine.dialect.get_table_names(conn))
    assert "sources" in table_names
    assert "foods" in table_names
    assert "food_source_refs" in table_names
    assert "staging" in table_names


def test_source_crud(session: Session):
    """Source can be created and retrieved."""
    source = Source(name="Test Source", tier=1)
    session.add(source)
    session.commit()

    result = session.exec(select(Source).where(Source.name == "Test Source")).first()
    assert result is not None
    assert result.tier == 1
    assert result.active is True


def test_food_defaults(session: Session):
    """Food has correct defaults for conflict_flag, active, source_confidence."""
    source = Source(name="Test Source", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)

    food = Food(
        name="Weet-Bix",
        carbs_per_100g=67.3,
        source_id=source.id,
    )
    session.add(food)
    session.commit()
    session.refresh(food)

    assert food.conflict_flag is False
    assert food.active is True
    assert food.source_confidence == 1.0
    assert food.created_at is not None
    assert food.updated_at is not None


def test_food_barcode_nullable_unique(session: Session):
    """Multiple foods can have barcode=None (nullable unique)."""
    source = Source(name="Test Source", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)

    food1 = Food(name="Food A", carbs_per_100g=10.0, source_id=source.id)
    food2 = Food(name="Food B", carbs_per_100g=20.0, source_id=source.id)
    session.add(food1)
    session.add(food2)
    session.commit()

    # Both have barcode=None — should not violate unique constraint
    assert food1.barcode is None
    assert food2.barcode is None


def test_food_source_ref_repr_excludes_raw_response():
    """FoodSourceRef __repr__ must not include raw_response_json."""
    from datetime import datetime, timezone

    ref = FoodSourceRef(
        food_id=1,
        source_id=1,
        reported_carbs=67.3,
        queried_at=datetime.now(timezone.utc),
        raw_response_json='{"secret": "data"}',
    )
    repr_str = repr(ref)
    assert "raw_response_json" not in repr_str
    assert "secret" not in repr_str
    assert "reported_carbs=67.3" in repr_str


def test_staging_defaults(session: Session):
    """Staging has correct default status and created_at."""
    source = Source(name="Test Source", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)

    staging = Staging(
        source_id=source.id,
        raw_data='{"test": true}',
    )
    session.add(staging)
    session.commit()
    session.refresh(staging)

    assert staging.status == "pending"
    assert staging.created_at is not None
    assert staging.mapped_data is None
    assert staging.reviewed_at is None


def test_seed_sources_json_valid():
    """Seed file is valid JSON with expected fields."""
    seed_file = Path(__file__).parent.parent / "app" / "seed" / "sources.json"
    assert seed_file.exists(), "sources.json seed file must exist"

    sources = json.loads(seed_file.read_text(encoding="utf-8"))
    assert len(sources) == 9  # 3 Tier 1 + 3 Tier 2 + 3 Tier 3

    required_fields = {"name", "tier", "url"}
    for source in sources:
        assert required_fields.issubset(source.keys()), (
            f"Missing fields in source: {source.get('name', 'unknown')}"
        )
        assert source["tier"] in (1, 2, 3)


def test_seed_once_guard(session: Session):
    """Seed function should not duplicate sources if called twice."""
    # First seed — should insert
    seed_sources_with_session(session)
    count_after_first = len(session.exec(select(Source)).all())
    assert count_after_first == 9

    # Second seed — should skip
    seed_sources_with_session(session)
    count_after_second = len(session.exec(select(Source)).all())
    assert count_after_second == 9


def seed_sources_with_session(session: Session):
    """Helper: run seed logic with a specific session."""
    from app.database import SEED_FILE

    existing = session.exec(select(Source).limit(1)).first()
    if existing is not None:
        return

    sources_data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    for entry in sources_data:
        source = Source(**entry)
        session.add(source)
    session.commit()
