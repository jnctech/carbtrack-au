"""Tests for the recipes router."""

from sqlmodel import Session

from app.models import Food, Recipe


def _seed_food(session: Session, **overrides) -> Food:
    defaults = {"name": "Banana, raw", "carbs_per_100g": 22.8}
    defaults.update(overrides)
    food = Food(**defaults)
    session.add(food)
    session.commit()
    session.refresh(food)
    return food


def test_create_recipe_with_ingredients(client, session):
    f1 = _seed_food(session, name="Banana, raw", carbs_per_100g=22.8)
    f2 = _seed_food(session, name="Greek yoghurt, natural", carbs_per_100g=4.7)

    response = client.post(
        "/recipes",
        json={
            "name": "Banana yoghurt",
            "servings": 2,
            "notes": "morning snack",
            "ingredients": [
                {"food_id": f1.id, "quantity_g": 100, "sort_order": 0},
                {"food_id": f2.id, "quantity_g": 150, "sort_order": 1},
            ],
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Banana yoghurt"
    assert data["servings"] == 2
    assert data["notes"] == "morning snack"
    assert len(data["ingredients"]) == 2
    # 22.8 + (4.7 * 1.5) = 22.8 + 7.05 = 29.85
    assert data["total_carbs_g"] == 29.85


def test_create_recipe_unknown_food_404(client):
    response = client.post(
        "/recipes",
        json={
            "name": "Bad",
            "servings": 1,
            "ingredients": [{"food_id": 9999, "quantity_g": 50}],
        },
    )
    assert response.status_code == 404
    assert "9999" in response.json()["detail"]


def test_get_recipe_full_detail(client, session):
    f = _seed_food(session)
    create = client.post(
        "/recipes",
        json={
            "name": "Single banana",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 50}],
        },
    )
    recipe_id = create.json()["id"]

    response = client.get(f"/recipes/{recipe_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Single banana"
    assert data["ingredients"][0]["carbs_g"] == 11.4
    assert data["attachments"] == []


def test_get_recipe_not_found(client):
    assert client.get("/recipes/9999").status_code == 404


def test_get_soft_deleted_recipe_404(client, session):
    f = _seed_food(session)
    rid = client.post(
        "/recipes",
        json={
            "name": "Tmp",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 30}],
        },
    ).json()["id"]
    assert client.delete(f"/recipes/{rid}").status_code == 200
    assert client.get(f"/recipes/{rid}").status_code == 404


def test_update_recipe_replaces_ingredients(client, session):
    f1 = _seed_food(session, name="A", carbs_per_100g=10)
    f2 = _seed_food(session, name="B", carbs_per_100g=20)
    f3 = _seed_food(session, name="C", carbs_per_100g=30)

    rid = client.post(
        "/recipes",
        json={
            "name": "v1",
            "servings": 1,
            "ingredients": [
                {"food_id": f1.id, "quantity_g": 100},
                {"food_id": f2.id, "quantity_g": 100},
            ],
        },
    ).json()["id"]

    response = client.put(
        f"/recipes/{rid}",
        json={
            "name": "v2",
            "servings": 3,
            "pinned": True,
            "ingredients": [{"food_id": f3.id, "quantity_g": 200}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "v2"
    assert data["servings"] == 3
    assert data["pinned"] is True
    assert len(data["ingredients"]) == 1
    assert data["ingredients"][0]["food_id"] == f3.id
    assert data["total_carbs_g"] == 60.0


def test_update_unknown_recipe_404(client, session):
    f = _seed_food(session)
    response = client.put(
        "/recipes/9999",
        json={
            "name": "x",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 10}],
        },
    )
    assert response.status_code == 404


def test_update_with_unknown_food_404(client, session):
    f = _seed_food(session)
    rid = client.post(
        "/recipes",
        json={
            "name": "x",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 10}],
        },
    ).json()["id"]
    response = client.put(
        f"/recipes/{rid}",
        json={
            "name": "x",
            "servings": 1,
            "ingredients": [{"food_id": 9999, "quantity_g": 10}],
        },
    )
    assert response.status_code == 404


def test_list_excludes_deleted_orders_pinned_first(client, session):
    f = _seed_food(session)
    client.post(
        "/recipes",
        json={
            "name": "A",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 10}],
        },
    )
    client.post(
        "/recipes",
        json={
            "name": "B",
            "servings": 1,
            "pinned": True,
            "ingredients": [{"food_id": f.id, "quantity_g": 10}],
        },
    )
    c = client.post(
        "/recipes",
        json={
            "name": "C",
            "servings": 1,
            "ingredients": [{"food_id": f.id, "quantity_g": 10}],
        },
    ).json()
    client.delete(f"/recipes/{c['id']}")

    listed = client.get("/recipes").json()
    names = [r["name"] for r in listed]
    assert "C" not in names
    assert names[0] == "B"  # pinned first
    assert set(names) == {"A", "B"}
    assert listed[0]["ingredient_count"] == 1
    assert listed[0]["thumb_url"] is None

    # include_inactive returns the deleted one too
    full = client.get("/recipes", params={"include_inactive": True}).json()
    assert any(r["name"] == "C" for r in full)


def test_calculate_endpoint(client, session):
    f1 = _seed_food(session, name="X", carbs_per_100g=50)
    f2 = _seed_food(session, name="Y", carbs_per_100g=10)

    response = client.post(
        "/recipes/calculate",
        json=[
            {"food_id": f1.id, "quantity_g": 200},
            {"food_id": f2.id, "quantity_g": 50},
        ],
    )
    assert response.status_code == 200
    data = response.json()
    # 100 + 5 = 105
    assert data["total_carbs_g"] == 105.0
    assert len(data["ingredients"]) == 2


def test_calculate_empty_list(client):
    response = client.post("/recipes/calculate", json=[])
    assert response.status_code == 200
    assert response.json() == {"total_carbs_g": 0.0, "ingredients": []}


def test_calculate_unknown_food_404(client):
    response = client.post(
        "/recipes/calculate", json=[{"food_id": 9999, "quantity_g": 100}]
    )
    assert response.status_code == 404


def test_delete_nonexistent_recipe_404(client):
    assert client.delete("/recipes/9999").status_code == 404


def test_recipe_model_default_active(session):
    r = Recipe(name="x")
    session.add(r)
    session.commit()
    session.refresh(r)
    assert r.active is True
    assert r.pinned is False
    assert r.servings == 1
