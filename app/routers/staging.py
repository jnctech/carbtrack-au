"""Staging router — submit, list, approve (with conflict detection), reject."""

import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_session
from app.models import Food, FoodSourceRef, Staging, _utcnow

router = APIRouter(prefix="/staging", tags=["staging"])


class StagingSubmit(BaseModel):
    source_id: int
    raw_data: str  # JSON string from source API

    @field_validator("raw_data")
    @classmethod
    def raw_data_must_be_valid_json(cls, v: str) -> str:
        if len(v) > 1_000_000:
            raise ValueError("raw_data exceeds 1 MB maximum size")
        try:
            json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"raw_data must be valid JSON: {exc}") from exc
        return v


class StagingReject(BaseModel):
    note: Optional[str] = None


class StagingResponse(BaseModel):
    """Response model — excludes raw_data from API responses."""

    model_config = {"from_attributes": True}

    id: int
    source_id: int
    mapped_data: Optional[str] = None
    status: str
    conflict_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime


@router.get("", response_model=list[StagingResponse])
def list_staging(
    status: Optional[Literal["pending", "approved", "rejected", "conflict"]] = Query(
        default="pending"
    ),
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


@router.post("", status_code=201, response_model=StagingResponse)
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


@router.post("/{staging_id}/approve", response_model=StagingResponse)
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

    try:
        mapped = json.loads(staging.mapped_data)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"mapped_data is not valid JSON: {exc}",
        ) from exc

    mapped_carbs = mapped.get("carbs_per_100g")
    if mapped_carbs is None:
        raise HTTPException(
            status_code=400,
            detail="mapped_data missing required field: carbs_per_100g",
        )
    if not isinstance(mapped_carbs, (int, float)) or isinstance(mapped_carbs, bool):
        raise HTTPException(
            status_code=400,
            detail=f"carbs_per_100g must be a number, got {type(mapped_carbs).__name__}",
        )
    if mapped_carbs < 0:
        raise HTTPException(
            status_code=400,
            detail=f"carbs_per_100g cannot be negative, got {mapped_carbs}",
        )

    for ref in _find_existing_refs(mapped, session):
        if ref.reported_carbs == 0:
            # Zero-carb ref indicates data quality issue — hold for review
            staging.status = "conflict"
            staging.conflict_notes = (
                f"Existing food_source_ref (id={ref.id}, source={ref.source_id}) "
                f"has reported_carbs=0. Manual review required before "
                f"promoting new value {mapped_carbs}g."
            )
            staging.reviewed_at = _utcnow()
            session.add(staging)
            session.commit()
            session.refresh(staging)
            return staging
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
    try:
        session.flush()  # populate food.id before inserting ref
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Food could not be created — a record with barcode "
            f"{mapped.get('barcode')!r} may already exist",
        )

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


@router.post("/{staging_id}/reject", response_model=StagingResponse)
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
