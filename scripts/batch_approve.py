"""Batch approve pending staging entries.

Reviews all pending staging entries that have mapped_data and approves them
through the standard conflict detection flow. Entries that trigger conflicts
are held (not promoted) — same as single approve.

Uses shared approve logic from app.services.approve — same code path as the
API endpoint to prevent drift.

Usage:
    python -m scripts.batch_approve [--dry-run] [--source-name "AUSNUT 2011-13"]
"""

import argparse
import json
import logging
import sys

from sqlmodel import Session, select

from app.database import engine, init_db
from app.models import Source, Staging
from app.services.approve import approve_staging_entry

logger = logging.getLogger(__name__)


def batch_approve(
    source_name: str | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Approve all pending staging entries with mapped_data.

    Optionally filter by source name.
    """
    init_db()

    results: dict[str, int] = {
        "approved": 0,
        "conflict": 0,
        "skipped_no_mapping": 0,
        "skipped_no_carbs": 0,
        "skipped_no_name": 0,
        "skipped_invalid_json": 0,
        "skipped_invalid_carbs": 0,
        "skipped_duplicate": 0,
        "errors": 0,
    }

    with Session(engine) as session:
        statement = select(Staging).where(Staging.status == "pending")

        if source_name:
            source = session.exec(
                select(Source).where(Source.name == source_name)
            ).first()
            if not source:
                raise SystemExit(f"Source '{source_name}' not found in database")
            statement = statement.where(Staging.source_id == source.id)

        pending = session.exec(statement).all()
        logger.info("Found %d pending staging entries", len(pending))

        for i, staging_entry in enumerate(pending):
            try:
                if dry_run:
                    if not staging_entry.mapped_data:
                        results["skipped_no_mapping"] += 1
                    else:
                        results["approved"] += 1
                    continue

                status = approve_staging_entry(staging_entry, session)
                results[status] = results.get(status, 0) + 1

                # Batch commit every 100 approvals
                if (i + 1) % 100 == 0:
                    session.commit()
                    logger.info("Progress: %d/%d processed...", i + 1, len(pending))

            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.warning(
                    "Data error approving staging id=%d: %s",
                    staging_entry.id, exc,
                )
                results["errors"] += 1

        if not dry_run:
            session.commit()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Batch approve pending staging entries"
    )
    parser.add_argument(
        "--source-name",
        help="Only approve entries from this source",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count entries without approving",
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

    results = batch_approve(args.source_name, dry_run=args.dry_run)

    print("\nBatch Approve Summary:")
    for status, count in sorted(results.items()):
        if count > 0:
            print(f"  {status}: {count}")
    if args.dry_run:
        print("  (DRY RUN — no data written)")

    sys.exit(1 if results["errors"] > 0 else 0)


if __name__ == "__main__":
    main()
