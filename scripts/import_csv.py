"""AUSNUT 2011-13 CSV bulk import to staging table.

Parses FSANZ AUSNUT CSV format and writes entries to the staging table
with source_id for the AUSNUT source. Foods go through staging → approve
flow, never direct to foods.

IMPORTANT: AUSNUT reports values per 100g natively. Do NOT divide by
serving size then multiply by 100 — this double-converts and corrupts values.

Usage:
    python -m scripts.import_csv path/to/ausnut.csv [--source-name "AUSNUT 2011-13"]
"""

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from sqlmodel import Session, select

from app.database import engine, init_db
from app.models import Source, Staging

logger = logging.getLogger(__name__)

# AUSNUT 2011-13 CSV column mapping.
# The CSV uses these headers (case-insensitive matching):
COLUMN_MAP = {
    "food key": "food_key",
    "food name": "name",
    "energy, with dietary fibre (kj)": "energy_kj",
    "energy with dietary fibre (kj)": "energy_kj",
    "energy (kj)": "energy_kj",
    "protein (g)": "protein_per_100g",
    "total fat (g)": "fat_per_100g",
    "fat, total (g)": "fat_per_100g",
    "fat total (g)": "fat_per_100g",
    "available carbohydrate, with sugar alcohols (g)": "carbs_per_100g",
    "available carbohydrate with sugar alcohols (g)": "carbs_per_100g",
    "available carbohydrates (g)": "carbs_per_100g",
    "carbohydrate (g)": "carbs_per_100g",
    "total sugars (g)": "sugars_per_100g",
    "sugars, total (g)": "sugars_per_100g",
    "sugars total (g)": "sugars_per_100g",
    "dietary fibre (g)": "fibre_per_100g",
    "sodium (mg)": "sodium_mg",
}


def find_ausnut_source(session: Session, source_name: str) -> Source:
    """Look up the AUSNUT source by name."""
    source = session.exec(
        select(Source).where(Source.name == source_name)
    ).first()
    if not source:
        available = [s.name for s in session.exec(select(Source)).all()]
        raise SystemExit(
            f"Source '{source_name}' not found in database. "
            f"Available sources: {available}"
        )
    return source


def map_headers(raw_headers: list[str]) -> dict[int, str]:
    """Map CSV column indices to our schema field names.

    Returns a dict of {column_index: schema_field_name}.
    Uses case-insensitive matching against COLUMN_MAP.
    """
    mapping = {}
    for idx, header in enumerate(raw_headers):
        normalised = header.strip().lower()
        if normalised in COLUMN_MAP:
            mapping[idx] = COLUMN_MAP[normalised]
    return mapping


