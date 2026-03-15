"""Sources router — read-only access to the source registry."""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import Source

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(session: Session = Depends(get_session)):
    sources = session.exec(select(Source)).all()
    return sources


@router.get("/{source_id}")
def get_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source
