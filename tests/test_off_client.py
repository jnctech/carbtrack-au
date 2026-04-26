"""Unit tests for the Open Food Facts client + field mapper.

Uses httpx.MockTransport to exercise the HTTP path without live network calls.
Two real Australian barcodes drive the happy/sad path coverage:
- 9310885116072 — Australian peanut butter (food, expected in OFF)
- 9300764051715 — hydrocarbon aerosol can (non-food, expected absent from OFF)
"""

import json

import httpx
import pytest

from app.services import off_client


PEANUT_BUTTER_BARCODE = "9310885116072"
AEROSOL_BARCODE = "9300764051715"


def _peanut_butter_payload(barcode: str = PEANUT_BUTTER_BARCODE) -> dict:
    return {
        "code": barcode,
        "status": 1,
        "product": {
            "product_name": "Smooth Peanut Butter",
            "brands": "Mega Value",
            "categories": "Spreads, Peanut butters",
            "serving_quantity": 20,
            "serving_quantity_unit": "g",
            "nutriments": {
                "carbohydrates_100g": 12.5,
                "sugars_100g": 6.0,
                "fiber_100g": 6.5,
                "energy-kj_100g": 2510,
                "proteins_100g": 27.0,
                "fat_100g": 49.0,
                "sodium_100g": 0.4,
            },
        },
    }


def _make_client(handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


# --- fetch_off_product ---


def test_fetch_off_product_returns_payload_for_known_barcode(monkeypatch):
    monkeypatch.delenv("OFF_FALLBACK_ENABLED", raising=False)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=_peanut_butter_payload())

    with _make_client(handler) as client:
        result = off_client.fetch_off_product(PEANUT_BUTTER_BARCODE, client=client)

    assert result is not None
    assert result["status"] == 1
    assert result["product"]["product_name"] == "Smooth Peanut Butter"
    assert PEANUT_BUTTER_BARCODE in captured["url"]
    assert captured["ua"], "User-Agent header must be sent per OFF ToS"


