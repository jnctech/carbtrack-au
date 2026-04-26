"""Foods router — CRUD, search, barcode lookup, soft delete."""

import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field as PydField
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_session
from app.models import Food, Source, Staging, _utcnow
from app.services.off_client import (
    OFF_SOURCE_NAME,
    fetch_off_product,
    map_off_to_carbtrack,
    submit_off_product,
)

_BARCODE_RE = re.compile(r"^\d{8,14}$")

router = APIRouter(prefix="/foods", tags=["foods"])


class FoodCreate(BaseModel):
    barcode: Optional[str] = None
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    source_id: Optional[int] = None
    source_confidence: float = 1.0
    carbs_per_100g: float
    sugars_per_100g: Optional[float] = None
    fibre_per_100g: Optional[float] = None
    energy_kj: Optional[float] = None
    protein_per_100g: Optional[float] = None
    fat_per_100g: Optional[float] = None
    sodium_mg: Optional[float] = None
    gi_rating: Optional[str] = None
    serving_size_g: Optional[float] = None


class FoodUpdate(BaseModel):
    barcode: Optional[str] = None
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    source_id: Optional[int] = None
    source_confidence: Optional[float] = None
    carbs_per_100g: Optional[float] = None
    sugars_per_100g: Optional[float] = None
    fibre_per_100g: Optional[float] = None
    energy_kj: Optional[float] = None
    protein_per_100g: Optional[float] = None
    fat_per_100g: Optional[float] = None
    sodium_mg: Optional[float] = None
    gi_rating: Optional[str] = None
    serving_size_g: Optional[float] = None
    conflict_flag: Optional[bool] = None
    verified_at: Optional[datetime] = None


