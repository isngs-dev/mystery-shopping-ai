"""Generates SAMPLE RAW/edited PDF pairs with known, deliberately injected issues.
Used by the demo seed, the sample-documents folder and the automated tests. All content is fictional."""
from __future__ import annotations

import pymupdf

NARRATIVE = [
    "I entered the store at 10:30 AM and was greeted by an associate within one minute.",
    "The associate was wearing a name badge and asked how she could help me.",
    "I asked about the premium blender priced at $89.99 and she demonstrated it for me.",
    "The store was clean and the shelves were well stocked.",
    "I paid with a card and received a printed receipt at the register.",
    "The cashier thanked me and invited me to return soon.",
]
HEADER = ["Store Name: Riverside Mart #{n}", "Date of Visit: 12 March 2026", "Shopper ID: SH-{n}"]


def _render(lines: list[str], title: str = "Mystery Shopping Visit Report", pages: int = 1) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 60), title, fontsize=16, fontname="helv")
    y = 100
    for ln in lines:
        if ln == "\f":  # page break
            page = doc.new_page(width=595, height=842)
            y = 60
            continue
        # wrap manually at ~88 chars so words keep real coordinates
        words, cur = ln.split(" "), ""
        for w in words:
            if len(cur) + len(w) + 1 > 88:
                page.insert_text((50, y), cur, fontsize=10.5, fontname="helv")
                y += 15
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            page.insert_text((50, y), cur, fontsize=10.5, fontname="helv")
        y += 20
    data = doc.tobytes()
    doc.close()
    return data


def make_pair(n: int = 1, scenario: str = "mixed") -> tuple[bytes, bytes, dict]:
    """Return (raw_pdf, edited_pdf, expected) for a scenario.

    scenarios:
      clean   - edits fix everything; no issues expected
      mixed   - one missed typo, one new typo, one changed amount, one removed sentence, one repeated word
      pii     - editor leaves a shopper email in the report
      missing - required field value removed in the edit
    """
    header = [h.format(n=100 + n) for h in HEADER]
    raw_body = list(NARRATIVE)
    raw_body[1] = "The associate was wearing a name badge and asked how she could help mee today."  # RAW typo (will be fixed)
    raw_body[3] = "The store was clean and the shelves were well stocked but the recieved goods area was messy."  # missed typo
    edited_body = list(NARRATIVE)
    edited_body[3] = "The store was clean and the shelves were well stocked but the received goods area was messy."  # corrected
    expected: dict = {}
    if scenario == "clean":
        pass
    elif scenario == "mixed":
        edited_body[3] = "The store was clean and the shelves were well stocked but the recieved goods area was messy."  # missed
        edited_body[2] = "I asked about the premium blender priced at $98.99 and she demonstrated it for me."  # number changed
        edited_body[4] = "I paid with a card and and received a printed receipt at the registar."  # repeated word + new typo
        edited_body[5] = ""  # sentence removed (omission)
        expected = {"missed_spelling": "recieved", "new_spelling": "registar", "numbers": ("$89.99", "$98.99"), "repeated": "and"}
    elif scenario == "pii":
        edited_body[5] = "The cashier thanked me and invited me to return soon. Contact the shopper at jane.doe@example.com for details."
        expected = {"pii": "jane.doe@example.com"}
    elif scenario == "missing":
        header_edit = list(header)
        header_edit[1] = "Date of Visit:"
        raw = _render(header + [""] + raw_body)
        edited = _render(header_edit + [""] + edited_body)
        return raw, edited, {"missing_label": "Date of Visit"}
    raw = _render(header + [""] + raw_body)
    edited = _render(header + [""] + [b for b in edited_body if b != ""] if scenario == "mixed" else header + [""] + edited_body)
    return raw, edited, expected


def make_scanned_pdf() -> bytes:
    """An image-only PDF (no text layer) to exercise the OCR / extraction-failure path."""
    src = pymupdf.open()
    p = src.new_page(width=300, height=200)
    p.insert_text((20, 80), "SCANNED PAGE SAMPLE", fontsize=18)
    pix = p.get_pixmap(dpi=100)
    out = pymupdf.open()
    page = out.new_page(width=300, height=200)
    page.insert_image(page.rect, stream=pix.tobytes("png"))
    data = out.tobytes()
    out.close()
    src.close()
    return data