def parse_float(value: str) -> float | None:
    """Parse a numeric string, returning None for empty/unparseable values."""
    value = value.strip()
    if not value or value in ("-", "N/A", "n/a", "NA", "Tr", "tr"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_row(row: list[str], header_map: dict[int, str]) -> dict | None:
    """Parse a single CSV row into a mapped_data dict.

    Returns None if the row lacks required fields (name, carbs_per_100g).
    Values are already per 100g — no conversion needed.
    """
    data = {}
    for idx, field_name in header_map.items():
        if idx < len(row):
            data[field_name] = row[idx].strip()

    name = data.get("name")
    if not name:
        return None

    # Build mapped_data matching the foods schema
    mapped = {"name": name}

    carbs = parse_float(data.get("carbs_per_100g", ""))
    if carbs is None:
        return None  # carbs_per_100g is required
    mapped["carbs_per_100g"] = carbs

    # Optional nutrition fields — already per 100g
    optional_fields = (
        "energy_kj", "protein_per_100g", "fat_per_100g",
        "sugars_per_100g", "fibre_per_100g", "sodium_mg",
    )
    for field in optional_fields:
        val = parse_float(data.get(field, ""))
        if val is not None:
            mapped[field] = val

    # AUSNUT foods are generic (no barcode, no brand)
    mapped["category"] = _infer_category(name)

    return mapped


def _infer_category(food_name: str) -> str | None:
    """Simple category inference from AUSNUT food names.

    AUSNUT names often include category hints like 'milk', 'bread', etc.
    Returns None if no category can be inferred.
    """
    name_lower = food_name.lower()
    categories = {
        "Breakfast Cereals": ["cereal", "muesli", "porridge", "oat", "weet-bix"],
        "Bread & Bakery": ["bread", "roll", "muffin", "crumpet", "toast"],
        "Dairy": ["milk", "cheese", "yoghurt", "yogurt", "cream", "butter"],
        "Fruit": ["apple", "banana", "orange", "grape", "berry", "mango", "pear"],
        "Vegetables": ["potato", "carrot", "broccoli", "spinach", "pumpkin", "pea"],
        "Meat & Poultry": ["beef", "chicken", "lamb", "pork", "turkey", "sausage"],
        "Seafood": ["fish", "prawn", "salmon", "tuna", "crab"],
        "Snacks": ["chip", "crisp", "biscuit", "cracker", "popcorn"],
        "Confectionery": ["chocolate", "lolly", "candy", "sugar", "sweet"],
        "Beverages": ["juice", "cordial", "soft drink", "cola", "water", "tea", "coffee"],
        "Rice & Pasta": ["rice", "pasta", "noodle", "spaghetti"],
        "Legumes & Nuts": ["bean", "lentil", "chickpea", "nut", "almond", "peanut"],
    }
    for category, keywords in categories.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return None


def import_csv(
    csv_path: Path,
    source_name: str = "AUSNUT 2011-13",
    *,
    dry_run: bool = False,
) -> dict:
    """Import AUSNUT CSV to staging table.

    Returns summary dict with counts.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    init_db()

    with Session(engine) as session:
        source = find_ausnut_source(session, source_name)
        logger.info("Using source: %s (id=%d, tier=%d)", source.name, source.id, source.tier)

        # Read CSV — try utf-8 first, fall back to latin-1
        try:
            text = csv_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = csv_path.read_text(encoding="latin-1")

        reader = csv.reader(text.splitlines())
        raw_headers = next(reader)
        header_map = map_headers(raw_headers)

        if not header_map:
            raise SystemExit(
                f"No recognised AUSNUT columns found in CSV headers: {raw_headers}"
            )

        # Check required fields are mapped
        mapped_fields = set(header_map.values())
        if "name" not in mapped_fields:
            raise SystemExit("CSV missing required column: food name")
        if "carbs_per_100g" not in mapped_fields:
            raise SystemExit(
                "CSV missing required column for carbohydrates. "
                "Expected one of: 'Available carbohydrate, with sugar alcohols (g)', "
                "'Carbohydrate (g)', or similar."
            )

        logger.info(
            "Mapped %d columns: %s",
            len(header_map),
            set(header_map.values()),
        )

        created = 0
        skipped = 0
        errors = 0

        for row_num, row in enumerate(reader, start=2):
            try:
                mapped = parse_row(row, header_map)
                if mapped is None:
                    skipped += 1
                    continue

                if dry_run:
                    created += 1
                    continue

                raw_dict = dict(zip(raw_headers, row))
                staging_entry = Staging(
                    source_id=source.id,
                    raw_data=json.dumps(raw_dict),
                    mapped_data=json.dumps(mapped),
                    status="pending",
                )
                session.add(staging_entry)
                created += 1

                # Batch commit every 500 rows
                if created % 500 == 0:
                    session.commit()
                    logger.info("Progress: %d rows imported...", created)

            except Exception:
                logger.exception("Error on row %d", row_num)
                errors += 1

        if not dry_run:
            session.commit()

    summary = {
        "file": str(csv_path),
        "source": source_name,
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
    }
    logger.info("Import complete: %s", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Import AUSNUT 2011-13 CSV to CarbTrack staging table"
    )
    parser.add_argument("csv_path", type=Path, help="Path to AUSNUT CSV file")
    parser.add_argument(
        "--source-name",
        default="AUSNUT 2011-13",
        help="Source name in database (default: 'AUSNUT 2011-13')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse CSV without writing to database",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    summary = import_csv(args.csv_path, args.source_name, dry_run=args.dry_run)

    print("\nImport Summary:")
    print(f"  File:    {summary['file']}")
    print(f"  Source:  {summary['source']}")
    print(f"  Created: {summary['created']}")
    print(f"  Skipped: {summary['skipped']}")
    print(f"  Errors:  {summary['errors']}")
    if summary["dry_run"]:
        print("  (DRY RUN — no data written)")

    sys.exit(1 if summary["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
