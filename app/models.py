"""SQLModel table definitions for CarbTrack AU.

Four tables per spec §3: Source, Food, FoodSourceRef, Staging.
All types are Postgres-compatible — no SQLite-specific types.
JSON-carrying fields use str (Text), not JSON type.
Timestamps set via Python default_factory, not server_default.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    tier: int = Field(nullable=False)
    url: Optional[str] = None
    api_base: Optional[str] = None
    api_notes: Optional[str] = None
    opus_reliability: Optional[str] = None
    opus_conflicts: Optional[str] = None
    last_queried_at: Optional[datetime] = None
    active: bool = Field(default=True)


class Food(SQLModel, table=True):
    __tablename__ = "foods"

    id: Optional[int] = Field(default=None, primary_key=True)
    barcode: Optional[str] = Field(default=None, unique=True)
    name: str = Field(nullable=False)
    brand: Optional[str] = None
    category: Optional[str] = None
    source_id: Optional[int] = Field(default=None, foreign_key="sources.id")
    source_confidence: float = Field(default=1.0)
    carbs_per_100g: float = Field(nullable=False)
    sugars_per_100g: Optional[float] = None
    fibre_per_100g: Optional[float] = None
    energy_kj: Optional[float] = None
    protein_per_100g: Optional[float] = None
    fat_per_100g: Optional[float] = None
    sodium_mg: Optional[float] = None
    gi_rating: Optional[str] = None
    serving_size_g: Optional[float] = None
    icon_key: Optional[str] = None
    conflict_flag: bool = Field(default=False)
    verified_at: Optional[datetime] = None
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class FoodSourceRef(SQLModel, table=True):
    __tablename__ = "food_source_refs"

    id: Optional[int] = Field(default=None, primary_key=True)
    food_id: int = Field(foreign_key="foods.id")
    source_id: int = Field(foreign_key="sources.id")
    reported_carbs: float = Field(nullable=False)
    queried_at: datetime = Field(nullable=False)
    raw_response_json: Optional[str] = None  # Text — never log, never return in API
    query_used: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"FoodSourceRef(id={self.id}, food_id={self.food_id}, "
            f"source_id={self.source_id}, reported_carbs={self.reported_carbs})"
        )


class Staging(SQLModel, table=True):
    __tablename__ = "staging"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id")
    raw_data: str = Field(nullable=False)  # Text — original JSON from source API
    mapped_data: Optional[str] = None  # Text — JSON matching foods schema after mapping
    status: str = Field(default="pending")
    conflict_notes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)

    def __repr__(self) -> str:
        return (
            f"Staging(id={self.id}, source_id={self.source_id}, "
            f"status={self.status})"
        )


class Recipe(SQLModel, table=True):
    __tablename__ = "recipes"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    servings: int = Field(default=1, nullable=False)
    notes: Optional[str] = None
    pinned: bool = Field(default=False)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RecipeIngredient(SQLModel, table=True):
    __tablename__ = "recipe_ingredients"

    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipes.id", nullable=False)
    food_id: int = Field(foreign_key="foods.id", nullable=False)
    quantity_g: float = Field(nullable=False)
    sort_order: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow)


class RecipeAttachment(SQLModel, table=True):
    __tablename__ = "recipe_attachments"

    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(foreign_key="recipes.id", nullable=False)
    kind: str = Field(nullable=False)
    filename: str = Field(nullable=False)
    mime_type: str = Field(nullable=False)
    caption: Optional[str] = None
    sort_order: int = Field(default=0, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow)
