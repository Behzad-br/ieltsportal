"""Class notes lock/unlock and student page access.

Updated for Module 14: batch visibility uses DB User.batch + creator campus,
not X-Student-Batch headers. Legacy header-only unlock is intentionally denied
when AUTH_LEGACY_HEADERS=false (staging/production).
"""
from io import BytesIO
import uuid

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
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


def _pdf_file():
    return ("notes.pdf", BytesIO(MIN_PDF), "application/pdf")


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"Notes {branch_id}", city="N", is_active=True))
        tag = uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"notes_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@notes.test",
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
        return {"id": row.id, "token": token, "batch": batch}
    finally:
        db.close()


def test_student_cannot_read_locked_notes(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 2)
    monkeypatch.setattr("app.notes.controllers.render_page", lambda path, page, mark: b"png-bytes")

    branch = f"notes-a-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    stu = _mk("student", branch, batch="Morning Batch A")
    other = _mk("student", branch, batch="Morning Batch B")

    created = client.post(
        "/api/notes/teacher",
        headers=_hdr(teacher["token"]),
        data={"title": "Writing vocabulary", "description": "Week 1"},
        files={"file": _pdf_file()},
    )
    assert created.status_code == 200, created.text
    note_id = created.json()["id"]

    locked = client.get(f"/api/notes/student/{note_id}/page/1", headers=_hdr(stu["token"]))
    assert locked.status_code == 403

    # Spoofed batch header must not unlock.
    spoof = client.get(
        f"/api/notes/student/{note_id}/page/1",
        headers={
            **_hdr(stu["token"]),
            "X-Student-Batch": "Morning Batch A",
            "X-Student-Id": "fake",
        },
    )
    assert spoof.status_code == 403

    access = client.put(
        f"/api/notes/teacher/{note_id}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": ["Morning Batch A"], "unlocked_student_ids": []},
    )
    assert access.status_code == 200
    assert "Morning Batch A" in access.json()["unlocked_batches"]

    page = client.get(
        f"/api/notes/student/{note_id}/page/1",
        headers={**_hdr(stu["token"]), "X-Student-Name": "Ali Ahmad"},
    )
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.content == b"png-bytes"

    other_denied = client.get(f"/api/notes/student/{note_id}/page/1", headers=_hdr(other["token"]))
    assert other_denied.status_code == 403


def test_unlock_specific_student(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr("app.notes.controllers.inspect_document", lambda path: 1)
    monkeypatch.setattr("app.notes.controllers.render_page", lambda path, page, mark: b"ok")

    branch = f"notes-b-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)
    s1 = _mk("student", branch, batch="Morning Batch A")
    s2 = _mk("student", branch, batch="Morning Batch A")

    created = client.post(
        "/api/notes/teacher",
        headers=_hdr(teacher["token"]),
        data={"title": "Speaking cues"},
        files={"file": _pdf_file()},
    )
    note_id = created.json()["id"]
    client.put(
        f"/api/notes/teacher/{note_id}/access",
        headers=_hdr(teacher["token"]),
        json={"unlocked_batches": [], "unlocked_student_ids": [s2["id"]]},
    )
    denied = client.get(f"/api/notes/student/{note_id}", headers=_hdr(s1["token"]))
    assert denied.status_code == 403
    allowed = client.get(f"/api/notes/student/{note_id}", headers=_hdr(s2["token"]))
    assert allowed.status_code == 200
    assert "unlocked_student_ids" not in allowed.json()
    listed = client.get("/api/notes/student", headers=_hdr(s2["token"]))
    assert any(n["id"] == note_id and n["unlocked"] is True for n in listed.json()["notes"])


def test_upload_text_document(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "NOTES_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"notes-c-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch)

    created = client.post(
        "/api/notes/teacher",
        headers=_hdr(teacher["token"]),
        data={"title": "Linking words", "description": "Plain text"},
        files={"file": ("linking-words.txt", BytesIO(b"However\nTherefore\nIn addition"), "text/plain")},
    )
    assert created.status_code == 200, created.text
    assert created.json()["kind"] == "txt"
    assert created.json()["page_count"] >= 1

    rejected = client.post(
        "/api/notes/teacher",
        headers=_hdr(teacher["token"]),
        data={"title": "Bad file"},
        files={"file": ("virus.exe", BytesIO(b"MZ"), "application/octet-stream")},
    )
    assert rejected.status_code == 400
