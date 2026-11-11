"""PDF validation and text extraction (PyMuPDF), with OCR (Tesseract) for scanned pages.

Nothing here executes document content; PDFs are parsed as untrusted data only.
Extraction failures are recorded, never papered over.
"""
from __future__ import annotations

import io
import shutil
from dataclasses import dataclass, field

import pymupdf

from ..config import get_settings


@dataclass
class PageData:
    page_no: int
    text: str = ""
    words: list = field(default_factory=list)  # [x0, y0, x1, y1, word]
    width: float = 0.0
    height: float = 0.0
    method: str = "text"  # text | ocr | none
    ocr_confidence: float | None = None
    note: str | None = None


@dataclass
class ExtractionResult:
    status: str  # ok | partial | failed | ocr_unavailable
    pages: list[PageData]
    summary: dict


def ocr_status() -> dict:
    s = get_settings()
    if not s.ocr_enabled:
        return {"available": False, "reason": "OCR disabled by configuration (OCR_ENABLED=false)."}
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return {"available": False, "reason": "pytesseract is not installed."}
    cmd = s.tesseract_cmd or shutil.which("tesseract")
    if not cmd:
        return {"available": False, "reason": "Tesseract binary not found on PATH (set TESSERACT_CMD)."}
    return {"available": True, "engine": "tesseract", "command": cmd}


@dataclass
class Validation:
    ok: bool
    status: str  # stored | invalid | unreadable
    page_count: int | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def validate_pdf(data: bytes) -> Validation:
    s = get_settings()
    if not data:
        return Validation(False, "invalid", error="File is empty.")
    if b"%PDF-" not in data[:1024]:
        return Validation(False, "invalid", error="File is not a PDF (missing %PDF header).")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        return Validation(False, "unreadable", error=f"PDF could not be opened: {exc}")
    try:
        if doc.needs_pass:
            return Validation(False, "unreadable", error="PDF is password-protected/encrypted.")
        n = doc.page_count
        if n < 1:
            return Validation(False, "invalid", error="PDF has no pages.")
        if n > s.max_pages:
            return Validation(False, "invalid", page_count=n, error=f"PDF has {n} pages (limit {s.max_pages}).")
        try:
            doc.load_page(0).get_text()
        except Exception as exc:  # noqa: BLE001
            return Validation(False, "unreadable", page_count=n, error=f"First page unreadable: {exc}")
        warnings = []
        head = data
        if b"/JavaScript" in head or b"/JS " in head or b"/Launch" in head:
            warnings.append("Contains active content (JavaScript/Launch actions); it is never executed.")
        return Validation(True, "stored", page_count=n, warnings=warnings)
    finally:
        doc.close()


