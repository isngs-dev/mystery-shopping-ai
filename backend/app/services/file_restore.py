"""Read a stored PDF; if it is missing from the storage disk, fetch the original again from the Drive file it was imported from.

A PDF can disappear from the disk when the host has no persistent disk (the database keeps the record, the file is gone). The Drive
sync repairs that in bulk; this module repairs it on demand, the moment somebody opens the PDF or QC needs it. Only byte-identical
content is restored (the stored SHA-256 must match), so the immutable RAW is never replaced by something different.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Document, DriveFile
from ..storage import get_storage
from .drive_sources import GoogleDriveSource, LocalSource, Node
from .repository import sha256_of

log = logging.getLogger("msai.file_restore")


class FileMissing(RuntimeError):
    """The PDF is not on the storage disk and could not be fetched again."""


def restore_document(db: Session, doc: Document) -> bool:
    """Try to fetch the Drive original of `doc` back onto the disk. Returns True when the file is there afterwards."""
    storage = get_storage()
    if storage.exists(doc.storage_key):
        return True
    rows = db.execute(select(DriveFile).where(DriveFile.document_id == doc.id)).scalars().all()
    for row in rows:
        kind, _, key = (row.source_key or "").partition(":")
        if kind not in ("google_drive", "local") or not key:
            continue
        src = None
        try:
            src = GoogleDriveSource(key) if kind == "google_drive" else LocalSource(key)       # both refuse anything outside their folder
            data = src.read(Node(row.drive_id, row.path.rsplit("/", 1)[-1], False, row.size, "", row.version))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch %s again from Drive: %s", doc.stored_name, exc)
            continue
        finally:
            if src is not None:
                src.close()
        if sha256_of(data) != doc.sha256:
            log.warning("%s: the Drive file has changed since it was imported; the original cannot be restored", doc.stored_name)
            continue
        storage.put_immutable(doc.storage_key, data)
        log.info("Restored %s from Drive", doc.stored_name)
        return True
    return False


def read_document(db: Session, doc: Document) -> bytes:
    """The PDF's bytes, restoring them from Drive first if the file is missing."""
    storage = get_storage()
    try:
        return storage.get(doc.storage_key)
    except FileNotFoundError:
        pass
    if restore_document(db, doc):
        return storage.get(doc.storage_key)
    raise FileMissing(f"{doc.stored_name} is missing from storage and could not be fetched again from Google Drive "
                      "(is Drive connected, and does the server have a persistent disk?).")