@router.get("")
def list_foods(
    q: Optional[str] = None,
    category: Optional[str] = None,
    barcode: Optional[str] = None,
    include_inactive: bool = False,
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    statement = select(Food)

    if not include_inactive:
        statement = statement.where(Food.active == True)  # noqa: E712

    if q:
        statement = statement.where(Food.name.contains(q))  # type: ignore[union-attr]

    if category:
        statement = statement.where(Food.category == category)

    if barcode:
        statement = statement.where(Food.barcode == barcode)

    statement = statement.offset(offset).limit(limit)
    return session.exec(statement).all()


def _stage_off_lookup(
    barcode: str, off_payload: dict, mapped: dict, session: Session
) -> Optional[int]:
    """Persist a pending staging row for the OFF lookup so it enters the
    standard review/conflict pipeline. Returns the staging id, or None if
    the OFF source isn't configured (fallback still serves the response).
    """
    source = session.exec(
        select(Source).where(Source.name == OFF_SOURCE_NAME)
    ).first()
    if source is None or source.id is None:
        return None

    existing = session.exec(
        select(Staging).where(
            Staging.source_id == source.id, Staging.status == "pending"
        )
    ).all()
    for row in existing:
        if row.mapped_data and f'"barcode": "{barcode}"' in row.mapped_data:
            return row.id

    staging = Staging(
        source_id=source.id,
        raw_data=json.dumps(off_payload),
        mapped_data=json.dumps(mapped),
        status="pending",
    )
    session.add(staging)
    session.commit()
    session.refresh(staging)
    return staging.id


def _provisional_response(mapped: dict, staging_id: Optional[int]) -> dict:
    """Shape the OFF-derived response to match the Food schema the scanner
    UI consumes, plus a `provisional` marker so callers can distinguish it
    from a verified Food row."""
    return {
        "id": None,
        "barcode": mapped["barcode"],
        "name": mapped["name"],
        "brand": mapped.get("brand"),
        "category": mapped.get("category"),
        "source_id": None,
        "source_confidence": 0.7,
        "carbs_per_100g": mapped["carbs_per_100g"],
        "sugars_per_100g": mapped.get("sugars_per_100g"),
        "fibre_per_100g": mapped.get("fibre_per_100g"),
        "energy_kj": mapped.get("energy_kj"),
        "protein_per_100g": mapped.get("protein_per_100g"),
        "fat_per_100g": mapped.get("fat_per_100g"),
        "sodium_mg": mapped.get("sodium_mg"),
        "gi_rating": mapped.get("gi_rating"),
        "serving_size_g": mapped.get("serving_size_g"),
        "conflict_flag": False,
        "active": True,
        "provisional": True,
        "source": OFF_SOURCE_NAME,
        "staging_id": staging_id,
    }


@router.get("/barcode/{barcode}")
def get_food_by_barcode(barcode: str, session: Session = Depends(get_session)):
    food = session.exec(
        select(Food).where(Food.barcode == barcode, Food.active == True)  # noqa: E712
    ).first()
    if food:
        return food

    off_payload = fetch_off_product(barcode)
    if off_payload is None:
        raise HTTPException(status_code=404, detail="Food not found")

    mapped = map_off_to_carbtrack(off_payload, barcode)
    if mapped is None:
        raise HTTPException(status_code=404, detail="Food not found")

    staging_id = _stage_off_lookup(barcode, off_payload, mapped, session)
    return _provisional_response(mapped, staging_id)


class FoodContribute(BaseModel):
    barcode: str = PydField(min_length=8, max_length=14)
    name: str = PydField(min_length=1, max_length=200)
    brand: Optional[str] = PydField(default=None, max_length=120)
    category: Optional[str] = PydField(default=None, max_length=120)
    carbs_per_100g: float = PydField(ge=0, le=100)
    sugars_per_100g: Optional[float] = PydField(default=None, ge=0, le=100)
    fibre_per_100g: Optional[float] = PydField(default=None, ge=0, le=100)
    energy_kj: Optional[float] = PydField(default=None, ge=0, le=4000)
    protein_per_100g: Optional[float] = PydField(default=None, ge=0, le=100)
    fat_per_100g: Optional[float] = PydField(default=None, ge=0, le=100)
    sodium_mg: Optional[float] = PydField(default=None, ge=0, le=10000)
    serving_size_g: Optional[float] = PydField(default=None, gt=0, le=2000)


@router.post("/contribute", status_code=202)
def contribute_food(
    payload: FoodContribute, session: Session = Depends(get_session)
):
    """Submit a user-supplied product. Always creates a pending staging row;
    best-effort writes back to Open Food Facts when OFF_CONTRIBUTE_ENABLED.

    Never auto-promotes to the foods table — admin review via the staging
    pipeline is mandatory. The local barcode endpoint will keep falling
    back to OFF for this barcode until either OFF accepts the contribution
    or admin approves the staging row.
    """
    if not _BARCODE_RE.fullmatch(payload.barcode):
        raise HTTPException(status_code=422, detail="Barcode must be 8–14 digits")

    mapped = payload.model_dump()
    raw = {"source": "user_contribution", "submitted": mapped}

    source = session.exec(
        select(Source).where(Source.name == OFF_SOURCE_NAME)
    ).first()
    if source is None or source.id is None:
        raise HTTPException(
            status_code=503,
            detail="Open Food Facts source not seeded — cannot accept contributions",
        )

    staging = Staging(
        source_id=source.id,
        raw_data=json.dumps(raw),
        mapped_data=json.dumps(mapped),
        status="pending",
    )
    session.add(staging)
    session.commit()
    session.refresh(staging)

    off_ok, off_reason = submit_off_product(payload.barcode, mapped)

    return {
        "staging_id": staging.id,
        "status": "pending_review",
        "off_submitted": off_ok,
        "off_reason": off_reason,
    }


@router.get("/{food_id}")
def get_food(
    food_id: int,
    include_inactive: bool = False,
    session: Session = Depends(get_session),
):
    food = session.get(Food, food_id)
    if not food or (not include_inactive and not food.active):
        raise HTTPException(status_code=404, detail="Food not found")
    return food


@router.post("", status_code=201)
def create_food(food_in: FoodCreate, session: Session = Depends(get_session)):
    food = Food.model_validate(food_in)
    session.add(food)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A food with this barcode already exists",
        )
    session.refresh(food)
    return food


@router.patch("/{food_id}")
def update_food(
    food_id: int,
    food_in: FoodUpdate,
    session: Session = Depends(get_session),
):
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")

    update_data = food_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(food, key, value)

    food.updated_at = _utcnow()
    session.add(food)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="A food with this barcode already exists",
        )
    session.refresh(food)
    return food


@router.delete("/{food_id}")
def delete_food(food_id: int, session: Session = Depends(get_session)):
    """Soft delete only — sets active=false, never SQL DELETE."""
    food = session.get(Food, food_id)
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")

    food.active = False
    food.updated_at = _utcnow()
    session.add(food)
    session.commit()
    return {"detail": "Food deactivated", "id": food_id}
