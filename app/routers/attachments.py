"""Recipe attachments router — photo upload, thumb generation, delete, patch.

Files land at ATTACHMENTS_DIR/<recipe_id>/<uuid>.<ext>; WebP thumbs at
ATTACHMENTS_DIR/thumbs/<recipe_id>/<uuid>.webp. The reverse proxy serves
both directories at /attachments/* — FastAPI does not.

EXIF rotation is applied via ImageOps.exif_transpose so portrait photos from
phones land the right way up. HEIC support is registered at module import.
"""

import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import pillow_heif
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from PIL import Image, ImageOps
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models import Recipe, RecipeAttachment

logger = logging.getLogger(__name__)
pillow_heif.register_heif_opener()

router = APIRouter(prefix="/recipes", tags=["attachments"])


ALLOWED_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}

THUMB_SIZE = (400, 400)
THUMB_QUALITY = 82
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB raw upload cap


def _attachments_root() -> Path:
    return Path(os.getenv("ATTACHMENTS_DIR", "/app/data/attachments"))


def _ensure_recipe(session: Session, recipe_id: int) -> Recipe:
    recipe = session.get(Recipe, recipe_id)
    if not recipe or not recipe.active:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return recipe


class AttachmentPatch(BaseModel):
    caption: Optional[str] = None
    sort_order: Optional[int] = None


def _serialise(att: RecipeAttachment) -> dict:
    stem = att.filename.rsplit(".", 1)[0]
    return {
        "id": att.id,
        "recipe_id": att.recipe_id,
        "kind": att.kind,
        "filename": att.filename,
        "mime_type": att.mime_type,
        "caption": att.caption,
        "sort_order": att.sort_order,
        "created_at": att.created_at,
        "url": f"/attachments/{att.recipe_id}/{att.filename}",
        "thumb_url": f"/attachments/thumbs/{att.recipe_id}/{stem}.webp",
    }


@router.post("/{recipe_id}/attachments", status_code=201)
async def upload_attachment(
    recipe_id: int,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    sort_order: int = Form(default=0),
    session: Session = Depends(get_session),
):
    _ensure_recipe(session, recipe_id)

    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported media type: {file.content_type}",
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")

    ext = ALLOWED_MIME[file.content_type]
    stem = uuid.uuid4().hex
    filename = f"{stem}.{ext}"

    root = _attachments_root()
    photo_dir = root / str(recipe_id)
    thumb_dir = root / "thumbs" / str(recipe_id)
    photo_dir.mkdir(parents=True, exist_ok=True)
    thumb_dir.mkdir(parents=True, exist_ok=True)

    photo_path = photo_dir / filename
    thumb_path = thumb_dir / f"{stem}.webp"

    photo_path.write_bytes(raw)

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE)
            img.save(thumb_path, format="WEBP", quality=THUMB_QUALITY)
    except Exception as exc:
        photo_path.unlink(missing_ok=True)
        thumb_path.unlink(missing_ok=True)
        logger.exception("Thumb generation failed for recipe %s", recipe_id)
        raise HTTPException(
            status_code=400,
            detail=f"Could not process image: {exc}",
        ) from exc

    att = RecipeAttachment(
        recipe_id=recipe_id,
        kind="photo",
        filename=filename,
        mime_type=file.content_type,
        caption=caption,
        sort_order=sort_order,
    )
    session.add(att)
    session.commit()
    session.refresh(att)
    return _serialise(att)


@router.patch("/{recipe_id}/attachments/{att_id}")
def patch_attachment(
    recipe_id: int,
    att_id: int,
    payload: AttachmentPatch,
    session: Session = Depends(get_session),
):
    _ensure_recipe(session, recipe_id)
    att = session.get(RecipeAttachment, att_id)
    if not att or att.recipe_id != recipe_id:
        raise HTTPException(status_code=404, detail="Attachment not found")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(att, key, value)
    session.add(att)
    session.commit()
    session.refresh(att)
    return _serialise(att)


@router.delete("/{recipe_id}/attachments/{att_id}")
def delete_attachment(
    recipe_id: int,
    att_id: int,
    session: Session = Depends(get_session),
):
    _ensure_recipe(session, recipe_id)
    att = session.exec(
        select(RecipeAttachment)
        .where(RecipeAttachment.id == att_id)
        .where(RecipeAttachment.recipe_id == recipe_id)
    ).first()
    if not att:
        raise HTTPException(status_code=404, detail="Attachment not found")

    root = _attachments_root()
    stem = att.filename.rsplit(".", 1)[0]
    (root / str(recipe_id) / att.filename).unlink(missing_ok=True)
    (root / "thumbs" / str(recipe_id) / f"{stem}.webp").unlink(missing_ok=True)

    session.delete(att)
    session.commit()
    return {"detail": "Attachment deleted", "id": att_id}
