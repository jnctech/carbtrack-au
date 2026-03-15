"""Shared helpers for Sonnet-assisted endpoints.

Centralises Anthropic client creation, model resolution, JSON response
parsing, and schema constants used across query-builder, sources, and
staging routers.
"""

import json
import os
import re

import anthropic
from fastapi import HTTPException

SCHEMA_FIELDS = (
    "name, brand, barcode, category, carbs_per_100g, sugars_per_100g, "
    "fibre_per_100g, energy_kj, protein_per_100g, fat_per_100g, "
    "sodium_mg, gi_rating, serving_size_g"
)

MAP_FIELDS_SYSTEM_PROMPT = (
    "You are a nutrition data field mapper for CarbTrack AU, "
    "an Australian food carbohydrate database for Type 1 Diabetes management. "
    "Given raw JSON from a food data source, map the fields to the CarbTrack schema. "
    "Return a JSON object with exactly these fields (use null for missing values): "
    f"{SCHEMA_FIELDS}. "
    "Rules: "
    "- All nutrition values must be per 100g (do NOT divide by serving then multiply by 100 "
    "if the source already reports per 100g). "
    "- energy_kj must be in kilojoules. If the source reports calories (kcal), "
    "multiply by 4.184 to convert. "
    "- gi_rating must be one of: 'low', 'medium', 'high', or null. "
    "- barcode should be EAN-13 format if available. "
    "- Return ONLY the JSON object, no markdown, no explanation."
)

_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def get_anthropic_client() -> anthropic.Anthropic:
    """Create Anthropic client. Raises 503 if API key not configured."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not configured",
        )
    return anthropic.Anthropic(api_key=api_key)


def get_sonnet_model() -> str:
    """Read model from SONNET_MODEL env var — never hardcode."""
    model = os.getenv("SONNET_MODEL")
    if not model:
        raise HTTPException(
            status_code=503,
            detail="SONNET_MODEL env var not configured",
        )
    return model


def parse_ai_json(response_text: str, error_detail: str = "Failed to parse AI response as JSON") -> dict:
    """Parse JSON from an AI response, handling markdown code block wrapping.

    Tries direct JSON parsing first, then falls back to extracting JSON
    from ```json ... ``` code blocks. Raises HTTP 502 if both fail.
    """
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    match = _CODE_BLOCK_RE.search(response_text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    raise HTTPException(status_code=502, detail=error_detail)
