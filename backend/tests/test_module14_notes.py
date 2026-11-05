"""Module 14 — Notes create/visibility/ownership/cross-campus/batch rename/files."""
from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

from app.auth.access import rename_batch_cascade
from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.notes.models import ClassNote
from tests.conftest import _TestSessionFactory


MIN_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pdf():
    return ("notes.pdf", BytesIO(MIN_PDF), "application/pdf")


def _mk(role: str, branch_id: str, *, batch: str | None = None, suffix: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M14 {branch_id}", city="M14", is_active=True))
        tag = suffix or uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m14_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m14.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id,
            batch=batch,
            is_active=True,
        )
        db.add(row)
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name=batch))
        db.commit()
        token = create_access_token(sub=row.id, role=role, extra={"branch_id": branch_id})
        return {"id": row.id, "token": token, "batch": batch, "branch_id": branch_id}
    finally:
        db.close()


def _create(client, teacher: dict, title: str = "M14 Note", monkeypatch=None, tmp_path=None) -> dict:
    res = client.post(
        "/api/notes/teacher",
        headers=_hdr(teacher["token"]),
        data={"title": title, "description": "desc"},
        files={"file": _pdf()},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_student_cannot_create_note(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m14-c-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    denied = client.post(
        "/api/notes/teacher",
        headers=_hdr(stu["token"]),
        data={"title": "Nope", "description": ""},
        files={"file": _pdf()},
    )
    assert denied.status_code == 403


def test_create_and_cross_campus_batch_denied(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)
    monkeypatch.setattr("app.notes.controllers.render_page", lambda path, page, mark: b"ok")

    a = f"m14-a-{uuid.uuid4().hex[:6]}"
    b = f"m14-b-{uuid.uuid4().hex[:6]}"
    tea_a = _mk("teacher", a)
    tea_b = _mk("teacher", b)
    stu_a = _mk("student", a, batch="Morning")
    stu_b = _mk("student", b, batch="Morning")

    note = _create(client, tea_a)
    db = _TestSessionFactory()
    try:
        row = db.query(ClassNote).filter_by(id=note["id"]).first()
        assert row is not None
        assert row.created_by == tea_a["id"]
    finally:
        db.close()

    # Foreign batch label on unlock
    bad_batch = client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(tea_a["token"]),
        json={"unlocked_batches": ["Does Not Exist"], "unlocked_student_ids": []},
    )
    assert bad_batch.status_code == 400

    # Foreign student unlock
    foreign_stu = client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(tea_a["token"]),
        json={"unlocked_batches": [], "unlocked_student_ids": [stu_b["id"]]},
    )
    assert foreign_stu.status_code == 403

    ok = client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(tea_a["token"]),
        json={"unlocked_batches": ["Morning"], "unlocked_student_ids": []},
    )
    assert ok.status_code == 200

    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu_a["token"])).status_code == 200
    # Same batch name, foreign campus
    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu_b["token"])).status_code == 403

    # Header spoof cannot unlock Branch B student
    spoof = client.get(
        f"/api/notes/student/{note['id']}",
        headers={**_hdr(stu_b["token"]), "X-Student-Batch": "Morning", "X-Student-Id": stu_a["id"]},
    )
    assert spoof.status_code == 403

    # Teacher B cannot edit/delete Teacher A note
    assert (
        client.put(
            f"/api/notes/teacher/{note['id']}",
            headers=_hdr(tea_b["token"]),
            data={"title": "Hijack", "description": ""},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/notes/teacher/{note['id']}", headers=_hdr(tea_b["token"])).status_code == 403


def test_direct_student_unlock_and_edit(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)

    branch = f"m14-d-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    s1 = _mk("student", branch, batch="Alpha")
    s2 = _mk("student", branch, batch="Alpha")
    note = _create(client, teacher)
    client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": [], "unlocked_student_ids": [s1["id"]]},
    )
    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(s1["token"])).status_code == 200
    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(s2["token"])).status_code == 403

    edited = client.put(
        f"/api/notes/teacher/{note['id']}",
        headers=_hdr(teacher["token"]),
        data={"title": "Updated Title", "description": "new"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "Updated Title"
    db = _TestSessionFactory()
    try:
        assert db.query(ClassNote).filter_by(id=note["id"]).first().title == "Updated Title"
    finally:
        db.close()


def test_batch_rename_preserves_visibility(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)

    branch = f"m14-r-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Old Morning")
    note = _create(client, teacher)
    client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": ["Old Morning"], "unlocked_student_ids": []},
    )
    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu["token"])).status_code == 200

    db = _TestSessionFactory()
    try:
        bat = db.query(Batch).filter_by(branch_id=branch, name="Old Morning").first()
        assert bat is not None
        rename_batch_cascade(db, bat, "New Morning")
        db.commit()
        row = db.query(ClassNote).filter_by(id=note["id"]).first()
        assert "New Morning" in row.unlocked_batches
        assert "Old Morning" not in row.unlocked_batches
        u = db.query(User).filter_by(id=stu["id"]).first()
        assert u.batch == "New Morning"
    finally:
        db.close()

    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu["token"])).status_code == 200


