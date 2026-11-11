"""Recordings linked to forms: list, add by Drive link, remove, and authenticated Range streaming."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..audit import audit
from ..db import get_db
from ..deps import needs
from ..models import MediaLink, User
from ..permissions import get_form_or_404
from ..serializers import iso
from ..services import media
from ..services.drive_sources import SourceError

router = APIRouter(prefix="/api", tags=["media"])


def media_out(m: MediaLink) -> dict:
    return {"id": m.id, "form_id": m.form_id, "title": m.title, "kind": m.kind, "mime": m.mime, "size": m.size, "source": m.source,
            "stream_url": f"/api/media/{m.id}/stream", "drive_file_id": m.drive_file_id,
            "preview_url": f"https://drive.google.com/file/d/{m.drive_file_id}/preview" if m.drive_file_id else None,
            "open_url": f"https://drive.google.com/file/d/{m.drive_file_id}/view" if m.drive_file_id else None, "created_at": iso(m.created_at)}


@router.get("/forms/{form_id}/media")
def list_media(form_id: int, user: User = Depends(needs("media.view")), db: Session = Depends(get_db)):
    form = get_form_or_404(db, user, form_id)
    rows = db.execute(select(MediaLink).where(MediaLink.form_id == form.id).order_by(MediaLink.id)).scalars().all()
    return [media_out(m) for m in rows]


class LinkIn(BaseModel):
    url: str = Field(min_length=10, max_length=800)
    title: str | None = Field(default=None, max_length=200)
    kind: str = Field(default="video", pattern="^(video|audio)$")


@router.post("/forms/{form_id}/media", status_code=201)
def add_media(form_id: int, body: LinkIn, request: Request, user: User = Depends(needs("media.manage")), db: Session = Depends(get_db)):
    """Attach a Google Drive link (file) as the recording of this form."""
    form = get_form_or_404(db, user, form_id)
    fid = media.drive_file_id(body.url)
    if not fid:
        raise HTTPException(422, "Paste a Google Drive file link (https://drive.google.com/file/d/…). Folder links and other sites are not accepted.")
    row, new = media.attach(db, form, title=(body.title or f"Recording for {form.form_code}").strip(), kind=body.kind, source="manual",
                            origin_key=f"drive:{fid}", drive_id=fid, url=body.url.strip(), user=user)
    if not new:
        raise HTTPException(409, "This recording is already linked to the form.")
    audit(db, user, "media.linked", entity_type="media", entity_id=row.id, form_id=form.id, project_id=form.project_id, detail={"drive_file_id": fid}, request=request)
    db.commit()
    return media_out(row)


@router.delete("/media/{media_id}")
def remove_media(media_id: int, request: Request, user: User = Depends(needs("media.manage")), db: Session = Depends(get_db)):
    row = db.get(MediaLink, media_id)
    if not row:
        raise HTTPException(404, "Recording not found.")
    form = get_form_or_404(db, user, row.form_id)
    db.delete(row)         # only the link is removed; the file in Drive is untouched
    audit(db, user, "media.unlinked", entity_type="media", entity_id=media_id, form_id=form.id, project_id=form.project_id, request=request)
    db.commit()
    return {"ok": True}


@router.get("/media/{media_id}/stream")
def stream(media_id: int, request: Request, user: User = Depends(needs("media.view")), db: Session = Depends(get_db)):
    """Stream the recording (HTTP Range supported, so the player can seek). Access follows the form's access rules."""
    row = db.get(MediaLink, media_id)
    if not row:
        raise HTTPException(404, "Recording not found.")
    form = get_form_or_404(db, user, row.form_id)
    rng = request.headers.get("range", "")
    if not rng or rng.startswith("bytes=0-"):      # log the start of a playback, not every seek
        audit(db, user, "media.played", entity_type="media", entity_id=row.id, form_id=form.id, project_id=form.project_id)
        db.commit()
    try:
        if row.local_path:
            return FileResponse(media.resolve_local(row.local_path), media_type=row.mime or None,
                                headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
        if row.drive_file_id:
            client, upstream = media.open_drive_stream(row.drive_file_id, request.headers.get("range"))
            if upstream.status_code >= 400:
                upstream.close()
                client.close()
                raise HTTPException(404 if upstream.status_code in (403, 404) else 502,
                                    "Google Drive did not provide this file to the service account. Share the file/folder with it, or use “Open in Drive”.")
            keep = {k: v for k, v in upstream.headers.items() if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
            keep.setdefault("accept-ranges", "bytes")
            keep.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
            return StreamingResponse(upstream.iter_raw(), status_code=upstream.status_code, headers=keep,
                                     background=BackgroundTask(lambda: (upstream.close(), client.close())))
    except SourceError as exc:
        raise HTTPException(409, str(exc)) from exc
    raise HTTPException(404, "This recording has no playable source.")
