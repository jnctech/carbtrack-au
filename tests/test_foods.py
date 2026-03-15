"""Tests for the foods router."""

from sqlmodel import Session

from app.models import Food, Source


def _create_source(session: Session) -> Source:
    source = Source(name="AUSNUT 2011-13", tier=1)
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def _create_food(session: Session, source: Source, **overrides) -> Food:
    defaults = {
        "name": "Weet-Bix",
        "carbs_per_100g": 67.3,
        "source_id": source.id,
        "brand": "Sanitarium",
        "category": "Breakfast Cereals",
        "energy_kj": 1490.0,
    }
    defaults.update(overrides)
    food = Food(**defaults)
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


def test_create_food(client, session):
    source = _create_source(session)
    response = client.post("/foods", json={
        "name": "Weet-Bix",
        "carbs_per_100g": 67.3,
        "source_id": source.id,
        "energy_kj": 1490.0,
    })
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Weet-Bix"
    assert data["carbs_per_100g"] == 67.3
    assert data["energy_kj"] == 1490.0
    assert data["active"] is True


def test_get_food_by_id(client, session):
    source = _create_source(session)
    food = _create_food(session, source)
    response = client.get(f"/foods/{food.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Weet-Bix"


def test_get_food_not_found(client):
    response = client.get("/foods/999")
    assert response.status_code == 404


def test_search_by_name(client, session):
    source = _create_source(session)
    _create_food(session, source, name="Weet-Bix")
    _create_food(session, source, name="Tim Tams")
    response = client.get("/foods", params={"q": "Weet"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Weet-Bix"


def test_search_by_category(client, session):
    source = _create_source(session)
    _create_food(session, source, name="Weet-Bix", category="Breakfast Cereals")
    _create_food(session, source, name="Tim Tams", category="Biscuits")
    response = client.get("/foods", params={"category": "Biscuits"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Tim Tams"


def test_barcode_lookup_found(client, session):
    source = _create_source(session)
    _create_food(session, source, barcode="9300652000057")
    response = client.get("/foods/barcode/9300652000057")
    assert response.status_code == 200
    assert response.json()["barcode"] == "9300652000057"


def test_barcode_lookup_not_found(client):
    response = client.get("/foods/barcode/0000000000000")
    assert response.status_code == 404


def test_soft_delete(client, session):
    source = _create_source(session)
    food = _create_food(session, source)
    response = client.delete(f"/foods/{food.id}")
    assert response.status_code == 200
    assert response.json()["detail"] == "Food deactivated"

    # Default GET by ID returns 404 for inactive food
    response = client.get(f"/foods/{food.id}")
    assert response.status_code == 404

    # Explicit include_inactive returns the food
    response = client.get(f"/foods/{food.id}", params={"include_inactive": True})
    assert response.status_code == 200
    assert response.json()["active"] is False


def test_active_filter_excludes_deleted(client, session):
    source = _create_source(session)
    food = _create_food(session, source)
    client.delete(f"/foods/{food.id}")

    # Default list excludes soft-deleted
    response = client.get("/foods")
    assert response.status_code == 200
    assert len(response.json()) == 0

    # Explicit include_inactive shows them
    response = client.get("/foods", params={"include_inactive": True})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_update_food(client, session):
    source = _create_source(session)
    food = _create_food(session, source)
    response = client.patch(f"/foods/{food.id}", json={
        "carbs_per_100g": 65.0,
        "gi_rating": "high",
    })
    assert response.status_code == 200
    data = response.json()
    assert data["carbs_per_100g"] == 65.0
    assert data["gi_rating"] == "high"
    # Other fields unchanged
    assert data["name"] == "Weet-Bix"


def test_update_food_not_found(client):
    response = client.patch("/foods/999", json={"name": "Nope"})
    assert response.status_code == 404


def test_create_food_duplicate_barcode(client, session):
    source = _create_source(session)
    _create_food(session, source, barcode="9300652000057")
    response = client.post("/foods", json={
        "name": "Duplicate Barcode Food",
        "carbs_per_100g": 10.0,
        "barcode": "9300652000057",
    })
    assert response.status_code == 409


def test_pagination(client, session):
    source = _create_source(session)
    for i in range(5):
        _create_food(session, source, name=f"Food {i}")

    response = client.get("/foods", params={"limit": 2, "offset": 0})
    assert len(response.json()) == 2

    response = client.get("/foods", params={"limit": 2, "offset": 4})
    assert len(response.json()) == 1
