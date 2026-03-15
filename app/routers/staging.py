"""Staging router — submit, list, approve (with conflict detection), reject."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models import Food, FoodSourceRef, Staging, _utcnow

router = APIRouter(prefix="/staging", tags=["staging"])


class StagingSubmit(BaseModel):
    source_id: int
    raw_data: str  # JSON string from source API


class StagingReject(BaseModel):
    note: Optional[str] = None


@router.get("")
def list_staging(
    status: Optional[str] = Query(default="pending"),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    """List staging entries filtered by status (default: pending)."""
    statement = select(Staging)
    if status:
        statement = statement.where(Staging.status == status)
    statement = statement.offset(offset).limit(limit)
    return session.exec(statement).all()


@router.post("", status_code=201)
def submit_staging(
    entry: StagingSubmit,
    session: Session = Depends(get_session),
):
    """Submit raw API response to staging for review."""
    staging = Staging(source_id=entry.source_id, raw_data=entry.raw_data)
    session.add(staging)
    session.commit()
    session.refresh(staging)
    return staging


@router.post("/{staging_id}/approve")
def approve_staging(
    staging_id: int,
    session: Session = Depends(get_session),
):
    """Approve staging entry — runs conflict detection, promotes or holds.

    No AI in this path — conflict detection is pure arithmetic.
    A food_source_refs row is inserted on every successful promotion.
    """
    staging = session.get(Staging, staging_id)
    if not staging:
        raise HTTPException(status_code=404, detail="Staging entry not found")

    if staging.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve entry with status '{staging.status}'",
        )

    if not staging.mapped_data:
        raise HTTPException(
            status_code=400,
            detail="Cannot approve without mapped_data — map fields first",
        )

    mapped = json.loads(staging.mapped_data)
    mapped_carbs = mapped.get("carbs_per_100g")
    if mapped_carbs is None:
        raise HTTPException(
            status_code=400,
            detail="mapped_data missing required field: carbs_per_100g",
        )

    for ref in _find_existing_refs(mapped, session):
        if ref.reported_carbs == 0:
            continue
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
            session.commit()
            session.refresh(staging)
            return staging

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
    session.flush()  # populate food.id before inserting ref

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
    session.commit()
    session.refresh(staging)
    return staging


@router.post("/{staging_id}/reject")
def reject_staging(
    staging_id: int,
    body: Optional[StagingReject] = None,
    session: Session = Depends(get_session),
):
    """Mark staging entry as rejected with optional note."""
    staging = session.get(Staging, staging_id)
    if not staging:
        raise HTTPException(status_code=404, detail="Staging entry not found")

    if staging.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject entry with status '{staging.status}'",
        )

    staging.status = "rejected"
    staging.reviewed_at = _utcnow()
    if body and body.note:
        staging.conflict_notes = body.note
    session.add(staging)
    session.commit()
    session.refresh(staging)
    return staging


def _find_existing_refs(mapped: dict, session: Session) -> list[FoodSourceRef]:
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
