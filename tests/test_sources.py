"""Tests for the sources router."""

from sqlmodel import Session

from app.models import Source


def _seed_sources(session: Session):
    sources = [
        Source(name="AUSNUT 2011-13", tier=1, url="https://example.com/ausnut"),
        Source(name="Open Food Facts AU", tier=1, url="https://example.com/off"),
        Source(name="CalorieKing AU", tier=3, url="https://example.com/ck"),
    ]
    for s in sources:
        session.add(s)
    session.commit()


def test_list_sources(client, session):
    _seed_sources(session)
    response = client.get("/sources")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


def test_list_sources_empty(client):
    response = client.get("/sources")
    assert response.status_code == 200
    assert response.json() == []


def test_get_source_by_id(client, session):
    _seed_sources(session)
    response = client.get("/sources/1")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "AUSNUT 2011-13"
    assert data["tier"] == 1


def test_get_source_includes_opus_fields(client, session):
    source = Source(
        name="Test Source",
        tier=2,
        opus_reliability="Reliable",
        opus_conflicts="None known",
    )
    session.add(source)
    session.commit()

    response = client.get("/sources/1")
    assert response.status_code == 200
    data = response.json()
    assert data["opus_reliability"] == "Reliable"
    assert data["opus_conflicts"] == "None known"


def test_get_source_not_found(client):
    response = client.get("/sources/999")
    assert response.status_code == 404
