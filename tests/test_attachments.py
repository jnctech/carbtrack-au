"""Tests for the recipe attachments router."""

import io

import pytest
from PIL import Image
from sqlmodel import Session

from app.models import Food, Recipe


@pytest.fixture
def att_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ATTACHMENTS_DIR", str(tmp_path))
    return tmp_path


def _seed_recipe(session: Session) -> int:
    food = Food(name="X", carbs_per_100g=10)
    session.add(food)
    session.commit()
    session.refresh(food)
    recipe = Recipe(name="r", servings=1)
    session.add(recipe)
    session.commit()
    session.refresh(recipe)
    return recipe.id


def _jpeg_bytes(size=(20, 20), color=(200, 50, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_creates_file_thumb_and_row(client, session, att_dir):
    rid = _seed_recipe(session)
    payload = _jpeg_bytes()

    response = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("photo.jpg", payload, "image/jpeg")},
        data={"caption": "hello"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["caption"] == "hello"
    assert body["mime_type"] == "image/jpeg"
    assert body["url"].startswith(f"/attachments/{rid}/")
    assert body["thumb_url"].endswith(".webp")

    photo_path = att_dir / str(rid) / body["filename"]
    stem = body["filename"].rsplit(".", 1)[0]
    thumb_path = att_dir / "thumbs" / str(rid) / f"{stem}.webp"
    assert photo_path.exists()
    assert thumb_path.exists()
    assert photo_path.read_bytes() == payload
    # Thumb decodes as a real WebP
    with Image.open(thumb_path) as t:
        assert t.format == "WEBP"


def test_upload_recipe_in_full_detail(client, session, att_dir):
    rid = _seed_recipe(session)
    client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    detail = client.get(f"/recipes/{rid}").json()
    assert len(detail["attachments"]) == 1
    listed = client.get("/recipes").json()
    target = next(r for r in listed if r["id"] == rid)
    assert target["thumb_url"] is not None


def test_upload_unsupported_mime_returns_415(client, session, att_dir):
    rid = _seed_recipe(session)
    response = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("doc.pdf", b"%PDF-", "application/pdf")},
    )
    assert response.status_code == 415


def test_upload_empty_returns_400(client, session, att_dir):
    rid = _seed_recipe(session)
    response = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("a.jpg", b"", "image/jpeg")},
    )
    assert response.status_code == 400


def test_upload_corrupt_image_returns_400(client, session, att_dir):
    rid = _seed_recipe(session)
    response = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("a.jpg", b"not really a jpeg", "image/jpeg")},
    )
    assert response.status_code == 400
    # No orphan files left behind
    assert list((att_dir / str(rid)).glob("*")) == []


def test_upload_unknown_recipe_404(client, att_dir):
    response = client.post(
        "/recipes/9999/attachments",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 404


def test_patch_caption_and_sort(client, session, att_dir):
    rid = _seed_recipe(session)
    att_id = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    ).json()["id"]

    response = client.patch(
        f"/recipes/{rid}/attachments/{att_id}",
        json={"caption": "updated", "sort_order": 5},
    )
    assert response.status_code == 200
    assert response.json()["caption"] == "updated"
    assert response.json()["sort_order"] == 5


def test_patch_unknown_attachment_404(client, session, att_dir):
    rid = _seed_recipe(session)
    response = client.patch(
        f"/recipes/{rid}/attachments/9999",
        json={"caption": "x"},
    )
    assert response.status_code == 404


def test_delete_removes_file_thumb_and_row(client, session, att_dir):
    rid = _seed_recipe(session)
    body = client.post(
        f"/recipes/{rid}/attachments",
        files={"file": ("a.jpg", _jpeg_bytes(), "image/jpeg")},
    ).json()
    att_id = body["id"]
    photo_path = att_dir / str(rid) / body["filename"]
    stem = body["filename"].rsplit(".", 1)[0]
    thumb_path = att_dir / "thumbs" / str(rid) / f"{stem}.webp"
    assert photo_path.exists() and thumb_path.exists()

    response = client.delete(f"/recipes/{rid}/attachments/{att_id}")
    assert response.status_code == 200
    assert not photo_path.exists()
    assert not thumb_path.exists()

    detail = client.get(f"/recipes/{rid}").json()
    assert detail["attachments"] == []


def test_delete_unknown_attachment_404(client, session, att_dir):
    rid = _seed_recipe(session)
    response = client.delete(f"/recipes/{rid}/attachments/9999")
    assert response.status_code == 404