def test_fetch_off_product_returns_none_for_missing_product():
    """Aerosol can — not a food, OFF returns status=0."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": AEROSOL_BARCODE, "status": 0, "status_verbose": "product not found"}
        )

    with _make_client(handler) as client:
        assert off_client.fetch_off_product(AEROSOL_BARCODE, client=client) is None


def test_fetch_off_product_returns_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    with _make_client(handler) as client:
        assert off_client.fetch_off_product("0000000000000", client=client) is None


def test_fetch_off_product_returns_none_on_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure")

    with _make_client(handler) as client:
        assert off_client.fetch_off_product(PEANUT_BUTTER_BARCODE, client=client) is None


def test_fetch_off_product_returns_none_on_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    with _make_client(handler) as client:
        assert off_client.fetch_off_product(PEANUT_BUTTER_BARCODE, client=client) is None


def test_fetch_off_product_disabled_via_env(monkeypatch):
    monkeypatch.setenv("OFF_FALLBACK_ENABLED", "false")
    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["hit"] = True
        return httpx.Response(200, json=_peanut_butter_payload())

    with _make_client(handler) as client:
        result = off_client.fetch_off_product(PEANUT_BUTTER_BARCODE, client=client)

    assert result is None
    assert called["hit"] is False, "Disabled flag must short-circuit before HTTP"


def test_fetch_off_product_uses_configured_user_agent(monkeypatch):
    monkeypatch.setenv("OFF_USER_AGENT", "CustomAgent/9.9 (test@example.com)")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=_peanut_butter_payload())

    with _make_client(handler) as client:
        off_client.fetch_off_product(PEANUT_BUTTER_BARCODE, client=client)

    assert captured["ua"] == "CustomAgent/9.9 (test@example.com)"


# --- map_off_to_carbtrack ---


def test_map_off_to_carbtrack_full_payload():
    payload = _peanut_butter_payload()
    mapped = off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE)

    assert mapped is not None
    assert mapped["barcode"] == PEANUT_BUTTER_BARCODE
    assert mapped["name"] == "Smooth Peanut Butter"
    assert mapped["brand"] == "Mega Value"
    assert mapped["category"] == "Spreads"
    assert mapped["carbs_per_100g"] == pytest.approx(12.5)
    assert mapped["sugars_per_100g"] == pytest.approx(6.0)
    assert mapped["fibre_per_100g"] == pytest.approx(6.5)
    assert mapped["energy_kj"] == pytest.approx(2510)
    assert mapped["protein_per_100g"] == pytest.approx(27.0)
    assert mapped["fat_per_100g"] == pytest.approx(49.0)
    assert mapped["sodium_mg"] == pytest.approx(400.0)  # 0.4 g → 400 mg
    assert mapped["serving_size_g"] == pytest.approx(20.0)
    assert mapped["gi_rating"] is None


def test_map_off_to_carbtrack_converts_kcal_to_kj():
    payload = _peanut_butter_payload()
    payload["product"]["nutriments"].pop("energy-kj_100g")
    payload["product"]["nutriments"]["energy-kcal_100g"] = 600

    mapped = off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE)

    assert mapped is not None
    assert mapped["energy_kj"] == pytest.approx(600 * 4.184, abs=0.05)


def test_map_off_to_carbtrack_ignores_non_gram_serving():
    payload = _peanut_butter_payload()
    payload["product"]["serving_quantity_unit"] = "ml"

    mapped = off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE)

    assert mapped is not None
    assert mapped["serving_size_g"] is None


def test_map_off_to_carbtrack_returns_none_without_carbs():
    payload = _peanut_butter_payload()
    payload["product"]["nutriments"].pop("carbohydrates_100g")

    assert off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE) is None


def test_map_off_to_carbtrack_returns_none_without_name():
    payload = _peanut_butter_payload()
    payload["product"]["product_name"] = ""

    assert off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE) is None


def test_map_off_to_carbtrack_rejects_negative_carbs():
    payload = _peanut_butter_payload()
    payload["product"]["nutriments"]["carbohydrates_100g"] = -1

    assert off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE) is None


def test_map_off_to_carbtrack_takes_first_brand_only():
    payload = _peanut_butter_payload()
    payload["product"]["brands"] = "Mega Value, Aldi, House Brand"

    mapped = off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE)

    assert mapped is not None
    assert mapped["brand"] == "Mega Value"


def test_map_off_to_carbtrack_handles_string_numbers():
    payload = _peanut_butter_payload()
    payload["product"]["nutriments"]["carbohydrates_100g"] = "12.5"
    payload["product"]["nutriments"]["sugars_100g"] = "not-a-number"

    mapped = off_client.map_off_to_carbtrack(payload, PEANUT_BUTTER_BARCODE)

    assert mapped is not None
    assert mapped["carbs_per_100g"] == pytest.approx(12.5)
    assert mapped["sugars_per_100g"] is None


def test_payload_round_trips_through_json():
    """Sanity: the canonical OFF response shape survives JSON serialisation
    so it can be stored in staging.raw_data without loss."""
    payload = _peanut_butter_payload()
    serialised = json.dumps(payload)
    assert json.loads(serialised) == payload


# --- submit_off_product (write-back to OFF) ---


def _user_contribution() -> dict:
    return {
        "barcode": PEANUT_BUTTER_BARCODE,
        "name": "Smooth Peanut Butter",
        "brand": "Mega Value",
        "category": "Spreads",
        "carbs_per_100g": 12.5,
        "sugars_per_100g": 6.0,
        "fibre_per_100g": 6.5,
        "energy_kj": 2510,
        "protein_per_100g": 27.0,
        "fat_per_100g": 49.0,
        "sodium_mg": 400.0,
        "serving_size_g": 20.0,
    }


def test_submit_off_product_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OFF_CONTRIBUTE_ENABLED", raising=False)
    ok, reason = off_client.submit_off_product(
        PEANUT_BUTTER_BARCODE, _user_contribution()
    )
    assert ok is False
    assert reason == "off_contribute_disabled"


def test_submit_off_product_success(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": 1, "status_verbose": "ok"})

    with _make_client(handler) as client:
        ok, reason = off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )

    assert ok is True
    assert reason is None
    assert "product_jqm2.php" in captured["url"]
    body = captured["body"]
    assert f"code={PEANUT_BUTTER_BARCODE}" in body
    assert "product_name=Smooth+Peanut+Butter" in body
    assert "nutriment_carbohydrates=12.5" in body
    # sodium scales mg → g for OFF
    assert "nutriment_sodium=0.4" in body
    assert "nutriment_sodium_unit=g" in body
    # energy stored as kJ on both sides
    assert "nutriment_energy-kj=2510" in body


def test_submit_off_product_omits_credentials_when_unset(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")
    monkeypatch.delenv("OFF_USERNAME", raising=False)
    monkeypatch.delenv("OFF_PASSWORD", raising=False)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": 1})

    with _make_client(handler) as client:
        off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )

    assert "user_id" not in captured["body"]
    assert "password" not in captured["body"]
    assert "app_name=CarbTrackAU" in captured["body"]


def test_submit_off_product_includes_credentials_when_set(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")
    monkeypatch.setenv("OFF_USERNAME", "ctuser")
    monkeypatch.setenv("OFF_PASSWORD", "secret")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": 1})

    with _make_client(handler) as client:
        off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )

    assert "user_id=ctuser" in captured["body"]
    assert "password=secret" in captured["body"]


def test_submit_off_product_rejected_returns_reason(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 0, "error": "validation"})

    with _make_client(handler) as client:
        ok, reason = off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )
    assert ok is False
    assert reason == "off_contribute_rejected"


def test_submit_off_product_network_error(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated")

    with _make_client(handler) as client:
        ok, reason = off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )
    assert ok is False
    assert reason == "off_contribute_network_error"


def test_submit_off_product_non_200_status(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    with _make_client(handler) as client:
        ok, reason = off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )
    assert ok is False
    assert reason == "off_contribute_http_500"


def test_submit_off_product_non_json_response(monkeypatch):
    monkeypatch.setenv("OFF_CONTRIBUTE_ENABLED", "true")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>fail</html>")

    with _make_client(handler) as client:
        ok, reason = off_client.submit_off_product(
            PEANUT_BUTTER_BARCODE, _user_contribution(), client=client
        )
    assert ok is False
    assert reason == "off_contribute_non_json"
