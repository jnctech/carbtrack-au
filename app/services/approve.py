"""Shared staging approval logic — used by both the API router and batch scripts.

No AI in this path — conflict detection is pure arithmetic.
"""

import json

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import Food, FoodSourceRef, Staging, _utcnow


def find_existing_refs(mapped: dict, session: Session) -> list[FoodSourceRef]:
    """Find existing food_source_refs matching by barcode or name+brand."""
    refs: list[FoodSourceRef] = []

    barcode = mapped.get("barcode")
    name = mapped.get("name")
    brand = mapped.get("brand")

    barcode_matched = False
    if barcode:
        food = session.exec(
            select(Food).where(Food.barcode == barcode, Food.active == True)  # noqa: E712
        ).first()
        if food:
            barcode_matched = True
            refs.extend(
                session.exec(
                    select(FoodSourceRef).where(FoodSourceRef.food_id == food.id)
                ).all()
            )

    if not barcode_matched and name:
        statement = select(Food).where(
            Food.name == name, Food.active == True  # noqa: E712
        )
        if brand:
            statement = statement.where(Food.brand == brand)
        for food in session.exec(statement).all():
            refs.extend(
                session.exec(
                    select(FoodSourceRef).where(FoodSourceRef.food_id == food.id)
                ).all()
            )

    return refs


def validate_mapped_data(mapped_data: str | None) -> tuple[dict | None, str | None]:
    """Validate mapped_data JSON and required fields.

    Returns (parsed_dict, error_reason). If error_reason is set, parsed_dict is None.
    """
    if not mapped_data:
        return None, "skipped_no_mapping"

    try:
        mapped = json.loads(mapped_data)
    except json.JSONDecodeError:
        return None, "skipped_invalid_json"

    mapped_carbs = mapped.get("carbs_per_100g")
    if mapped_carbs is None or not isinstance(mapped_carbs, (int, float)):
        return None, "skipped_no_carbs"
    if isinstance(mapped_carbs, bool) or mapped_carbs < 0:
        return None, "skipped_invalid_carbs"

    if not mapped.get("name"):
        return None, "skipped_no_name"

    return mapped, None


def check_conflicts(
    mapped: dict, staging: Staging, session: Session
) -> str | None:
    """Run conflict detection against existing food_source_refs.

    Returns None if no conflict, or sets staging status/notes and returns "conflict".
    """
    mapped_carbs = mapped["carbs_per_100g"]

    for ref in find_existing_refs(mapped, session):
        if ref.reported_carbs <= 0:
            staging.status = "conflict"
            staging.conflict_notes = (
                f"Existing food_source_ref (id={ref.id}, source={ref.source_id}) "
                f"has reported_carbs={ref.reported_carbs}. Manual review required "
                f"before promoting new value {mapped_carbs}g."
            )
            staging.reviewed_at = _utcnow()
            session.add(staging)
            return "conflict"

        variance = abs(ref.reported_carbs - mapped_carbs) / ref.reported_carbs
        if variance > 0.05:
            staging.status = "conflict"
            staging.conflict_notes = (
                f"Carb variance {variance:.1%} exceeds 5% threshold. "
                f"Existing: {ref.reported_carbs}g (source {ref.source_id}), "
                f"New: {mapped_carbs}g (source {staging.source_id})."
            )
            staging.reviewed_at = _utcnow()
            session.add(staging)
            return "conflict"

    return None


def promote_to_foods(
    mapped: dict, staging: Staging, session: Session
) -> str:
    """Promote mapped_data to foods table and create FoodSourceRef.

    Returns "approved" on success, "skipped_duplicate" on IntegrityError.
    """
    mapped_carbs = mapped["carbs_per_100g"]

    food = Food(
        barcode=mapped.get("barcode"),
        name=mapped["name"],
        brand=mapped.get("brand"),
        category=mapped.get("category"),
        source_id=staging.source_id,
        source_confidence=mapped.get("source_confidence", 1.0),
        carbs_per_100g=mapped_carbs,
        sugars_per_100g=mapped.get("sugars_per_100g"),
        fibre_per_100g=mapped.get("fibre_per_100g"),
        energy_kj=mapped.get("energy_kj"),
        protein_per_100g=mapped.get("protein_per_100g"),
        fat_per_100g=mapped.get("fat_per_100g"),
        sodium_mg=mapped.get("sodium_mg"),
        gi_rating=mapped.get("gi_rating"),
        serving_size_g=mapped.get("serving_size_g"),
    )
    session.add(food)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return "skipped_duplicate"

    session.add(
        FoodSourceRef(
            food_id=food.id,
            source_id=staging.source_id,
            reported_carbs=mapped_carbs,
            queried_at=_utcnow(),
            raw_response_json=staging.raw_data,
        )
    )

    staging.status = "approved"
    staging.reviewed_at = _utcnow()
    session.add(staging)
    return "approved"


def approve_staging_entry(staging: Staging, session: Session) -> str:
    """Full approve flow: validate, conflict check, promote.

    Returns status string: approved, conflict, skipped_*, or error reason.
    """
    mapped, error = validate_mapped_data(staging.mapped_data)
    if error:
        return error

    conflict = check_conflicts(mapped, staging, session)
    if conflict:
        return conflict

    return promote_to_foods(mapped, staging, session)
