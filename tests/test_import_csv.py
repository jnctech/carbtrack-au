"""Tests for scripts/import_csv.py — AUSNUT CSV bulk import."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import select

from app.models import Source, Staging
from scripts.import_csv import (
    _infer_category,
    import_csv,
    map_headers,
    parse_float,
    parse_row,
)


class TestMapHeaders:
    def test_maps_standard_ausnut_columns(self):
        headers = [
            "Food Key",
            "Food Name",
            "Energy, with dietary fibre (kJ)",
            "Protein (g)",
            "Total fat (g)",
            "Available carbohydrate, with sugar alcohols (g)",
            "Total sugars (g)",
            "Dietary fibre (g)",
            "Sodium (mg)",
        ]
        result = map_headers(headers)
        assert result[1] == "name"
        assert result[2] == "energy_kj"
        assert result[3] == "protein_per_100g"
        assert result[4] == "fat_per_100g"
        assert result[5] == "carbs_per_100g"
        assert result[6] == "sugars_per_100g"
        assert result[7] == "fibre_per_100g"
        assert result[8] == "sodium_mg"

    def test_case_insensitive(self):
        headers = ["FOOD NAME", "CARBOHYDRATE (G)"]
        result = map_headers(headers)
        assert result[0] == "name"
        assert result[1] == "carbs_per_100g"

    def test_ignores_unknown_columns(self):
        headers = ["Food Name", "Unknown Column", "Carbohydrate (g)"]
        result = map_headers(headers)
        assert len(result) == 2
        assert 1 not in result  # Unknown column index not mapped


class TestParseFloat:
    def test_valid_float(self):
        assert parse_float("10.5") == 10.5

    def test_integer(self):
        assert parse_float("42") == 42.0

    def test_empty_string(self):
        assert parse_float("") is None

    def test_dash(self):
        assert parse_float("-") is None

    def test_na(self):
        assert parse_float("N/A") is None
        assert parse_float("NA") is None

    def test_trace(self):
        assert parse_float("Tr") is None
        assert parse_float("tr") is None

    def test_whitespace(self):
        assert parse_float("  10.5  ") == 10.5


class TestParseRow:
    @pytest.fixture()
    def header_map(self):
        return {
            0: "food_key",
            1: "name",
            2: "energy_kj",
            3: "carbs_per_100g",
            4: "sugars_per_100g",
            5: "protein_per_100g",
        }

    def test_valid_row(self, header_map):
        row = ["12345", "Weet-Bix, original", "1490", "67.3", "3.3", "11.7"]
        result = parse_row(row, header_map)
        assert result is not None
        assert result["name"] == "Weet-Bix, original"
        assert result["carbs_per_100g"] == 67.3
        assert result["sugars_per_100g"] == 3.3
        assert result["energy_kj"] == 1490.0
        assert result["protein_per_100g"] == 11.7

    def test_missing_name_returns_none(self, header_map):
        row = ["12345", "", "1490", "67.3", "3.3", "11.7"]
        assert parse_row(row, header_map) is None

    def test_missing_carbs_returns_none(self, header_map):
        row = ["12345", "Test Food", "1490", "", "3.3", "11.7"]
        assert parse_row(row, header_map) is None

    def test_trace_values_excluded(self, header_map):
        row = ["12345", "Test Food", "1490", "10.0", "Tr", "11.7"]
        result = parse_row(row, header_map)
        assert result is not None
        assert "sugars_per_100g" not in result

    def test_no_double_conversion(self, header_map):
        """AUSNUT values are already per 100g — verify no conversion applied."""
        row = ["12345", "Test Food", "1490", "67.3", "3.3", "11.7"]
        result = parse_row(row, header_map)
        # The value should be exactly what was in the CSV — no division/multiplication
        assert result["carbs_per_100g"] == 67.3


class TestInferCategory:
    def test_breakfast_cereal(self):
        assert _infer_category("Weet-Bix, original") == "Breakfast Cereals"

    def test_bread(self):
        assert _infer_category("White bread, sliced") == "Bread & Bakery"

    def test_dairy(self):
        assert _infer_category("Full cream milk") == "Dairy"

    def test_fruit(self):
        assert _infer_category("Apple, raw, unpeeled") == "Fruit"

    def test_unknown(self):
        assert _infer_category("Mystery food item xyz") is None


class TestImportCsv:
    """Integration tests using a real database session."""

    @pytest.fixture()
    def csv_file(self, tmp_path):
        """Create a minimal AUSNUT-format CSV file."""
        content = textwrap.dedent("""\
            Food Key,Food Name,Energy (kJ),Protein (g),Total fat (g),Carbohydrate (g),Total sugars (g),Dietary fibre (g),Sodium (mg)
            10001,Weet-Bix original,1490,11.7,1.4,67.3,3.3,10.5,270
            10002,Full cream milk,272,3.3,3.4,4.9,4.9,0.0,42
            10003,Banana raw,395,1.1,0.2,23.1,16.6,2.7,1
        """)
        csv_path = tmp_path / "ausnut_test.csv"
        csv_path.write_text(content, encoding="utf-8")
        return csv_path

    @pytest.fixture()
    def csv_file_missing_carbs(self, tmp_path):
        """CSV with a row missing carbs — should be skipped."""
        content = textwrap.dedent("""\
            Food Key,Food Name,Energy (kJ),Carbohydrate (g)
            10001,Good Food,1490,67.3
            10002,Bad Food,272,
            10003,Another Good,395,23.1
        """)
        csv_path = tmp_path / "ausnut_missing.csv"
        csv_path.write_text(content, encoding="utf-8")
        return csv_path

    def test_import_creates_staging_entries(self, csv_file, session, engine):
        """Import should create staging entries, not direct food entries."""
        # Seed the AUSNUT source
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("scripts.import_csv.engine", engine), patch(
            "scripts.import_csv.init_db"
        ):
            summary = import_csv(csv_file, "AUSNUT 2011-13")

        assert summary["created"] == 3
        assert summary["skipped"] == 0
        assert summary["errors"] == 0

        # Verify staging entries created
        entries = session.exec(select(Staging)).all()
        assert len(entries) == 3

        # Verify mapped_data is pre-populated
        for entry in entries:
            assert entry.status == "pending"
            assert entry.source_id == source.id
            mapped = json.loads(entry.mapped_data)
            assert "name" in mapped
            assert "carbs_per_100g" in mapped
            # Values should NOT be double-converted
            assert isinstance(mapped["carbs_per_100g"], float)

    def test_import_skips_rows_without_carbs(self, csv_file_missing_carbs, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("scripts.import_csv.engine", engine), patch(
            "scripts.import_csv.init_db"
        ):
            summary = import_csv(csv_file_missing_carbs, "AUSNUT 2011-13")

        assert summary["created"] == 2
        assert summary["skipped"] == 1

    def test_import_dry_run(self, csv_file, session, engine):
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("scripts.import_csv.engine", engine), patch(
            "scripts.import_csv.init_db"
        ):
            summary = import_csv(csv_file, "AUSNUT 2011-13", dry_run=True)

        assert summary["created"] == 3
        assert summary["dry_run"] is True

        # No staging entries should be created in dry run
        entries = session.exec(select(Staging)).all()
        assert len(entries) == 0

    def test_import_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            import_csv(Path("/nonexistent/file.csv"))

    def test_import_unknown_source(self, csv_file, session, engine):
        with patch("scripts.import_csv.engine", engine), patch(
            "scripts.import_csv.init_db"
        ):
            with pytest.raises(SystemExit, match="not found"):
                import_csv(csv_file, "Nonexistent Source")

    def test_import_preserves_raw_data(self, csv_file, session, engine):
        """raw_data should contain the original CSV row data as JSON."""
        source = Source(name="AUSNUT 2011-13", tier=1)
        session.add(source)
        session.commit()

        with patch("scripts.import_csv.engine", engine), patch(
            "scripts.import_csv.init_db"
        ):
            import_csv(csv_file, "AUSNUT 2011-13")

        entry = session.exec(select(Staging)).first()
        raw = json.loads(entry.raw_data)
        assert "Food Name" in raw
        assert "Energy (kJ)" in raw
