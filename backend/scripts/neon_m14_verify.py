"""Controlled Neon verification for Module 14 — Notes / Resources."""
from __future__ import annotations

import sys
import uuid
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.access import rename_batch_cascade
from app.auth.models import Batch, Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.notes.models import ClassNote

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


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m14-neon-a-{tag}"
    branch_b = f"m14-neon-b-{tag}"
    ids: list[str] = []
    note_ids: list[str] = []
    failures: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)
            print(f"  FAIL: {msg}")
        else:
            print(f"  OK: {msg}")

    client = TestClient(app)
    db = SessionLocal()
    try:
        print("=== Neon notes integrity snapshot ===")
        notes = db.query(ClassNote).all()
        print(f"  notes={len(notes)}")
        missing_creator = sum(1 for n in notes if not n.created_by)
        legacy = sum(1 for n in notes if (n.created_by or "") in {"teacher", ""})
        print(f"  missing_created_by={missing_creator} legacy_teacher_or_empty={legacy}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M14 {name} {tag}", city="M14", is_active=True))
            db.add(Batch(id=f"bat-{name.lower()}-{tag}", branch_id=bid, name="Morning"))
        for role, bid, suffix, batch in (
            ("student", branch_a, "a", "Morning"),
            ("student", branch_b, "b", "Morning"),
            ("teacher", branch_a, "ta", None),
            ("teacher", branch_b, "tb", None),
        ):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m14_{suffix}_{tag}@m14.neon",
                username=f"m14_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M14 {suffix}",
                branch_id=bid,
                batch=batch,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        db.commit()
        stu_a, stu_b, tea_a, tea_b = ids
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m14_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m14_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m14_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m14_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Create / visibility ===")
    created = client.post(
        "/api/notes/teacher",
        headers=_hdr(tok_ta),
        data={"title": f"M14 Note {tag}", "description": "batch unlock"},
        files={"file": ("notes.pdf", BytesIO(MIN_PDF), "application/pdf")},
    )
    check(created.status_code == 200, f"create note ({created.status_code})")
    note_id = created.json().get("id") if created.status_code == 200 else ""
    if note_id:
        note_ids.append(note_id)

    access = client.put(
        f"/api/notes/teacher/{note_id}/access",
        headers=_hdr(tok_ta),
        json={"unlocked_batches": ["Morning"], "unlocked_student_ids": []},
    )
    check(access.status_code == 200, f"unlock Morning ({access.status_code})")

    check(
        client.get(f"/api/notes/student/{note_id}", headers=_hdr(tok_a)).status_code == 200,
        "Student A sees note",
    )
    check(
        client.get(f"/api/notes/student/{note_id}", headers=_hdr(tok_b)).status_code == 403,
        "Student B same batch name denied",
    )
    check(
        client.get(
            f"/api/notes/student/{note_id}",
            headers={**_hdr(tok_b), "X-Student-Batch": "Morning", "X-Student-Id": stu_a},
        ).status_code
        == 403,
        "header spoof denied",
    )

    print("=== Ownership / edit ===")
    edit = client.put(
        f"/api/notes/teacher/{note_id}",
        headers=_hdr(tok_ta),
        data={"title": f"M14 Edited {tag}", "description": "edited"},
    )
    check(edit.status_code == 200 and edit.json().get("title", "").startswith("M14 Edited"), "teacher A edit")
    check(
        client.put(
            f"/api/notes/teacher/{note_id}",
            headers=_hdr(tok_tb),
            data={"title": "Hijack", "description": ""},
        ).status_code
        == 403,
        "teacher B edit denied",
    )

    db = SessionLocal()
    try:
        row = db.query(ClassNote).filter_by(id=note_id).first()
        check(
            row is not None and row.title.startswith("M14 Edited") and row.created_by == tea_a,
            "Neon edit persistence",
        )
        stored = Path(settings.NOTES_DIR) / (row.stored_name if row else "")
        check(row is not None and stored.exists(), "file exists on disk")
    finally:
        db.close()

    print("=== Batch rename ===")
    db = SessionLocal()
    try:
        bat = db.query(Batch).filter_by(branch_id=branch_a, name="Morning").first()
        rename_batch_cascade(db, bat, f"Morning-{tag}")
        db.commit()
        row = db.query(ClassNote).filter_by(id=note_id).first()
        check(row is not None and f"Morning-{tag}" in (row.unlocked_batches or []), "note batch label cascaded")
        u = db.query(User).filter_by(id=stu_a).first()
        check(u is not None and u.batch == f"Morning-{tag}", "student batch renamed")
    finally:
        db.close()
    check(
        client.get(f"/api/notes/student/{note_id}", headers=_hdr(tok_a)).status_code == 200,
        "Student A still sees after rename",
    )

    print("=== Delete ===")
    check(
        client.delete(f"/api/notes/teacher/{note_id}", headers=_hdr(tok_tb)).status_code == 403,
        "teacher B delete denied",
    )
    deleted = client.delete(f"/api/notes/teacher/{note_id}", headers=_hdr(tok_ta))
    check(deleted.status_code == 200, f"teacher A delete ({deleted.status_code})")
    if note_id in note_ids:
        note_ids.remove(note_id)

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for nid in note_ids:
            row = db.query(ClassNote).filter_by(id=nid).first()
            if row:
                p = Path(settings.NOTES_DIR) / row.stored_name
                db.delete(row)
                if p.exists():
                    p.unlink()
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in (branch_a, branch_b):
            for bat in db.query(Batch).filter_by(branch_id=bid).all():
                db.delete(bat)
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M14 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM14_NEON_NOTES_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM14_NEON_NOTES_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
