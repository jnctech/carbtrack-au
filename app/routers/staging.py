"""Staging router — submit, list, approve (with conflict detection), reject, map."""

import json
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.ai_helpers import (
    MAP_FIELDS_SYSTEM_PROMPT,
    SCHEMA_FIELDS,
    call_sonnet,
    parse_ai_json,
)
from app.database import get_session
from app.models import Source, Staging, _utcnow
from app.services.approve import check_conflicts, promote_to_foods

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

    if not mapped.get("name"):
        raise HTTPException(
            status_code=400,
            detail="mapped_data missing required field: name",
        )

    conflict = check_conflicts(mapped, staging, session)
    if conflict:
        session.commit()
        session.refresh(staging)
        return staging

    result = promote_to_foods(mapped, staging, session)
    if result == "skipped_duplicate":
        raise HTTPException(
            status_code=409,
            detail=f"Food could not be created — a record with barcode "
            f"{mapped.get('barcode')!r} may already exist",
        )

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


@router.post("/{staging_id}/map", response_model=StagingResponse)
def map_staging(
    staging_id: int,
    session: Session = Depends(get_session),
):
    """Sonnet maps raw_data → mapped_data. Does NOT promote or trigger conflict detection.

    Map and approve are strictly separate actions.
    """
    staging = session.get(Staging, staging_id)
    if not staging:
        raise HTTPException(status_code=404, detail="Staging entry not found")

    if staging.status not in ("pending", "conflict"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot map entry with status '{staging.status}'",
        )

    try:
        raw_parsed = json.loads(staging.raw_data)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"raw_data is not valid JSON: {exc}",
        ) from exc

    source = session.get(Source, staging.source_id)
    if not source:
        raise HTTPException(
            status_code=400,
            detail=f"Staging entry references source_id={staging.source_id} which does not exist",
        )

    user_prompt = (
        f"Source: {source.name}\n"
        f"Raw data:\n{json.dumps(raw_parsed, indent=2)}\n\n"
        f"Map these fields to the CarbTrack schema: {SCHEMA_FIELDS}"
    )

    response_text = call_sonnet(
        system=MAP_FIELDS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=600,
    )

    mapped = parse_ai_json(
        response_text,
        error_detail="Failed to parse AI field mapping response as JSON",
    )

    staging.mapped_data = json.dumps(mapped)
    session.add(staging)
    session.commit()
    session.refresh(staging)
    return staging


class SetMappedData(BaseModel):
    mapped_data: str

    @field_validator("mapped_data")
    @classmethod
    def mapped_data_must_be_valid_json(cls, v: str) -> str:
        if len(v) > 1_000_000:
            raise ValueError("mapped_data exceeds 1 MB maximum size")
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"mapped_data must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("mapped_data must be a JSON object")
        if not isinstance(parsed.get("name"), str) or not parsed["name"].strip():
            raise ValueError("mapped_data must contain a non-empty 'name' string")
        carbs = parsed.get("carbs_per_100g")
        if carbs is None:
            raise ValueError("mapped_data must contain 'carbs_per_100g'")
        if not isinstance(carbs, (int, float)) or isinstance(carbs, bool):
            raise ValueError("carbs_per_100g must be a number")
        if carbs < 0:
            raise ValueError("carbs_per_100g cannot be negative")
        return v


@router.post("/{staging_id}/set-mapped", response_model=StagingResponse)
def set_mapped_data(
    staging_id: int,
    body: SetMappedData,
    session: Session = Depends(get_session),
):
    """Set mapped_data directly — bypasses Sonnet for pre-mapped sources like OFF."""
    staging = session.get(Staging, staging_id)
    if not staging:
        raise HTTPException(status_code=404, detail="Staging entry not found")

    if staging.status not in ("pending", "conflict"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot set mapped data on entry with status '{staging.status}'",
        )

    staging.mapped_data = body.mapped_data
    session.add(staging)
    session.commit()
    session.refresh(staging)
    return staging


