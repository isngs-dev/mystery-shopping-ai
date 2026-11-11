"""Naming convention: FORM001_1.pdf = original RAW PDF, FORM001_2.pdf = edited PDF submitted for QC."""
import re
from datetime import datetime

FILENAME_RE = re.compile(r"^(?P<form>[A-Za-z0-9][A-Za-z0-9\-]{0,39})_(?P<n>[12])\.pdf$", re.IGNORECASE)
FORM_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,39}$")

KIND_SUFFIX = {"raw": 1, "edited": 2}


def normalize_form_code(code: str) -> str:
    return code.strip().upper()


def parse_filename(name: str) -> tuple[str, str] | None:
    """Return (FORM_CODE, kind) or None when the name violates the convention."""
    m = FILENAME_RE.match(name.strip())
    if not m:
        return None
    return normalize_form_code(m.group("form")), ("raw" if m.group("n") == "1" else "edited")


def logical_name(form_code: str, kind: str) -> str:
    return f"{form_code}_{KIND_SUFFIX[kind]}.pdf"


def stored_name(form_code: str, kind: str, version_no: int, when: datetime) -> str:
    """Raw and the first edited file keep the canonical name. Resubmissions are versioned + timestamped so no
    historical document is ever overwritten."""
    if kind == "raw" or version_no == 1:
        return logical_name(form_code, kind)
    return f"{form_code}_{KIND_SUFFIX[kind]}_r{version_no}_{when.strftime('%Y%m%dT%H%M%SZ')}.pdf"
