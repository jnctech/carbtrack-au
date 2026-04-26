"""Open Food Facts client + field mapper for runtime barcode fallback.

Used by GET /foods/barcode/{barcode} when the local DB has no match.
No AI in this path — mapping is mechanical (per CLAUDE.md hard constraint #3).
Network failures degrade gracefully to a 404; raw responses are never logged.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

OFF_SOURCE_NAME = "Open Food Facts AU"

DEFAULT_API_BASE = "https://world.openfoodfacts.org/api/v2"
DEFAULT_USER_AGENT = "CarbTrackAU/0.1 (https://github.com/jnctech/carbtrack-au)"
DEFAULT_TIMEOUT = 5.0


def _config() -> tuple[str, str, float, bool]:
    api_base = os.getenv("OFF_API_BASE", DEFAULT_API_BASE).rstrip("/")
    user_agent = os.getenv("OFF_USER_AGENT", DEFAULT_USER_AGENT)
    timeout = float(os.getenv("OFF_TIMEOUT", DEFAULT_TIMEOUT))
    enabled = os.getenv("OFF_FALLBACK_ENABLED", "true").lower() == "true"
    return api_base, user_agent, timeout, enabled


def fetch_off_product(barcode: str, *, client: httpx.Client | None = None) -> dict | None:
    """Fetch a product from Open Food Facts by barcode.

    Returns the parsed JSON dict on a 200 response, or None on any miss
    (404, network error, non-JSON body, status=0). Never raises — the caller
    treats a None as "no fallback available, return 404 to user".
    """
    api_base, user_agent, timeout, enabled = _config()
    if not enabled:
        return None

    url = f"{api_base}/product/{barcode}.json"
    headers = {"User-Agent": user_agent, "Accept": "application/json"}

    try:
        if client is None:
            with httpx.Client(timeout=timeout, headers=headers) as ctx_client:
                response = ctx_client.get(url)
        else:
            response = client.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        logger.warning("OFF lookup failed for barcode (network): %s", exc)
        return None

    if response.status_code != 200:
        logger.info("OFF lookup non-200 for barcode: status=%d", response.status_code)
        return None

    try:
        payload = response.json()
    except ValueError:
        logger.warning("OFF lookup returned non-JSON body")
        return None

    if payload.get("status") != 1:
        return None

    return payload


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _first_csv(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    head = value.split(",")[0].strip()
    return head or None


def _energy_kj(nutriments: dict) -> float | None:
    """Return energy_kj from OFF nutriments. Convert kcal → kJ if needed."""
    kj = _coerce_float(nutriments.get("energy-kj_100g"))
    if kj is not None:
        return kj
    kcal = _coerce_float(nutriments.get("energy-kcal_100g"))
    if kcal is not None:
        return round(kcal * 4.184, 2)
    return None


def _sodium_mg(nutriments: dict) -> float | None:
    """OFF reports sodium in g/100g — convert to mg/100g."""
    sodium_g = _coerce_float(nutriments.get("sodium_100g"))
    if sodium_g is None:
        return None
    return round(sodium_g * 1000.0, 2)


def _serving_size_g(product: dict) -> float | None:
    """Use serving_quantity only when serving_quantity_unit is grams."""
    unit = product.get("serving_quantity_unit")
    if isinstance(unit, str) and unit.strip().lower() not in ("", "g"):
        return None
    return _coerce_float(product.get("serving_quantity"))


def map_off_to_carbtrack(off_payload: dict, barcode: str) -> dict | None:
    """Map an OFF /product response to CarbTrack's foods schema.

    Returns a dict matching the Food schema (per 100g, energy_kj) or None
    when required fields are missing. Required: name, carbs_per_100g.
    """
    product = off_payload.get("product") or {}
    nutriments = product.get("nutriments") or {}

    name = product.get("product_name")
    if not isinstance(name, str) or not name.strip():
        return None

    carbs = _coerce_float(nutriments.get("carbohydrates_100g"))
    if carbs is None or carbs < 0:
        return None

    return {
        "barcode": barcode,
        "name": name.strip(),
        "brand": _first_csv(product.get("brands")),
        "category": _first_csv(product.get("categories")),
        "carbs_per_100g": carbs,
        "sugars_per_100g": _coerce_float(nutriments.get("sugars_100g")),
        "fibre_per_100g": _coerce_float(nutriments.get("fiber_100g")),
        "energy_kj": _energy_kj(nutriments),
        "protein_per_100g": _coerce_float(nutriments.get("proteins_100g")),
        "fat_per_100g": _coerce_float(nutriments.get("fat_100g")),
        "sodium_mg": _sodium_mg(nutriments),
        "gi_rating": None,
        "serving_size_g": _serving_size_g(product),
    }
