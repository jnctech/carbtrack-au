"""Tests for barcode scanner static page, static file serving, and OFF import."""

import json

import pytest

from app.models import Food, Source, Staging


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


def _off_source(session):
    source = Source(name="Open Food Facts AU", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


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


def test_scanner_served_with_native_detector_flag(client):
    """Phase 5A.1: served HTML includes the html5-qrcode flag that opts into the
    browser's native BarcodeDetector when present. Smoke-only — actual delegation
    to ML Kit happens in the browser and isn't exercised here."""
    resp = client.get("/static/scanner.html")
    assert "useBarCodeDetectorIfSupported" in resp.text


def test_scanner_served_with_pinned_video_constraints(client):
    """Phase 5A.1: served HTML includes the 1920×1080 + continuous-AF hints.
    Smoke-only — browser may ignore the hints; this just guards against
    accidental regressions to facingMode-only config."""
    resp = client.get("/static/scanner.html")
    assert 'width: { ideal: 1920 }' in resp.text
    assert 'focusMode: "continuous"' in resp.text


def test_scanner_served_with_torch_button(client):
    """Phase 5A.1: served HTML contains the torch DOM elements and the
    applyVideoConstraints call that toggles the hardware torch. CSS keeps
    the row hidden until JS confirms the camera reports the capability."""
    resp = client.get("/static/scanner.html")
    assert 'id="torch-btn"' in resp.text
    assert 'id="torch-row"' in resp.text
    assert "applyVideoConstraints" in resp.text
    assert "torch" in resp.text


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


# --- Scanner page: OFF import elements ---


def test_scanner_page_contains_off_api(client):
    resp = client.get("/static/scanner.html")
    assert "openfoodfacts.org" in resp.text


def test_scanner_page_contains_import_buttons(client):
    resp = client.get("/static/scanner.html")
    assert "btn-staging" in resp.text
    assert "btn-quick" in resp.text


# --- set-mapped endpoint ---


def test_set_mapped_data(client, session):
    source = _off_source(session)
    resp = client.post("/staging", json={
        "source_id": source.id,
        "raw_data": json.dumps({"product_name": "Test"}),
    })
    assert resp.status_code == 201
    staging_id = resp.json()["id"]

    mapped = json.dumps({"name": "Test Food", "carbs_per_100g": 50.0})
    resp = client.post(f"/staging/{staging_id}/set-mapped", json={
        "mapped_data": mapped,
    })
    assert resp.status_code == 200
    assert resp.json()["mapped_data"] == mapped


def test_set_mapped_rejects_invalid_json(client, session):
    source = _off_source(session)
    resp = client.post("/staging", json={
        "source_id": source.id,
        "raw_data": json.dumps({"test": True}),
    })
    staging_id = resp.json()["id"]

    resp = client.post(f"/staging/{staging_id}/set-mapped", json={
        "mapped_data": "not json",
    })
    assert resp.status_code == 422


def test_set_mapped_requires_name_and_carbs(client, session):
    source = _off_source(session)
    resp = client.post("/staging", json={
        "source_id": source.id,
        "raw_data": json.dumps({"test": True}),
    })
    staging_id = resp.json()["id"]

    resp = client.post(f"/staging/{staging_id}/set-mapped", json={
        "mapped_data": json.dumps({"name": "Test"}),
    })
    assert resp.status_code == 422


def test_set_mapped_not_found(client):
    resp = client.post("/staging/9999/set-mapped", json={
        "mapped_data": json.dumps({"name": "X", "carbs_per_100g": 1.0}),
    })
    assert resp.status_code == 404


def test_set_mapped_rejects_approved_entry(client, session):
    source = _off_source(session)
    staging = Staging(
        source_id=source.id,
        raw_data=json.dumps({"test": True}),
        status="approved",
    )
    session.add(staging)
    session.commit()
    session.refresh(staging)

    resp = client.post(f"/staging/{staging.id}/set-mapped", json={
        "mapped_data": json.dumps({"name": "X", "carbs_per_100g": 1.0}),
    })
    assert resp.status_code == 400


# --- Quick add (direct POST /foods) ---


def test_quick_add_creates_food(client, session):
    source = _off_source(session)
    resp = client.post("/foods", json={
        "name": "OFF Product",
        "barcode": "9999999999999",
        "carbs_per_100g": 45.2,
        "source_id": source.id,
        "source_confidence": 0.7,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "OFF Product"
    assert data["carbs_per_100g"] == pytest.approx(45.2)
    assert data["source_confidence"] == pytest.approx(0.7)


def test_quick_add_duplicate_barcode_returns_409(client, session):
    _seed(session)
    resp = client.post("/foods", json={
        "name": "Duplicate",
        "barcode": "9300652001709",
        "carbs_per_100g": 50.0,
    })
    assert resp.status_code == 409
