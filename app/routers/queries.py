"""Query builder router — Sonnet-assisted query construction and field mapping.

No outbound HTTP calls from this module. The server generates templates
that the user executes externally. No httpx, requests, or urllib imports.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.ai_helpers import (
    MAP_FIELDS_SYSTEM_PROMPT,
    SCHEMA_FIELDS,
    get_anthropic_client,
    get_sonnet_model,
    parse_ai_json,
)
from app.database import get_session
from app.models import Source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/query-builder", tags=["query-builder"])

CONSTRUCT_SYSTEM_PROMPT = (
    "You are a nutrition data API assistant for CarbTrack AU, "
    "an Australian food carbohydrate database for Type 1 Diabetes management. "
    "Given a food source's API details and a food search term, generate a ready-to-use "
    "API query template. Return a JSON object with these keys: "
    '"curl" (a curl command), "fetch" (a JavaScript fetch snippet), '
    '"url" (the direct URL), "notes" (any caveats about the source or query). '
    "Only generate the query template — never execute the query. "
    "All nutrition values are per 100g. Energy is in kJ (kilojoules), not calories. "
    "Focus on Australian food sources and products."
)


class ConstructRequest(BaseModel):
    source_id: int
    food_name: Optional[str] = None
    barcode: Optional[str] = None
    query_type: str = "search"  # "search" or "barcode"


class ConstructResponse(BaseModel):
    curl: str
    fetch: str
    url: str
    notes: str


class MapFieldsRequest(BaseModel):
    source_id: int
    raw_json: str


@router.post("/construct", response_model=ConstructResponse)
def construct_query(
    request: ConstructRequest,
    session: Session = Depends(get_session),
) -> ConstructResponse:
    """Generate an API call template for a food source. Never executes the call."""
    source = session.get(Source, request.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if not source.api_base:
        raise HTTPException(
            status_code=400,
            detail=f"Source '{source.name}' has no API endpoint configured",
        )

    if not request.food_name and not request.barcode:
        raise HTTPException(
            status_code=400,
            detail="Provide either food_name or barcode",
        )

    search_term = request.barcode if request.query_type == "barcode" else request.food_name
    user_prompt = (
        f"Source: {source.name}\n"
        f"API base: {source.api_base}\n"
        f"API notes: {source.api_notes}\n"
        f"Search term: {search_term}\n"
        f"Query type: {request.query_type}\n"
        f"Generate a query template to find nutrition data for this food."
    )

    client = get_anthropic_client()
    model = get_sonnet_model()

    response = client.messages.create(
        model=model,
        max_tokens=400,
        system=CONSTRUCT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    result = parse_ai_json(response.content[0].text)

    return ConstructResponse(
        curl=result.get("curl", ""),
        fetch=result.get("fetch", ""),
        url=result.get("url", ""),
        notes=result.get("notes", ""),
    )


@router.get("/sources")
def list_query_sources(session: Session = Depends(get_session)) -> list[dict]:
    """List sources with known API endpoints and example queries."""
    sources = session.exec(
        select(Source).where(Source.api_base != None, Source.active == True)  # noqa: E711, E712
    ).all()

    return [
        {
            "id": s.id,
            "name": s.name,
            "tier": s.tier,
            "api_base": s.api_base,
            "api_notes": s.api_notes,
        }
        for s in sources
    ]


@router.post("/map-fields")
def map_fields(
    request: MapFieldsRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Map raw JSON from a source to the CarbTrack schema using Sonnet."""
    source = session.get(Source, request.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        raw_parsed = json.loads(request.raw_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"raw_json must be valid JSON: {exc}",
        ) from exc

    user_prompt = (
        f"Source: {source.name} (Tier {source.tier})\n"
        f"Raw data:\n{json.dumps(raw_parsed, indent=2)}\n\n"
        f"Map these fields to the CarbTrack schema: {SCHEMA_FIELDS}"
    )

    client = get_anthropic_client()
    model = get_sonnet_model()

    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=MAP_FIELDS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    mapped = parse_ai_json(
        response.content[0].text,
        error_detail="Failed to parse AI field mapping response as JSON",
    )

    return {"source_id": request.source_id, "mapped_data": mapped}
