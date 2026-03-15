"""Shared helpers for Sonnet-assisted endpoints.

Centralises Anthropic client creation, model resolution, API call execution,
JSON response parsing, and schema constants used across query-builder, sources,
and staging routers.
"""

import json
import logging
import os
import re

import anthropic
from fastapi import HTTPException

logger = logging.getLogger(__name__)

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


def call_sonnet(
    *,
    system: str,
    user_prompt: str,
    max_tokens: int,
) -> str:
    """Call Sonnet and return the text response.

    Handles Anthropic SDK exceptions, empty responses, and non-text blocks.
    Raises appropriate HTTP errors for each failure mode.
    """
    client = get_anthropic_client()
    model = get_sonnet_model()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.AuthenticationError:
        logger.error("Anthropic API authentication failed — API key may be invalid or revoked")
        raise HTTPException(
            status_code=503,
            detail="AI service authentication failed. Check ANTHROPIC_API_KEY configuration.",
        )
    except anthropic.RateLimitError:
        logger.warning("Anthropic API rate limit hit")
        raise HTTPException(
            status_code=429,
            detail="AI service rate limit reached. Please retry after a short delay.",
        )
    except anthropic.APIConnectionError as exc:
        logger.error("Anthropic API connection failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="Unable to reach AI service. Please try again later.",
        )
    except anthropic.APIStatusError as exc:
        logger.error("Anthropic API returned status %d: %s", exc.status_code, exc.message)
        raise HTTPException(
            status_code=502,
            detail=f"AI service error (status {exc.status_code}). Please try again later.",
        )

    if not response.content or not hasattr(response.content[0], "text"):
        logger.error(
            "Anthropic returned unexpected response structure: stop_reason=%s, content_length=%d",
            response.stop_reason,
            len(response.content) if response.content else 0,
        )
        raise HTTPException(
            status_code=502,
            detail="AI service returned an unexpected response format. Please retry.",
        )

    return response.content[0].text


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

    logger.error("AI response not parseable as JSON. First 500 chars: %s", response_text[:500])
    raise HTTPException(status_code=502, detail=error_detail)