def _group_lines(raw: list) -> list[list]:
    """Group PyMuPDF word tuples into visual lines (top to bottom, left to right). Geometric ordering means a
    page keeps the same reading order even after its text has been re-typeset by the AI editor."""
    ws = sorted(raw, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[dict] = []
    for w in ws:
        cy, h = (w[1] + w[3]) / 2, max(w[3] - w[1], 1.0)
        if lines and abs(cy - lines[-1]["cy"]) <= max(2.0, 0.5 * min(h, lines[-1]["h"])):
            ln = lines[-1]
            ln["words"].append(w)
            ln["cy"] = sum((x[1] + x[3]) / 2 for x in ln["words"]) / len(ln["words"])
            ln["h"] = min(ln["h"], h)
        else:
            lines.append({"cy": cy, "h": h, "words": [w]})
    return [sorted(ln["words"], key=lambda w: w[0]) for ln in lines]


def _page_from_text(page, page_no: int) -> PageData:
    rect = page.rect
    lines = _group_lines(page.get_text("words"))
    words = [[round(w[0], 2), round(w[1], 2), round(w[2], 2), round(w[3], 2), w[4]] for ln in lines for w in ln]
    text = "\n".join(" ".join(w[4] for w in ln) for ln in lines)
    return PageData(page_no, text, words, rect.width, rect.height, "text")


def _ocr_page(page, page_no: int) -> PageData:
    import pytesseract
    from PIL import Image

    s = get_settings()
    if s.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = s.tesseract_cmd
    pix = page.get_pixmap(dpi=s.ocr_dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    d = pytesseract.image_to_data(img, lang=s.ocr_language, output_type=pytesseract.Output.DICT)
    scale = 72.0 / s.ocr_dpi
    words, confs, lines, cur, cur_key = [], [], [], [], None
    for i, txt in enumerate(d["text"]):
        txt = (txt or "").strip()
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not txt or conf < 0:
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        words.append([round(x * scale, 2), round(y * scale, 2), round((x + w) * scale, 2), round((y + h) * scale, 2), txt])
        confs.append(conf)
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        if cur_key is not None and key != cur_key:
            lines.append(" ".join(cur))
            cur = []
        cur.append(txt)
        cur_key = key
    if cur:
        lines.append(" ".join(cur))
    avg = (sum(confs) / len(confs) / 100.0) if confs else None
    note = None
    if avg is not None and avg < 0.8:
        note = f"Low OCR confidence ({avg:.0%}); text may contain recognition errors."
    return PageData(page_no, "\n".join(lines), words, page.rect.width, page.rect.height, "ocr", avg, note)


def extract_pdf(data: bytes) -> ExtractionResult:
    s = get_settings()
    ocr = ocr_status()
    pages: list[PageData] = []
    doc = pymupdf.open(stream=data, filetype="pdf")
    failed = ocr_needed_unavailable = ocr_pages = text_pages = blank = 0
    try:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            try:
                pd = _page_from_text(page, i + 1)
            except Exception as exc:  # noqa: BLE001
                pages.append(PageData(i + 1, method="none", note=f"Text extraction failed: {exc}"))
                failed += 1
                continue
            if len(pd.text.strip()) >= s.min_chars_per_page_for_text:
                text_pages += 1
                pages.append(pd)
                continue
            has_images = bool(page.get_images(full=False))
            if not has_images and not pd.text.strip():
                pd.method, pd.note = "none", "Blank page (no text or images)."
                blank += 1
                pages.append(pd)
                continue
            # Little/no text but images present => treat as scanned.
            if ocr["available"]:
                try:
                    op = _ocr_page(page, i + 1)
                    pages.append(op)
                    ocr_pages += 1
                    if not op.text.strip():
                        op.note = (op.note or "") + " OCR found no text."
                        failed += 1
                except Exception as exc:  # noqa: BLE001
                    pd.method, pd.note = "none", f"OCR failed: {exc}"
                    pages.append(pd)
                    failed += 1
            else:
                pd.method = "none"
                pd.note = f"Scanned page; OCR unavailable ({ocr['reason']})"
                pages.append(pd)
                ocr_needed_unavailable += 1
    finally:
        doc.close()

    confs = [p.ocr_confidence for p in pages if p.ocr_confidence is not None]
    summary = {
        "pages": len(pages), "text_pages": text_pages, "ocr_pages": ocr_pages, "blank_pages": blank,
        "failed_pages": failed, "ocr_unavailable_pages": ocr_needed_unavailable,
        "avg_ocr_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "low_confidence_pages": [p.page_no for p in pages if p.note and p.note.startswith("Low OCR")],
        "ocr": ocr, "order": "geometric",
    }
    total_text = sum(len(p.text.strip()) for p in pages)
    if ocr_needed_unavailable:
        status = "ocr_unavailable"
    elif failed and total_text == 0:
        status = "failed"
    elif failed or summary["low_confidence_pages"]:
        status = "partial"
    elif total_text == 0:
        status = "failed"
        summary["note"] = "No extractable text found."
    else:
        status = "ok"
    return ExtractionResult(status, pages, summary)
