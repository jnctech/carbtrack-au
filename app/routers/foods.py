"""Foods router — CRUD, search, barcode lookup, soft delete."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import get_session
from app.models import Food, _utcnow

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


@router.get("/barcode/{barcode}")
def get_food_by_barcode(barcode: str, session: Session = Depends(get_session)):
    food = session.exec(
        select(Food).where(Food.barcode == barcode, Food.active == True)  # noqa: E712
    ).first()
    if not food:
        raise HTTPException(status_code=404, detail="Food not found")
    return food


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
