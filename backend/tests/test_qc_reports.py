"""The Excel QC report of every project is kept up to date on disk by itself as forms arrive and QC runs."""
from openpyxl import load_workbook

from app.config import get_settings
from app.sample_docs import make_pair
from app.services import qc_reports

from .helpers import intake, upload


def _rows(path):
    ws = load_workbook(path)["QC Report"]
    return {ws.cell(i, 4).value: {ws.cell(1, k).value: ws.cell(i, k).value for k in range(1, 24)} for i in range(2, ws.max_row + 1)}


def test_project_excel_file_fills_itself_as_forms_arrive(api, ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "qc_reports_dir", str(tmp_path))
    monkeypatch.setattr(type(qc_reports.worker), "running", property(lambda self: True))       # the worker thread is off in tests: drive it by hand
    qc_reports.worker._dirty.clear()
    assert api("manager").put(f"/api/qc/projects/{ctx['p1']}/reviewer", json={"reviewer_id": ctx["users"]["qc"]}).status_code == 200

    # 1. a pair arrives, QC runs by itself -> the project is marked dirty and its workbook gets the form
    intake(api, ctx, code="FORM001")
    assert ctx["p1"] in qc_reports.worker._dirty
    assert qc_reports.worker.run_once() >= 1
    files = list(tmp_path.rglob("*.xlsx"))
    assert [f.name for f in files] == ["Store Visits.xlsx"] and files[0].parent.name == "Acme Retail"
    rows = _rows(files[0])
    assert set(rows) == {"FORM001"} and rows["FORM001"]["QC Status"] == "Request Revision" and rows["FORM001"]["QC Member"] == "QC"
    assert rows["FORM001"]["Editor"] == "iSN Team" and rows["FORM001"]["Feedback"].startswith("QC score")

    # 2. a second form arrives: only a RAW first (row appears, waiting), then its edited PDF (QC runs, row is filled)
    mgr, ed = api("manager"), api("editor")
    raw, edited, _ = make_pair(2, "mixed")
    fid = upload(mgr, ctx["p1"], "FORM002_1.pdf", raw).json()["results"][0]["form_id"]
    assert qc_reports.worker.run_once() >= 1
    assert set(_rows(files[0])) == {"FORM001", "FORM002"} and "Waiting for the edited PDF" in _rows(files[0])["FORM002"]["Feedback"]
    mgr.post(f"/api/forms/{fid}/assign", json={"editor_id": ctx["users"]["editor"]})
    ed.post(f"/api/forms/{fid}/start")
    upload(ed, ctx["p1"], "FORM002_2.pdf", edited)
    assert qc_reports.worker.run_once() >= 1
    assert _rows(files[0])["FORM002"]["QC Status"] == "Request Revision"

    # 3. a reviewer rejects every finding of FORM001 -> its row turns Accepted by itself
    qc = api("qc")
    for f in qc.get(f"/api/forms/{[i for i in range(1, 5) if qc.get(f'/api/forms/{i}').json().get('form_code') == 'FORM001'][0]}/qc").json()["findings"]:
        qc.post(f"/api/findings/{f['id']}/review", json={"action": "reject"})
    assert qc_reports.worker.run_once() >= 1
    assert _rows(files[0])["FORM001"]["QC Status"] == "Accepted" and not _rows(files[0])["FORM001"]["Error Category"]

    # nothing changed -> nothing is rewritten
    assert qc_reports.worker.run_once() == 0


def test_a_locked_workbook_is_retried_not_lost(api, ctx, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "qc_reports_dir", str(tmp_path))
    calls = []
    real = qc_reports.write_project

    def flaky(pid):
        calls.append(pid)
        if len(calls) == 1:
            raise PermissionError("file is open in Excel")
        return real(pid)

    monkeypatch.setattr(qc_reports, "write_project", flaky)
    w = qc_reports.Worker()
    w._dirty.add(ctx["p1"])
    assert w.run_once() == 0 and w.pending() == 1           # failed, stays queued
    assert w.run_once() == 1 and w.pending() == 0 and list(tmp_path.rglob("*.xlsx"))
