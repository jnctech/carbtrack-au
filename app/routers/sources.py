"""Sources router — read-only access to the source registry + query template generation."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.ai_helpers import call_sonnet, parse_ai_json
from app.database import get_session
from app.models import Source

router = APIRouter(prefix="/sources", tags=["sources"])


class QueryTemplateRequest(BaseModel):
    food_name: Optional[str] = None
    barcode: Optional[str] = None


@router.get("")
def list_sources(session: Session = Depends(get_session)) -> list[Source]:
    sources = session.exec(select(Source)).all()
    return sources


@router.get("/{source_id}")
def get_source(source_id: int, session: Session = Depends(get_session)) -> Source:
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/{source_id}/query-template")
def generate_query_template(
    source_id: int,
    request: QueryTemplateRequest,
    session: Session = Depends(get_session),
) -> dict:
    """Sonnet generates a query template for this source + food name/barcode."""
    source = session.get(Source, source_id)
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

    search_term = request.barcode or request.food_name
    user_prompt = (
        f"Source: {source.name}\n"
        f"API base: {source.api_base}\n"
        f"API notes: {source.api_notes}\n"
        f"Search term: {search_term}\n"
        f"Generate a query template (curl command, URL, and notes) "
        f"to find nutrition data for this food from this source."
    )

    response_text = call_sonnet(
        system=(
            "You are a nutrition data API assistant for CarbTrack AU. "
            "Given a food source's API details and a search term, generate a "
            "ready-to-use query template. Return a JSON object with keys: "
            '"curl", "url", "notes". Never execute the query.'
        ),
        user_prompt=user_prompt,
        max_tokens=400,
    )

    result = parse_ai_json(response_text)

    return {
        "source_id": source_id,
        "source_name": source.name,
        "curl": result.get("curl", ""),
        "url": result.get("url", ""),
        "notes": result.get("notes", ""),
    }
