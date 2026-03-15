"""Tests for query builder router — construct, sources, map-fields, query-template.

All Anthropic API calls are mocked — no live API calls in tests.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.models import Source


@pytest.fixture(name="sources_with_api")
def sources_with_api_fixture(client, engine):
    """Seed sources: one with API, one without."""
    with Session(engine) as session:
        api_source = Source(
            name="Open Food Facts AU",
            tier=1,
            url="https://au.openfoodfacts.org/",
            api_base="https://world.openfoodfacts.org/api/v2",
            api_notes="Free REST API, no auth. /product/{barcode}.json",
        )
        no_api_source = Source(
            name="AUSNUT 2011-13",
            tier=1,
            url="https://www.foodstandards.gov.au/",
            api_base=None,
            api_notes="CSV bulk download only",
        )
        session.add(api_source)
        session.add(no_api_source)
        session.commit()
        session.refresh(api_source)
        session.refresh(no_api_source)
        return client, api_source.id, no_api_source.id


def _mock_anthropic_response(text: str):
    """Build a mock Anthropic messages.create response."""
    mock_content = MagicMock()
    mock_content.text = text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    return mock_response


class TestConstructEndpoint:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_construct_returns_template(self, mock_anthropic_cls, sources_with_api):
        client, api_source_id, _ = sources_with_api

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({
                "curl": "curl 'https://world.openfoodfacts.org/cgi/search.pl?search_terms=Weet-Bix&cc=au'",
                "fetch": "fetch('https://world.openfoodfacts.org/cgi/search.pl?search_terms=Weet-Bix&cc=au')",
                "url": "https://world.openfoodfacts.org/cgi/search.pl?search_terms=Weet-Bix&cc=au",
                "notes": "Open Food Facts AU search. No auth required.",
            })
        )

        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id, "food_name": "Weet-Bix"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "curl" in data
        assert "Weet-Bix" in data["curl"]
        assert "fetch" in data
        assert "url" in data
        assert "notes" in data

        # Verify Sonnet was called with max_tokens=400
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 400

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_construct_barcode_query(self, mock_anthropic_cls, sources_with_api):
        client, api_source_id, _ = sources_with_api

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({
                "curl": "curl 'https://world.openfoodfacts.org/api/v2/product/9300652000115.json'",
                "fetch": "fetch('https://world.openfoodfacts.org/api/v2/product/9300652000115.json')",
                "url": "https://world.openfoodfacts.org/api/v2/product/9300652000115.json",
                "notes": "Barcode lookup.",
            })
        )

        response = client.post(
            "/query-builder/construct",
            json={
                "source_id": api_source_id,
                "barcode": "9300652000115",
                "query_type": "barcode",
            },
        )

        assert response.status_code == 200
        assert "9300652000115" in response.json()["url"]

    def test_construct_source_not_found(self, client):
        response = client.post(
            "/query-builder/construct",
            json={"source_id": 999, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 404

    def test_construct_source_no_api(self, sources_with_api):
        client, _, no_api_id = sources_with_api
        response = client.post(
            "/query-builder/construct",
            json={"source_id": no_api_id, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 400
        assert "no API endpoint" in response.json()["detail"]

    def test_construct_no_search_term(self, sources_with_api):
        client, api_source_id, _ = sources_with_api
        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id},
        )
        assert response.status_code == 400
        assert "food_name or barcode" in response.json()["detail"]

    @patch.dict("os.environ", {"SONNET_MODEL": "test-model"}, clear=False)
    def test_construct_no_api_key(self, sources_with_api, monkeypatch):
        client, api_source_id, _ = sources_with_api
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 503

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=False)
    def test_construct_no_sonnet_model(self, sources_with_api, monkeypatch):
        client, api_source_id, _ = sources_with_api
        monkeypatch.delenv("SONNET_MODEL", raising=False)
        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 503

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_construct_handles_markdown_wrapped_json(self, mock_anthropic_cls, sources_with_api):
        """AI sometimes wraps JSON in markdown code blocks."""
        client, api_source_id, _ = sources_with_api

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            '```json\n{"curl": "curl example", "fetch": "fetch example", "url": "https://example.com", "notes": "test"}\n```'
        )

        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 200
        assert response.json()["url"] == "https://example.com"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_construct_unparseable_response(self, mock_anthropic_cls, sources_with_api):
        client, api_source_id, _ = sources_with_api

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "This is not JSON at all"
        )

        response = client.post(
            "/query-builder/construct",
            json={"source_id": api_source_id, "food_name": "Weet-Bix"},
        )
        assert response.status_code == 502


class TestQueryBuilderSources:
    def test_list_sources_with_api(self, sources_with_api):
        client, api_source_id, _ = sources_with_api

        response = client.get("/query-builder/sources")
        assert response.status_code == 200
        data = response.json()
        # Only sources with api_base should be returned
        assert len(data) == 1
        assert data[0]["name"] == "Open Food Facts AU"
        assert data[0]["api_base"] is not None

    def test_list_sources_empty(self, client):
        response = client.get("/query-builder/sources")
        assert response.status_code == 200
        assert response.json() == []


class TestMapFields:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_map_fields_returns_mapping(self, mock_anthropic_cls, sources_with_api):
        client, api_source_id, _ = sources_with_api

        mapped_result = {
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

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps(mapped_result)
        )

        raw_json = json.dumps({
            "product_name": "Weet-Bix Original",
            "brands": "Sanitarium",
            "code": "9300652000115",
            "nutriments": {"carbohydrates_100g": 67.3},
        })

        response = client.post(
            "/query-builder/map-fields",
            json={"source_id": api_source_id, "raw_json": raw_json},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == api_source_id
        assert data["mapped_data"]["carbs_per_100g"] == 67.3
        assert data["mapped_data"]["energy_kj"] == 1490.0

        # Verify max_tokens=600 for mapping
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 600

    def test_map_fields_source_not_found(self, client):
        response = client.post(
            "/query-builder/map-fields",
            json={"source_id": 999, "raw_json": "{}"},
        )
        assert response.status_code == 404

    def test_map_fields_invalid_json(self, sources_with_api):
        client, api_source_id, _ = sources_with_api
        response = client.post(
            "/query-builder/map-fields",
            json={"source_id": api_source_id, "raw_json": "not json"},
        )
        assert response.status_code == 400

    def test_map_fields_rejects_oversized_payload(self, sources_with_api):
        client, api_source_id, _ = sources_with_api
        huge = json.dumps({"x": "a" * 1_100_000})
        response = client.post(
            "/query-builder/map-fields",
            json={"source_id": api_source_id, "raw_json": huge},
        )
        assert response.status_code == 422


class TestQueryTemplate:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key", "SONNET_MODEL": "test-model"})
    @patch("app.ai_helpers.anthropic.Anthropic")
    def test_query_template_returns_result(self, mock_anthropic_cls, sources_with_api):
        client, api_source_id, _ = sources_with_api

        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _mock_anthropic_response(
            json.dumps({
                "curl": "curl 'https://world.openfoodfacts.org/cgi/search.pl?search_terms=Weet-Bix'",
                "url": "https://world.openfoodfacts.org/cgi/search.pl?search_terms=Weet-Bix",
                "notes": "Free API, no auth.",
            })
        )

        response = client.post(
            f"/sources/{api_source_id}/query-template",
            json={"food_name": "Weet-Bix"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_id"] == api_source_id
        assert "curl" in data
        assert "url" in data

        # Verify max_tokens=400
        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 400

    def test_query_template_source_not_found(self, client):
        response = client.post(
            "/sources/999/query-template",
            json={"food_name": "Weet-Bix"},
        )
        assert response.status_code == 404

    def test_query_template_no_api(self, sources_with_api):
        client, _, no_api_id = sources_with_api
        response = client.post(
            f"/sources/{no_api_id}/query-template",
            json={"food_name": "Weet-Bix"},
        )
        assert response.status_code == 400

    def test_query_template_no_search_term(self, sources_with_api):
        client, api_source_id, _ = sources_with_api
        response = client.post(
            f"/sources/{api_source_id}/query-template",
            json={},
        )
        assert response.status_code == 400


class TestNoOutboundHTTP:
    """Verify the queries module does not import HTTP client libraries."""

    def test_no_httpx_import(self):
        import app.routers.queries as q

        # The module should not have httpx, requests, or urllib imported
        module_source = q.__file__
        with open(module_source) as f:
            source = f.read()
        assert "import httpx" not in source
        assert "import requests" not in source
        assert "from urllib" not in source

    def test_no_requests_import(self):
        import app.routers.queries as q

        assert not hasattr(q, "requests")
        assert not hasattr(q, "httpx")
