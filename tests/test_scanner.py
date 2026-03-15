"""Tests for barcode scanner static page and static file serving."""

import pytest

from app.models import Food, Source


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
