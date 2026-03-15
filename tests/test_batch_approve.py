"""Tests for scripts/batch_approve.py — batch staging approval."""

import json
from unittest.mock import patch

from sqlmodel import select

from app.models import Food, FoodSourceRef, Source, Staging
from app.services.approve import approve_staging_entry
from scripts.batch_approve import batch_approve


class TestApproveEntry:
    def test_approves_valid_entry(self, session):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        staging = Staging(
            source_id=source.id,
            raw_data='{"food": "test"}',
            mapped_data=json.dumps({
                "name": "Test Food",
                "carbs_per_100g": 10.0,
                "energy_kj": 500,
            }),
            status="pending",
        )
        session.add(staging)
        session.commit()

        result = approve_staging_entry(staging, session)
        session.commit()

        assert result == "approved"
        assert staging.status == "approved"

        # Food should be created
        food = session.exec(select(Food).where(Food.name == "Test Food")).first()
        assert food is not None
        assert food.carbs_per_100g == 10.0

        # FoodSourceRef should be created
        ref = session.exec(
            select(FoodSourceRef).where(FoodSourceRef.food_id == food.id)
        ).first()
        assert ref is not None
        assert ref.reported_carbs == 10.0

    def test_detects_conflict(self, session):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        # Create existing food with different carbs
        food = Food(name="Conflict Food", carbs_per_100g=10.0, source_id=source.id)
        session.add(food)
        session.flush()

        ref = FoodSourceRef(
            food_id=food.id,
            source_id=source.id,
            reported_carbs=10.0,
            queried_at=food.created_at,
        )
        session.add(ref)
        session.commit()

        # New entry with >5% carb difference
        staging = Staging(
            source_id=source.id,
            raw_data='{}',
            mapped_data=json.dumps({
                "name": "Conflict Food",
                "carbs_per_100g": 12.0,  # 20% diff
            }),
            status="pending",
        )
        session.add(staging)
        session.commit()

        result = approve_staging_entry(staging, session)
        session.commit()

        assert result == "conflict"
        assert staging.status == "conflict"
        assert "20.0%" in staging.conflict_notes

    def test_skips_no_mapping(self, session):
        staging = Staging(
            source_id=1,
            raw_data='{}',
            mapped_data=None,
            status="pending",
        )
        result = approve_staging_entry(staging, session)
        assert result == "skipped_no_mapping"

    def test_skips_missing_carbs(self, session):
        staging = Staging(
            source_id=1,
            raw_data='{}',
            mapped_data=json.dumps({"name": "Food", "energy_kj": 500}),
            status="pending",
        )
        result = approve_staging_entry(staging, session)
        assert result == "skipped_no_carbs"

    def test_skips_missing_name(self, session):
        staging = Staging(
            source_id=1,
            raw_data='{}',
            mapped_data=json.dumps({"carbs_per_100g": 10.0}),
            status="pending",
        )
        result = approve_staging_entry(staging, session)
        assert result == "skipped_no_name"

    def test_skips_invalid_json(self, session):
        staging = Staging(
            source_id=1,
            raw_data='{}',
            mapped_data="not json",
            status="pending",
        )
        result = approve_staging_entry(staging, session)
        assert result == "skipped_invalid_json"

    def test_within_5_percent_promotes(self, session):
        """Carb difference within 5% should promote, not conflict."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        food = Food(name="Close Food", carbs_per_100g=10.0, source_id=source.id)
        session.add(food)
        session.flush()

        ref = FoodSourceRef(
            food_id=food.id,
            source_id=source.id,
            reported_carbs=10.0,
            queried_at=food.created_at,
        )
        session.add(ref)
        session.commit()

        # New entry with 4% diff — should promote
        staging = Staging(
            source_id=source.id,
            raw_data='{}',
            mapped_data=json.dumps({
                "name": "Close Food Different",
                "carbs_per_100g": 10.4,
            }),
            status="pending",
        )
        session.add(staging)
        session.commit()

        result = approve_staging_entry(staging, session)
        session.commit()

        assert result == "approved"


class TestBatchApprove:
    def test_batch_approve_multiple(self, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        # Create 3 pending staging entries
        for i in range(3):
            staging = Staging(
                source_id=source.id,
                raw_data='{}',
                mapped_data=json.dumps({
                    "name": f"Food {i}",
                    "carbs_per_100g": 10.0 + i,
                }),
                status="pending",
            )
            session.add(staging)
        session.commit()

        with patch("scripts.batch_approve.engine", engine), patch(
            "scripts.batch_approve.init_db"
        ):
            results = batch_approve("AUSNUT 2011-13")

        assert results["approved"] == 3
        assert results["errors"] == 0

        # Verify foods created
        foods = session.exec(select(Food)).all()
        assert len(foods) == 3

    def test_batch_approve_dry_run(self, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        staging = Staging(
            source_id=source.id,
            raw_data='{}',
            mapped_data=json.dumps({
                "name": "Dry Run Food",
                "carbs_per_100g": 10.0,
            }),
            status="pending",
        )
        session.add(staging)
        session.commit()

        with patch("scripts.batch_approve.engine", engine), patch(
            "scripts.batch_approve.init_db"
        ):
            results = batch_approve("AUSNUT 2011-13", dry_run=True)

        assert results["approved"] == 1

        # No foods should be created in dry run
        foods = session.exec(select(Food)).all()
        assert len(foods) == 0

    def test_batch_approve_skips_non_pending(self, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        # Already approved entry
        staging = Staging(
            source_id=source.id,
            raw_data='{}',
            mapped_data=json.dumps({"name": "Already Done", "carbs_per_100g": 5.0}),
            status="approved",
        )
        session.add(staging)
        session.commit()

        with patch("scripts.batch_approve.engine", engine), patch(
            "scripts.batch_approve.init_db"
        ):
            results = batch_approve("AUSNUT 2011-13")

        assert results["approved"] == 0
