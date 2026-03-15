"""Tests for staging router — submit, list, approve, reject, conflict detection."""

import json

import pytest
from sqlmodel import Session, select

from app.database import get_session
from app.models import FoodSourceRef, Staging


@pytest.fixture(name="seeded_client")
def seeded_client_fixture(client, engine):
    """Client with a seeded source for staging tests."""
    from app.models import Source

    with Session(engine) as session:
        source = Source(name="Test Source", tier=1, url="https://example.com")
        session.add(source)
        session.commit()
        session.refresh(source)
        source_id = source.id

    return client, source_id


def _mapped_data(**overrides):
    """Build a valid mapped_data JSON string."""
    data = {
        "name": "Weet-Bix",
        "brand": "Sanitarium",
        "barcode": "9300652000115",
        "category": "Breakfast Cereals",
        "carbs_per_100g": 67.3,
        "sugars_per_100g": 3.3,
        "fibre_per_100g": 10.5,
        "energy_kj": 1490.0,
        "protein_per_100g": 11.7,
        "fat_per_100g": 1.4,
        "sodium_mg": 270.0,
        "gi_rating": "low",
        "serving_size_g": 30.0,
    }
    data.update(overrides)
    return json.dumps(data)


def _set_mapped_data(client, staging_id: int, mapped_data: str):
    """Set mapped_data on a staging entry directly via DB.

    In production this would be done by POST /staging/{id}/map (Phase 3).
    For Phase 2 tests, we write directly to the staging row.
    """
    for session in client.app.dependency_overrides[get_session]():
        staging = session.get(Staging, staging_id)
        staging.mapped_data = mapped_data
        session.add(staging)
        session.commit()
        break


class TestListStaging:
    def test_list_empty(self, client):
        response = client.get("/staging")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_pending_only(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})

        client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        client.post("/staging", json={"source_id": source_id, "raw_data": raw})

        response = client.get("/staging")
        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_list_filter_by_status(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})

        # Submit and reject one
        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        client.post(f"/staging/{staging_id}/reject")

        # Submit another (still pending)
        client.post("/staging", json={"source_id": source_id, "raw_data": raw})

        assert len(client.get("/staging?status=pending").json()) == 1
        assert len(client.get("/staging?status=rejected").json()) == 1


class TestSubmitStaging:
    def test_submit_creates_pending_entry(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "Weet-Bix", "carbs": 67.3})

        response = client.post(
            "/staging", json={"source_id": source_id, "raw_data": raw}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["source_id"] == source_id
        assert data["raw_data"] == raw
        assert data["status"] == "pending"
        assert data["mapped_data"] is None
        assert data["reviewed_at"] is None


class TestApproveStaging:
    def test_approve_without_mapped_data_fails(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})
        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]

        response = client.post(f"/staging/{staging_id}/approve")
        assert response.status_code == 400
        assert "mapped_data" in response.json()["detail"]

    def test_approve_promotes_to_foods(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "Weet-Bix"})
        mapped = _mapped_data()

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        _set_mapped_data(client, staging_id, mapped)

        response = client.post(f"/staging/{staging_id}/approve")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["reviewed_at"] is not None

        # Verify food was created
        foods_response = client.get("/foods?q=Weet-Bix")
        foods = foods_response.json()
        assert len(foods) == 1
        assert foods[0]["name"] == "Weet-Bix"
        assert foods[0]["carbs_per_100g"] == 67.3
        assert foods[0]["source_id"] == source_id

    def test_approve_creates_food_source_ref(self, seeded_client, engine):
        client, source_id = seeded_client
        raw = json.dumps({"product": "Up&Go"})
        mapped = _mapped_data(
            name="Up&Go",
            barcode="9300652201055",
            carbs_per_100g=14.1,
        )

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        _set_mapped_data(client, staging_id, mapped)

        client.post(f"/staging/{staging_id}/approve")

        # Verify food_source_ref was created
        with Session(engine) as session:
            refs = session.exec(select(FoodSourceRef)).all()
            assert len(refs) == 1
            assert refs[0].reported_carbs == 14.1
            assert refs[0].source_id == source_id
            assert "raw_response_json" not in repr(refs[0])

    def test_approve_not_found(self, client):
        response = client.post("/staging/999/approve")
        assert response.status_code == 404

    def test_approve_already_approved(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})
        mapped = _mapped_data(name="TestFood1", barcode=None)

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        _set_mapped_data(client, staging_id, mapped)

        client.post(f"/staging/{staging_id}/approve")

        response = client.post(f"/staging/{staging_id}/approve")
        assert response.status_code == 400
        assert "approved" in response.json()["detail"]

    def test_approve_missing_carbs_per_100g(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})
        mapped = json.dumps({"name": "NoCarbsFood"})

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        _set_mapped_data(client, staging_id, mapped)

        response = client.post(f"/staging/{staging_id}/approve")
        assert response.status_code == 400
        assert "carbs_per_100g" in response.json()["detail"]


