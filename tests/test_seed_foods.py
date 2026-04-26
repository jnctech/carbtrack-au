"""Tests for seed foods functionality."""

import json
from pathlib import Path
from unittest.mock import patch

from sqlmodel import select

from app.database import seed_foods, top_up_seed_foods
from app.models import Food, FoodSourceRef, Source


class TestSeedFoods:
    def test_seeds_foods_on_empty_table(self, session, engine):
        """Foods should be seeded when table is empty."""
        # Seed a source first (AUSNUT)
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("app.database.engine", engine):
            seed_foods()

        foods = session.exec(select(Food)).all()
        assert len(foods) >= 50  # Spec requires >= 50

    def test_skips_if_already_seeded(self, session, engine):
        """Should not re-seed if foods table already has data."""
        food = Food(name="Existing", carbs_per_100g=10.0)
        session.add(food)
        session.commit()

        with patch("app.database.engine", engine):
            seed_foods()

        foods = session.exec(select(Food)).all()
        assert len(foods) == 1  # Only the one we added

    def test_seed_foods_have_required_fields(self, session, engine):
        """All seed foods must have name and carbs_per_100g."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("app.database.engine", engine):
            seed_foods()

        foods = session.exec(select(Food)).all()
        for food in foods:
            assert food.name, f"Food id={food.id} missing name"
            assert food.carbs_per_100g is not None, f"{food.name} missing carbs_per_100g"
            assert food.source_confidence >= 0.9, f"{food.name} confidence below 0.9"

    def test_seed_foods_json_valid(self):
        """Validate the seed file structure."""
        seed_file = Path(__file__).parent.parent / "app" / "seed" / "seed_foods.json"
        data = json.loads(seed_file.read_text(encoding="utf-8"))

        assert isinstance(data, list)
        assert len(data) >= 50

        for entry in data:
            assert "name" in entry
            assert "carbs_per_100g" in entry
            assert isinstance(entry["carbs_per_100g"], (int, float))
            assert entry["carbs_per_100g"] >= 0

    def test_seed_foods_source_confidence(self):
        """All seed foods should have confidence >= 0.9 (Tier 1)."""
        seed_file = Path(__file__).parent.parent / "app" / "seed" / "seed_foods.json"
        data = json.loads(seed_file.read_text(encoding="utf-8"))

        for entry in data:
            confidence = entry.get("source_confidence", 1.0)
            assert confidence >= 0.9, f"{entry['name']} has confidence {confidence} < 0.9"

    def test_seed_creates_food_source_refs(self, session, engine):
        """Seed foods must create FoodSourceRef records for conflict detection."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("app.database.engine", engine):
            seed_foods()

        foods = session.exec(select(Food)).all()
        refs = session.exec(select(FoodSourceRef)).all()

        # Every seeded food should have a corresponding FoodSourceRef
        assert len(refs) == len(foods)
        for ref in refs:
            assert ref.reported_carbs >= 0
            assert ref.source_id == source.id


class TestTopUpSeedFoods:
    """top_up_seed_foods() must add missing generic entries to a non-empty DB."""

    def test_skips_when_table_empty(self, session, engine):
        """Empty DB is seed_foods()'s job — top-up bails out."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("app.database.engine", engine):
            top_up_seed_foods()

        assert session.exec(select(Food)).all() == []

    def test_inserts_missing_generic_into_existing_db(self, session, engine):
        """Existing DB missing one of the seed entries gets it filled in."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()
        session.refresh(source)
        session.add(Food(name="Existing", carbs_per_100g=10.0, source_id=source.id))
        session.commit()

        with patch("app.database.engine", engine):
            top_up_seed_foods()

        names = {f.name for f in session.exec(select(Food)).all()}
        assert "Existing" in names
        assert "Plain flour, wheat" in names
        assert "Pear, raw, unpeeled" in names
        assert "Cocoa powder, unsweetened" in names

    def test_idempotent_when_run_twice(self, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()
        session.add(Food(name="Existing", carbs_per_100g=10.0))
        session.commit()

        with patch("app.database.engine", engine):
            top_up_seed_foods()
            count_after_first = len(session.exec(select(Food)).all())
            top_up_seed_foods()
            count_after_second = len(session.exec(select(Food)).all())

        assert count_after_first == count_after_second

    def test_respects_soft_deleted_entries(self, session, engine):
        """If admin soft-deleted a generic, top-up must NOT reinsert it."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()
        session.refresh(source)
        # Soft-delete an entry that's in the seed file
        retired = Food(
            name="Plain flour, wheat",
            brand=None,
            barcode=None,
            carbs_per_100g=71.4,
            active=False,
            source_id=source.id,
        )
        session.add(retired)
        session.commit()

        with patch("app.database.engine", engine):
            top_up_seed_foods()

        flour_rows = session.exec(
            select(Food).where(Food.name == "Plain flour, wheat")
        ).all()
        assert len(flour_rows) == 1
        assert flour_rows[0].active is False

    def test_creates_food_source_ref_for_each_top_up(self, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()
        session.refresh(source)
        session.add(Food(name="Existing", carbs_per_100g=10.0, source_id=source.id))
        session.commit()

        with patch("app.database.engine", engine):
            top_up_seed_foods()

        foods = session.exec(select(Food)).all()
        refs = session.exec(select(FoodSourceRef)).all()
        # Existing food was added without a ref; top-up must create a ref per
        # newly inserted food (foods - 1 == refs).
        assert len(refs) == len(foods) - 1
        for ref in refs:
            assert ref.source_id == source.id
