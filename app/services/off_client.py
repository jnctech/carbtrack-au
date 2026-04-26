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
DEFAULT_WRITE_BASE = "https://world.openfoodfacts.org/cgi"
DEFAULT_USER_AGENT = "CarbTrackAU/0.1 (https://github.com/jnctech/carbtrack-au)"
DEFAULT_TIMEOUT = 5.0
DEFAULT_APP_NAME = "CarbTrackAU"
DEFAULT_APP_VERSION = "0.1"


def _config() -> tuple[str, str, float, bool]:
    api_base = os.getenv("OFF_API_BASE", DEFAULT_API_BASE).rstrip("/")
    user_agent = os.getenv("OFF_USER_AGENT", DEFAULT_USER_AGENT)
    timeout = float(os.getenv("OFF_TIMEOUT", DEFAULT_TIMEOUT))
    enabled = os.getenv("OFF_FALLBACK_ENABLED", "true").lower() == "true"
    return api_base, user_agent, timeout, enabled


def _write_config() -> dict:
    return {
        "write_base": os.getenv("OFF_WRITE_BASE", DEFAULT_WRITE_BASE).rstrip("/"),
        "user_agent": os.getenv("OFF_USER_AGENT", DEFAULT_USER_AGENT),
        "timeout": float(os.getenv("OFF_TIMEOUT", DEFAULT_TIMEOUT)),
        "enabled": os.getenv("OFF_CONTRIBUTE_ENABLED", "false").lower() == "true",
        "app_name": os.getenv("OFF_APP_NAME", DEFAULT_APP_NAME),
        "app_version": os.getenv("OFF_APP_VERSION", DEFAULT_APP_VERSION),
        "username": os.getenv("OFF_USERNAME"),
        "password": os.getenv("OFF_PASSWORD"),
    }


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


# --- Outbound contribution to OFF (community write-back) ---

# CarbTrack stores nutrition per 100g and energy in kJ. OFF accepts the same
# shape via nutriment_<key>_value + nutriment_<key>_unit. Map: CarbTrack key →
# (OFF nutriment key, OFF unit, scale factor applied to the CarbTrack value).
# Sodium scales 1/1000 because we store mg and OFF expects g.
_NUTRIMENT_MAP: tuple[tuple[str, str, str, float], ...] = (
    ("carbs_per_100g", "carbohydrates", "g", 1.0),
    ("sugars_per_100g", "sugars", "g", 1.0),
    ("fibre_per_100g", "fiber", "g", 1.0),
    ("protein_per_100g", "proteins", "g", 1.0),
    ("fat_per_100g", "fat", "g", 1.0),
    ("energy_kj", "energy-kj", "kJ", 1.0),
    ("sodium_mg", "sodium", "g", 0.001),
)


def _build_off_form(barcode: str, mapped: dict, cfg: dict) -> dict:
    """Build the application/x-www-form-urlencoded body for OFF's write API."""
    form = {
        "code": barcode,
        "app_name": cfg["app_name"],
        "app_version": cfg["app_version"],
        "lc": "en",
        "lang": "en",
    }
    if cfg["username"] and cfg["password"]:
        form["user_id"] = cfg["username"]
        form["password"] = cfg["password"]

    if mapped.get("name"):
        form["product_name"] = mapped["name"]
    if mapped.get("brand"):
        form["brands"] = mapped["brand"]
    if mapped.get("category"):
        form["categories"] = mapped["category"]
    if mapped.get("serving_size_g") is not None:
        form["serving_size"] = f"{mapped['serving_size_g']}g"

    for src_key, off_key, unit, scale in _NUTRIMENT_MAP:
        value = mapped.get(src_key)
        if value is None:
            continue
        form[f"nutriment_{off_key}"] = str(round(value * scale, 6))
        form[f"nutriment_{off_key}_unit"] = unit

    return form


def submit_off_product(
    barcode: str, mapped: dict, *, client: httpx.Client | None = None
) -> tuple[bool, str | None]:
    """Submit a product contribution to Open Food Facts.

    Returns (ok, reason_when_skipped). Best-effort: any failure (disabled,
    network, non-200, or OFF status != 1) returns False with a short reason
    so the caller can surface it but never block local staging.
    Credentials and request bodies are never logged in full.
    """
    cfg = _write_config()
    if not cfg["enabled"]:
        return False, "off_contribute_disabled"

    url = f"{cfg['write_base']}/product_jqm2.php"
    headers = {"User-Agent": cfg["user_agent"], "Accept": "application/json"}
    form = _build_off_form(barcode, mapped, cfg)

    try:
        if client is None:
            with httpx.Client(timeout=cfg["timeout"], headers=headers) as ctx_client:
                response = ctx_client.post(url, data=form)
        else:
            response = client.post(url, data=form, headers=headers, timeout=cfg["timeout"])
    except httpx.HTTPError as exc:
        logger.warning("OFF contribution network error: %s", type(exc).__name__)
        return False, "off_contribute_network_error"

    if response.status_code != 200:
        logger.info("OFF contribution non-200: status=%d", response.status_code)
        return False, f"off_contribute_http_{response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return False, "off_contribute_non_json"

    if payload.get("status") == 1:
        logger.info("OFF contribution accepted for barcode (length=%d)", len(barcode))
        return True, None

    return False, "off_contribute_rejected"
