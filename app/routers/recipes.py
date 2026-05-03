"""Recipes router — CRUD, calculate, soft delete.

Endpoints:
    GET    /recipes                     list active recipes (summary view)
    POST   /recipes                     create recipe + ingredients in one txn
    POST   /recipes/calculate           stateless carb total for an ingredient list
    GET    /recipes/{id}                full recipe detail
    PUT    /recipes/{id}                replace recipe + full ingredient swap
    DELETE /recipes/{id}                soft delete (active=False)

PUT replaces the ingredient list wholesale (delete-all + insert) — simpler than
patch-by-id for the MVP and matches the tablet's "edit screen" UX.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.database import get_session
from app.models import Food, Recipe, RecipeAttachment, RecipeIngredient, _utcnow

router = APIRouter(prefix="/recipes", tags=["recipes"])


class IngredientIn(BaseModel):
    food_id: int
    quantity_g: float = Field(gt=0)
    sort_order: int = 0


class RecipeCreate(BaseModel):
    name: str
    servings: int = Field(default=1, ge=1)
    notes: Optional[str] = None
    pinned: bool = False
    ingredients: list[IngredientIn] = Field(default_factory=list)


class RecipeUpdate(BaseModel):
    name: str
    servings: int = Field(ge=1)
    notes: Optional[str] = None
    pinned: bool = False
    ingredients: list[IngredientIn] = Field(default_factory=list)


class CalculateItem(BaseModel):
    food_id: int
    quantity_g: float = Field(gt=0)


def _ingredient_view(ing: RecipeIngredient, food: Food) -> dict:
    carbs = round(food.carbs_per_100g * ing.quantity_g / 100.0, 2)
    return {
        "id": ing.id,
        "food_id": food.id,
        "name": food.name,
        "carbs_per_100g": food.carbs_per_100g,
        "quantity_g": ing.quantity_g,
        "carbs_g": carbs,
        "sort_order": ing.sort_order,
    }


def _attachment_view(att: RecipeAttachment) -> dict:
    return {
        "id": att.id,
        "kind": att.kind,
        "filename": att.filename,
        "mime_type": att.mime_type,
        "caption": att.caption,
        "sort_order": att.sort_order,
        "url": f"/attachments/{att.recipe_id}/{att.filename}",
        "thumb_url": f"/attachments/thumbs/{att.recipe_id}/{att.filename.rsplit('.', 1)[0]}.webp",
    }


def _load_foods(session: Session, food_ids: list[int]) -> dict[int, Food]:
    if not food_ids:
        return {}
    foods = session.exec(select(Food).where(Food.id.in_(food_ids))).all()  # type: ignore[union-attr]
    return {f.id: f for f in foods}


@router.get("")
def list_recipes(
    include_inactive: bool = False,
    session: Session = Depends(get_session),
):
    statement = select(Recipe)
    if not include_inactive:
        statement = statement.where(Recipe.active == True)  # noqa: E712
    statement = statement.order_by(Recipe.pinned.desc(), Recipe.updated_at.desc())  # type: ignore[union-attr]
    recipes = session.exec(statement).all()

    result = []
    for r in recipes:
        ing_count = len(
            session.exec(
                select(RecipeIngredient).where(RecipeIngredient.recipe_id == r.id)
            ).all()
        )
        first_att = session.exec(
            select(RecipeAttachment)
            .where(RecipeAttachment.recipe_id == r.id)
            .order_by(RecipeAttachment.sort_order, RecipeAttachment.id)  # type: ignore[arg-type]
        ).first()
        thumb_url = None
        if first_att:
            stem = first_att.filename.rsplit(".", 1)[0]
            thumb_url = f"/attachments/thumbs/{r.id}/{stem}.webp"
        result.append(
            {
                "id": r.id,
                "name": r.name,
                "servings": r.servings,
                "pinned": r.pinned,
                "active": r.active,
                "ingredient_count": ing_count,
                "thumb_url": thumb_url,
                "updated_at": r.updated_at,
            }
        )
    return result


@router.post("/calculate")
def calculate_carbs(
    items: list[CalculateItem],
    session: Session = Depends(get_session),
):
    """Stateless carb totaliser — no recipe row created, no ingredients persisted."""
    foods = _load_foods(session, [it.food_id for it in items])
    missing = [it.food_id for it in items if it.food_id not in foods]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Food(s) not found: {missing}",
        )

    detail = []
    total = 0.0
    for it in items:
        f = foods[it.food_id]
        carbs = round(f.carbs_per_100g * it.quantity_g / 100.0, 2)
        total += carbs
        detail.append(
            {
                "food_id": f.id,
                "name": f.name,
                "quantity_g": it.quantity_g,
                "carbs_g": carbs,
            }
        )
    return {"total_carbs_g": round(total, 2), "ingredients": detail}


@router.post("", status_code=201)
def create_recipe(payload: RecipeCreate, session: Session = Depends(get_session)):
    foods = _load_foods(session, [i.food_id for i in payload.ingredients])
    missing = [i.food_id for i in payload.ingredients if i.food_id not in foods]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Food(s) not found: {missing}",
        )

    recipe = Recipe(
        name=payload.name,
        servings=payload.servings,
        notes=payload.notes,
        pinned=payload.pinned,
    )
    session.add(recipe)
    session.flush()

    for ing in payload.ingredients:
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                food_id=ing.food_id,
                quantity_g=ing.quantity_g,
                sort_order=ing.sort_order,
            )
        )
    session.commit()
    session.refresh(recipe)
    return _full_recipe(session, recipe)


@router.get("/{recipe_id}")
def get_recipe(recipe_id: int, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe or not recipe.active:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _full_recipe(session, recipe)


@router.put("/{recipe_id}")
def update_recipe(
    recipe_id: int,
    payload: RecipeUpdate,
    session: Session = Depends(get_session),
):
    recipe = session.get(Recipe, recipe_id)
    if not recipe or not recipe.active:
        raise HTTPException(status_code=404, detail="Recipe not found")

    foods = _load_foods(session, [i.food_id for i in payload.ingredients])
    missing = [i.food_id for i in payload.ingredients if i.food_id not in foods]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Food(s) not found: {missing}",
        )

    recipe.name = payload.name
    recipe.servings = payload.servings
    recipe.notes = payload.notes
    recipe.pinned = payload.pinned
    recipe.updated_at = _utcnow()
    session.add(recipe)

    existing = session.exec(
        select(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id)
    ).all()
    for row in existing:
        session.delete(row)
    session.flush()

    for ing in payload.ingredients:
        session.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                food_id=ing.food_id,
                quantity_g=ing.quantity_g,
                sort_order=ing.sort_order,
            )
        )
    session.commit()
    session.refresh(recipe)
    return _full_recipe(session, recipe)


@router.delete("/{recipe_id}")
def delete_recipe(recipe_id: int, session: Session = Depends(get_session)):
    recipe = session.get(Recipe, recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe.active = False
    recipe.updated_at = _utcnow()
    session.add(recipe)
    session.commit()
    return {"detail": "Recipe deactivated", "id": recipe_id}


def _full_recipe(session: Session, recipe: Recipe) -> dict:
    ingredients = session.exec(
        select(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == recipe.id)
        .order_by(RecipeIngredient.sort_order, RecipeIngredient.id)  # type: ignore[arg-type]
    ).all()
    foods = _load_foods(session, [i.food_id for i in ingredients])
    ing_views = [_ingredient_view(i, foods[i.food_id]) for i in ingredients if i.food_id in foods]
    total = round(sum(v["carbs_g"] for v in ing_views), 2)

    attachments = session.exec(
        select(RecipeAttachment)
        .where(RecipeAttachment.recipe_id == recipe.id)
        .order_by(RecipeAttachment.sort_order, RecipeAttachment.id)  # type: ignore[arg-type]
    ).all()

    return {
        "id": recipe.id,
        "name": recipe.name,
        "servings": recipe.servings,
        "notes": recipe.notes,
        "pinned": recipe.pinned,
        "active": recipe.active,
        "created_at": recipe.created_at,
        "updated_at": recipe.updated_at,
        "ingredients": ing_views,
        "attachments": [_attachment_view(a) for a in attachments],
        "total_carbs_g": total,
    }