def test_student_move_batch_current_membership(client, monkeypatch, tmp_path):
    """Notes follow current batch membership — leave Batch A → lose Batch A notes."""
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)

    branch = f"m14-m-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Batch A")
    # Ensure Batch B catalog exists
    _mk("student", branch, batch="Batch B", suffix=uuid.uuid4().hex[:8])
    note = _create(client, teacher)
    client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": ["Batch A"], "unlocked_student_ids": []},
    )
    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu["token"])).status_code == 200

    db = _TestSessionFactory()
    try:
        u = db.query(User).filter_by(id=stu["id"]).first()
        u.batch = "Batch B"
        db.commit()
    finally:
        db.close()

    assert client.get(f"/api/notes/student/{note['id']}", headers=_hdr(stu["token"])).status_code == 403


def test_path_traversal_and_delete_cleans_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)

    branch = f"m14-f-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    note = _create(client, teacher)
    stored = Path(tmp_path) / note.get("original_name", "")
    # Find actual stored file
    db = _TestSessionFactory()
    try:
        row = db.query(ClassNote).filter_by(id=note["id"]).first()
        path = Path(tmp_path) / row.stored_name
        assert path.exists()
        # Inject traversal into stored_name — file endpoint must reject
        row.stored_name = "../secret.pdf"
        db.commit()
    finally:
        db.close()

    bad = client.get(f"/api/notes/teacher/{note['id']}/file", headers=_hdr(teacher["token"]))
    assert bad.status_code in (400, 404)

    # Restore and delete cleanly
    db = _TestSessionFactory()
    try:
        row = db.query(ClassNote).filter_by(id=note["id"]).first()
        # Recreate a safe file for delete test
        row.stored_name = f"{uuid.uuid4().hex}.pdf"
        (Path(tmp_path) / row.stored_name).write_bytes(MIN_PDF)
        stored_name = row.stored_name
        db.commit()
    finally:
        db.close()

    assert client.delete(f"/api/notes/teacher/{note['id']}", headers=_hdr(teacher["token"])).status_code == 200
    assert not (Path(tmp_path) / stored_name).exists()
    db = _TestSessionFactory()
    try:
        assert db.query(ClassNote).filter_by(id=note["id"]).first() is None
    finally:
        db.close()


def test_inactive_student_denied_note_preserved(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)

    branch = f"m14-i-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning")
    note = _create(client, teacher)
    client.put(
        f"/api/notes/teacher/{note['id']}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": ["Morning"], "unlocked_student_ids": []},
    )
    db = _TestSessionFactory()
    try:
        u = db.query(User).filter_by(id=stu["id"]).first()
        u.is_active = False
        db.commit()
    finally:
        db.close()
    assert client.get("/api/notes/student", headers=_hdr(stu["token"])).status_code == 403
    db = _TestSessionFactory()
    try:
        assert db.query(ClassNote).filter_by(id=note["id"]).first() is not None
    finally:
        db.close()