class TestConflictDetection:
    def test_no_conflict_within_threshold(self, seeded_client):
        """Second source with ≤5% carb difference should promote normally."""
        client, source_id = seeded_client

        # First entry — baseline
        raw1 = json.dumps({"product": "Tim Tams"})
        mapped1 = _mapped_data(
            name="Tim Tams", barcode="9310072000336", carbs_per_100g=67.0
        )
        r1 = client.post("/staging", json={"source_id": source_id, "raw_data": raw1})
        _set_mapped_data(client, r1.json()["id"], mapped1)
        client.post(f"/staging/{r1.json()['id']}/approve")

        # Second entry — within 5% (67.0 → 69.0 = 2.99%)
        raw2 = json.dumps({"product": "Tim Tams v2"})
        mapped2 = _mapped_data(
            name="Tim Tams", barcode="9310072000336x", carbs_per_100g=69.0
        )
        r2 = client.post("/staging", json={"source_id": source_id, "raw_data": raw2})
        _set_mapped_data(client, r2.json()["id"], mapped2)

        response = client.post(f"/staging/{r2.json()['id']}/approve")
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_conflict_exceeds_threshold(self, seeded_client):
        """Second source with >5% carb difference triggers conflict hold."""
        client, source_id = seeded_client

        # First entry — baseline
        raw1 = json.dumps({"product": "Tip Top Bread"})
        mapped1 = _mapped_data(
            name="Tip Top Bread",
            brand="Tip Top",
            barcode="9310000001111",
            carbs_per_100g=40.0,
        )
        r1 = client.post("/staging", json={"source_id": source_id, "raw_data": raw1})
        _set_mapped_data(client, r1.json()["id"], mapped1)
        client.post(f"/staging/{r1.json()['id']}/approve")

        # Second entry — 16.25% variance (40.0 → 46.5)
        raw2 = json.dumps({"product": "Tip Top Bread v2"})
        mapped2 = _mapped_data(
            name="Tip Top Bread",
            brand="Tip Top",
            barcode="9310000001111x",
            carbs_per_100g=46.5,
        )
        r2 = client.post("/staging", json={"source_id": source_id, "raw_data": raw2})
        _set_mapped_data(client, r2.json()["id"], mapped2)

        response = client.post(f"/staging/{r2.json()['id']}/approve")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "conflict"
        assert data["conflict_notes"] is not None
        assert "16.2%" in data["conflict_notes"]

        # Verify food was NOT added a second time
        foods_response = client.get("/foods?q=Tip Top Bread")
        assert len(foods_response.json()) == 1

    def test_conflict_by_barcode_match(self, seeded_client):
        """Conflict detected via barcode match, not name+brand."""
        client, source_id = seeded_client

        # First entry with barcode
        raw1 = json.dumps({"product": "Product A"})
        mapped1 = _mapped_data(
            name="Product A", barcode="1234567890123", carbs_per_100g=50.0
        )
        r1 = client.post("/staging", json={"source_id": source_id, "raw_data": raw1})
        _set_mapped_data(client, r1.json()["id"], mapped1)
        client.post(f"/staging/{r1.json()['id']}/approve")

        # Second entry — same barcode, different name, >5% carb variance
        raw2 = json.dumps({"product": "Product A renamed"})
        mapped2 = _mapped_data(
            name="Product A Renamed",
            barcode="1234567890123",
            carbs_per_100g=60.0,
        )
        r2 = client.post("/staging", json={"source_id": source_id, "raw_data": raw2})
        _set_mapped_data(client, r2.json()["id"], mapped2)

        response = client.post(f"/staging/{r2.json()['id']}/approve")
        assert response.json()["status"] == "conflict"

    def test_conflict_exact_boundary_5_percent_passes(self, seeded_client):
        """Exactly 5% variance should NOT trigger conflict (threshold is >5%)."""
        client, source_id = seeded_client

        # First entry
        raw1 = json.dumps({"product": "Boundary Food"})
        mapped1 = _mapped_data(
            name="Boundary Food", barcode=None, carbs_per_100g=100.0
        )
        r1 = client.post("/staging", json={"source_id": source_id, "raw_data": raw1})
        _set_mapped_data(client, r1.json()["id"], mapped1)
        client.post(f"/staging/{r1.json()['id']}/approve")

        # Second entry — exactly 5% (100.0 → 105.0)
        raw2 = json.dumps({"product": "Boundary Food v2"})
        mapped2 = _mapped_data(
            name="Boundary Food", barcode=None, carbs_per_100g=105.0
        )
        r2 = client.post("/staging", json={"source_id": source_id, "raw_data": raw2})
        _set_mapped_data(client, r2.json()["id"], mapped2)

        response = client.post(f"/staging/{r2.json()['id']}/approve")
        assert response.json()["status"] == "approved"

    def test_conflict_just_over_5_percent(self, seeded_client):
        """5.1% variance SHOULD trigger conflict."""
        client, source_id = seeded_client

        # First entry
        raw1 = json.dumps({"product": "Over Boundary"})
        mapped1 = _mapped_data(
            name="Over Boundary", barcode=None, carbs_per_100g=100.0
        )
        r1 = client.post("/staging", json={"source_id": source_id, "raw_data": raw1})
        _set_mapped_data(client, r1.json()["id"], mapped1)
        client.post(f"/staging/{r1.json()['id']}/approve")

        # Second entry — 5.1% variance (100.0 → 105.1)
        raw2 = json.dumps({"product": "Over Boundary v2"})
        mapped2 = _mapped_data(
            name="Over Boundary", barcode=None, carbs_per_100g=105.1
        )
        r2 = client.post("/staging", json={"source_id": source_id, "raw_data": raw2})
        _set_mapped_data(client, r2.json()["id"], mapped2)

        response = client.post(f"/staging/{r2.json()['id']}/approve")
        assert response.json()["status"] == "conflict"


class TestRejectStaging:
    def test_reject_sets_status(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "bad data"})

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]

        response = client.post(f"/staging/{staging_id}/reject")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "rejected"
        assert data["reviewed_at"] is not None

    def test_reject_with_note(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "bad data"})

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]

        response = client.post(
            f"/staging/{staging_id}/reject",
            json={"note": "Data looks unreliable"},
        )
        assert response.status_code == 200
        assert response.json()["conflict_notes"] == "Data looks unreliable"

    def test_reject_not_found(self, client):
        response = client.post("/staging/999/reject")
        assert response.status_code == 404

    def test_reject_already_rejected(self, seeded_client):
        client, source_id = seeded_client
        raw = json.dumps({"product": "test"})

        r = client.post("/staging", json={"source_id": source_id, "raw_data": raw})
        staging_id = r.json()["id"]
        client.post(f"/staging/{staging_id}/reject")

        response = client.post(f"/staging/{staging_id}/reject")
        assert response.status_code == 400
