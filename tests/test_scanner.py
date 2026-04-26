"""Tests for barcode scanner static page and static file serving."""

import json

import pytest
from sqlmodel import select

from app.models import Food, Source, Staging
from app.routers import foods as foods_router
from app.services import off_client


def _seed(session):
    source = Source(name="AUSNUT 2011-13", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)
    food = Food(
        name="Weet-Bix",
        brand="Sanitarium",
        barcode="9300652001709",
        carbs_per_100g=67.3,
        serving_size_g=30.0,
        source_id=source.id,
    )
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


# --- Static file serving ---


def test_scanner_page_served(client):
    resp = client.get("/static/scanner.html")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_scanner_page_contains_reader(client):
    resp = client.get("/static/scanner.html")
    assert 'id="reader"' in resp.text


def test_scanner_page_contains_api_fetch(client):
    resp = client.get("/static/scanner.html")
    assert "/foods/barcode/" in resp.text


def test_static_404_for_missing_file(client):
    resp = client.get("/static/nonexistent.html")
    assert resp.status_code == 404


# --- Barcode lookup integration (scanner → API) ---


def test_barcode_lookup_returns_food(client, session):
    food = _seed(session)
    resp = client.get(f"/foods/barcode/{food.barcode}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Weet-Bix"
    assert data["carbs_per_100g"] == pytest.approx(67.3)
    assert data["serving_size_g"] == pytest.approx(30.0)
    assert "conflict_flag" in data
    assert data["conflict_flag"] is False


def test_barcode_lookup_404_unknown(client):
    resp = client.get("/foods/barcode/0000000000000")
    assert resp.status_code == 404


def test_barcode_lookup_excludes_inactive(client, session):
    food = _seed(session)
    food.active = False
    session.add(food)
    session.commit()
    resp = client.get(f"/foods/barcode/{food.barcode}")
    assert resp.status_code == 404


def test_barcode_route_not_shadowed_by_food_id(client, session):
    """Ensure /foods/barcode/{barcode} resolves correctly even when
    the barcode's numeric value could match a food_id."""
    source = Source(name="Test", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)
    food = Food(
        name="Target",
        barcode="0000000000001",
        carbs_per_100g=55.0,
        source_id=source.id,
    )
    session.add(food)
    session.commit()
    resp = client.get("/foods/barcode/0000000000001")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Target"


# --- OFF fallback (real-world barcodes from user-supplied scans) ---

PEANUT_BUTTER_BARCODE = "9310885116072"
AEROSOL_BARCODE = "9300764051715"


def _seed_off_source(session):
    """Mirror the real seed entry for Open Food Facts AU."""
    source = Source(name=off_client.OFF_SOURCE_NAME, tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _off_peanut_butter_payload(barcode=PEANUT_BUTTER_BARCODE):
    return {
        "code": barcode,
        "status": 1,
        "product": {
            "product_name": "Smooth Peanut Butter",
            "brands": "Mega Value",
            "categories": "Spreads",
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


def test_local_hit_skips_off_fallback(client, session, monkeypatch):
    """Verified local row must short-circuit before any network call."""
    food = _seed(session)

    def fail(*args, **kwargs):
        raise AssertionError("OFF must not be called when local DB has a hit")

    monkeypatch.setattr(foods_router, "fetch_off_product", fail)

    resp = client.get(f"/foods/barcode/{food.barcode}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Weet-Bix"
    assert "provisional" not in resp.json()


def test_off_fallback_returns_provisional_food_for_peanut_butter(
    client, session, monkeypatch
):
    """Local miss + OFF hit → 200 with provisional payload + staging row."""
    _seed_off_source(session)

    monkeypatch.setattr(
        foods_router,
        "fetch_off_product",
        lambda barcode: _off_peanut_butter_payload(barcode),
    )

    resp = client.get(f"/foods/barcode/{PEANUT_BUTTER_BARCODE}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["barcode"] == PEANUT_BUTTER_BARCODE
    assert body["name"] == "Smooth Peanut Butter"
    assert body["brand"] == "Mega Value"
    assert body["carbs_per_100g"] == pytest.approx(12.5)
    assert body["sodium_mg"] == pytest.approx(400.0)
    assert body["provisional"] is True
    assert body["source"] == off_client.OFF_SOURCE_NAME
    assert body["staging_id"] is not None
    assert body["conflict_flag"] is False

    staged = session.exec(select(Staging)).all()
    assert len(staged) == 1
    assert staged[0].status == "pending"
    persisted_mapping = json.loads(staged[0].mapped_data)
    assert persisted_mapping["barcode"] == PEANUT_BUTTER_BARCODE


def test_off_fallback_404_when_aerosol_not_in_database(client, monkeypatch):
    """Non-food aerosol — OFF returns no match, endpoint must 404."""
    monkeypatch.setattr(foods_router, "fetch_off_product", lambda barcode: None)

    resp = client.get(f"/foods/barcode/{AEROSOL_BARCODE}")
    assert resp.status_code == 404


def test_off_fallback_404_when_payload_unmappable(client, monkeypatch):
    """OFF returned a product but it lacks carbs_per_100g — refuse to dose."""
    payload = _off_peanut_butter_payload()
    payload["product"]["nutriments"].pop("carbohydrates_100g")
    monkeypatch.setattr(foods_router, "fetch_off_product", lambda barcode: payload)

    resp = client.get(f"/foods/barcode/{PEANUT_BUTTER_BARCODE}")
    assert resp.status_code == 404


def test_off_fallback_dedupes_repeat_pending_lookup(client, session, monkeypatch):
    """Hitting the same barcode twice must not create duplicate staging rows."""
    _seed_off_source(session)
    monkeypatch.setattr(
        foods_router,
        "fetch_off_product",
        lambda barcode: _off_peanut_butter_payload(barcode),
    )

    first = client.get(f"/foods/barcode/{PEANUT_BUTTER_BARCODE}")
    second = client.get(f"/foods/barcode/{PEANUT_BUTTER_BARCODE}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["staging_id"] == second.json()["staging_id"]
    assert len(session.exec(select(Staging)).all()) == 1


def test_off_fallback_serves_response_even_without_off_source_seeded(
    client, monkeypatch
):
    """If OFF source row is missing we still serve the carb data — staging is best-effort."""
    monkeypatch.setattr(
        foods_router,
        "fetch_off_product",
        lambda barcode: _off_peanut_butter_payload(barcode),
    )

    resp = client.get(f"/foods/barcode/{PEANUT_BUTTER_BARCODE}")
    assert resp.status_code == 200
    assert resp.json()["staging_id"] is None
    assert resp.json()["provisional"] is True
