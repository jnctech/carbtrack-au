"""Tests for POST /staging/{id}/map — Sonnet-assisted field mapping.

Map must NOT trigger promotion. Mock Anthropic — no live API calls.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from app.models import Food, Source, Staging


@pytest.fixture(name="staging_setup")
def staging_setup_fixture(client, engine):
    """Client with a source and a pending staging entry."""
    with Session(engine) as session:
        source = Source(
            name="Open Food Facts AU",
            tier=1,
            url="https://au.openfoodfacts.org/",
            api_base="https://world.openfoodfacts.org/api/v2",
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        source_id = source.id

    raw_data = json.dumps({
        "product_name": "Weet-Bix Original",
        "brands": "Sanitarium",
        "code": "9300652000115",
        "nutriments": {
            "carbohydrates_100g": 67.3,
            "sugars_100g": 3.3,
            "fiber_100g": 10.5,
            "energy-kj_100g": 1490,
            "proteins_100g": 11.7,
            "fat_100g": 1.4,
            "sodium_100g": 0.27,
        },
    })

    r = client.post("/staging", json={"source_id": source_id, "raw_data": raw_data})
    staging_id = r.json()["id"]

    return client, engine, source_id, staging_id


def _mock_anthropic_response(text: str):
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


class TestStagingMap:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_stores_mapped_data(self, mock_anthropic_cls, staging_setup):
        client, engine, source_id, staging_id = staging_setup

        mapped_result = {
            "name": "Weet-Bix Original",
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
            "gi_rating": None,
            "serving_size_g": 30.0,
        }

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps(mapped_result)
        )

        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"  # NOT promoted
        assert data["mapped_data"] is not None

        # Verify mapped_data is stored in DB
        with Session(engine) as session:
            staging = session.get(Staging, staging_id)
            stored = json.loads(staging.mapped_data)
            assert stored["carbs_per_100g"] == 67.3
            assert stored["energy_kj"] == 1490.0

        # Verify max_tokens=600
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 600

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_does_not_promote(self, mock_anthropic_cls, staging_setup):
        """Map must NOT create a food record — promotion is a separate approve step."""
        client, engine, source_id, staging_id = staging_setup

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({
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
                "gi_rating": None,
                "serving_size_g": 30.0,
            })
        )

        # Count foods before map
        with Session(engine) as session:
            foods_before = len(session.exec(select(Food)).all())

        client.post(f"/staging/{staging_id}/map")

        # Count foods after map — must be unchanged
        with Session(engine) as session:
            foods_after = len(session.exec(select(Food)).all())

        assert foods_after == foods_before

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_response_excludes_raw_data(self, mock_anthropic_cls, staging_setup):
        """API response must not contain raw_data."""
        client, _, _, staging_id = staging_setup

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({"name": "Test", "carbs_per_100g": 10.0})
        )

        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 200
        assert "raw_data" not in response.json()

    def test_map_not_found(self, client):
        response = client.post("/staging/999/map")
        assert response.status_code == 404

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_already_approved_fails(self, mock_anthropic_cls, staging_setup):
        """Cannot map an entry that has already been approved."""
        client, engine, source_id, staging_id = staging_setup

        # First map it
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({
                "name": "Weet-Bix",
                "carbs_per_100g": 67.3,
            })
        )
        client.post(f"/staging/{staging_id}/map")

        # Approve it
        response = client.post(f"/staging/{staging_id}/approve")
        assert response.status_code == 200

        # Try to map again — should fail
        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 400
        assert "approved" in response.json()["detail"]

    def test_map_rejected_fails(self, staging_setup):
        client, _, _, staging_id = staging_setup
        client.post(f"/staging/{staging_id}/reject")

        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 400
        assert "rejected" in response.json()["detail"]

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_handles_markdown_wrapped_json(self, mock_anthropic_cls, staging_setup):
        client, _, _, staging_id = staging_setup

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            '```json\n{"name": "Test Food", "carbs_per_100g": 25.0}\n```'
        )

        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 200
        # Verify mapped_data stored correctly via API response
        mapped_str = response.json()["mapped_data"]
        mapped = json.loads(mapped_str)
        assert mapped["name"] == "Test Food"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_unparseable_response(self, mock_anthropic_cls, staging_setup):
        client, _, _, staging_id = staging_setup

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "I cannot parse this data properly"
        )

        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 502

    @patch.dict("os.environ", {"SONNET_MODEL": "test-model"}, clear=False)
    def test_map_no_api_key(self, staging_setup, monkeypatch):
        client, _, _, staging_id = staging_setup
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 503

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False)
    def test_map_no_sonnet_model(self, staging_setup, monkeypatch):
        client, _, _, staging_id = staging_setup
        monkeypatch.delenv("SONNET_MODEL", raising=False)
        response = client.post(f"/staging/{staging_id}/map")
        assert response.status_code == 503
