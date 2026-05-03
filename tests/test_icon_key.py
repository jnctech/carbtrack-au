"""Tests for icon_key column + backfill helpers."""

import os
from pathlib import Path

import app.database as db_module
from app.database import (
    ICON_KEY_BACKFILL,
    create_db_and_tables,
    migrate_schema,
    seed_icon_keys,
)
from app.models import Food


def test_icon_key_field_round_trip(client, session):
    food = Food(name="Weet-Bix, original", carbs_per_100g=67.3, icon_key="cereal_weetbix")
    session.add(food)
    session.commit()
    response = client.get(f"/foods/{food.id}")
    assert response.status_code == 200
    assert response.json()["icon_key"] == "cereal_weetbix"


def test_seed_icon_keys_idempotent_and_skips_unmatched(monkeypatch, tmp_path):
    """End-to-end against a real on-disk SQLite engine to exercise migrate + seed."""
    db_path = tmp_path / "carb.db"
    monkeypatch.setattr(
        db_module, "engine", db_module.create_engine(f"sqlite:///{db_path}")
    )
    create_db_and_tables()
    migrate_schema()  # idempotent on a brand-new schema

    from sqlmodel import Session, select

    with Session(db_module.engine) as s:
        s.add(Food(name="Weet-Bix, original", carbs_per_100g=67.3))
        s.add(Food(name="Some Unmapped Food", carbs_per_100g=10.0))
        s.commit()

    seed_icon_keys()
    seed_icon_keys()  # second run is a no-op

    with Session(db_module.engine) as s:
        weetbix = s.exec(select(Food).where(Food.name == "Weet-Bix, original")).first()
        unmapped = s.exec(select(Food).where(Food.name == "Some Unmapped Food")).first()
        assert weetbix.icon_key == "cereal_weetbix"
        assert unmapped.icon_key is None


def test_backfill_mapping_has_expected_count():
    assert len(ICON_KEY_BACKFILL) == 21


def test_init_db_creates_attachments_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "ATTACHMENTS_DIR", Path(tmp_path) / "att")
    db_path = tmp_path / "carb.db"
    monkeypatch.setattr(
        db_module, "engine", db_module.create_engine(f"sqlite:///{db_path}")
    )
    monkeypatch.setattr(db_module, "SEED_FILE", tmp_path / "missing-sources.json")
    monkeypatch.setattr(db_module, "SEED_FOODS_FILE", tmp_path / "missing-foods.json")

    db_module.init_db()
    assert (Path(tmp_path) / "att").exists()
    assert (Path(tmp_path) / "att" / "thumbs").exists()
    # Cleanup env so other tests aren't affected
    os.environ.pop("ATTACHMENTS_DIR", None)
