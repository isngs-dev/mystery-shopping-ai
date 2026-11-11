from app.sample_docs import make_pair


def upload(a, project_id, filename, data, **fields):
    return a.post("/api/repository/upload", data={"project_id": str(project_id), **fields},
                  files=[("files", (filename, data, "application/pdf"))])


def intake(api, ctx, code="FORM001", scenario="mixed", assign=True, start=True, submit=True):
    """RAW upload by manager, assignment, editor start + edited upload (QC runs inline in tests)."""
    raw, edited, expected = make_pair(1, scenario)
    mgr = api("manager")
    r = upload(mgr, ctx["p1"], f"{code}_1.pdf", raw)
    assert r.json()["uploaded"] == 1, r.text
    form_id = r.json()["results"][0]["form_id"]
    if assign:
        r = mgr.post(f"/api/forms/{form_id}/assign", json={"editor_id": ctx["users"]["editor"], "reviewer_id": ctx["users"]["qc"]})
        assert r.status_code == 200, r.text
    ed = api("editor")
    if start:
        assert ed.post(f"/api/forms/{form_id}/start").status_code == 200
    if submit:
        r = upload(ed, ctx["p1"], f"{code}_2.pdf", edited)
        assert r.json()["uploaded"] == 1, r.text
    return form_id, expected, (raw, edited)
