"""Module 7 — Student lifecycle, batch assignment, ownership/IDOR across skills."""
from __future__ import annotations

import uuid

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.listening.models import ListeningAttempt
from app.reading.models import ReadingAttempt
from app.speaking.services import speaking_session
from app.writing.models import WritingAttempt, WritingQuestion
from tests.conftest import _TestSessionFactory


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(
    *,
    role: str,
    branch_id: str = "",
    username: str | None = None,
    password: str = "Passw0rd!",
    batch: str = "",
    is_active: bool = True,
) -> dict:
    db = _TestSessionFactory()
    try:
        uid = f"{role[:2]}-{uuid.uuid4().hex[:10]}"
        uname = username or uid
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m7.test",
            username=uname,
            password_hash=hash_password(password),
            full_name=uname,
            branch_id=branch_id,
            batch=batch,
            is_active=is_active,
        )
        db.add(row)
        db.commit()
        token = create_access_token(sub=row.id, role=row.role, extra={"branch_id": branch_id})
        return {"id": row.id, "username": uname, "password": password, "token": token, "branch_id": branch_id}
    finally:
        db.close()


def _ensure_branch(bid: str, name: str, *, active: bool = True) -> None:
    db = _TestSessionFactory()
    try:
        row = db.query(Branch).filter_by(id=bid).first()
        if not row:
            db.add(Branch(id=bid, name=name, city=name, is_active=active))
        else:
            row.is_active = active
            row.name = name
        db.commit()
    finally:
        db.close()


def _ensure_batch(branch_id: str, name: str) -> str:
    db = _TestSessionFactory()
    try:
        bid = f"batch-{uuid.uuid4().hex[:8]}"
        existing = db.query(Batch).filter_by(branch_id=branch_id, name=name).first()
        if existing:
            return existing.id
        db.add(Batch(id=bid, name=name, branch_id=branch_id))
        db.commit()
        return bid
    finally:
        db.close()


def _user_db(user_id: str) -> dict | None:
    db = _TestSessionFactory()
    try:
        u = db.query(User).filter_by(id=user_id).first()
        if not u:
            return None
        return {
            "id": u.id,
            "role": u.role,
            "branch_id": u.branch_id,
            "batch": u.batch or "",
            "is_active": bool(u.is_active),
            "password_hash": u.password_hash,
        }
    finally:
        db.close()


def test_create_student_sa_ba_teacher_and_denials(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-a-{suffix}"
    b = f"m7-b-{suffix}"
    _ensure_branch(a, f"A {suffix}")
    _ensure_branch(b, f"B {suffix}")
    _ensure_batch(a, f"Morning {suffix}")
    _ensure_batch(b, f"Evening {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"ba_{suffix}")
    teacher = _mk(role="teacher", branch_id=a, username=f"t_{suffix}")
    student = _mk(role="student", branch_id=a, username=f"s0_{suffix}")

    sa_ok = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"sa_{suffix}@m7.test",
            "username": f"sa_{suffix}",
            "password": "Student1!",
            "full_name": "SA Student",
            "branch_id": b,
            "batch": f"Evening {suffix}",
        },
    )
    assert sa_ok.status_code == 200, sa_ok.text
    assert sa_ok.json()["batch"] == f"Evening {suffix}"
    assert "password_hash" not in sa_ok.json()
    assert verify_password("Student1!", _user_db(sa_ok.json()["id"])["password_hash"])

    ba_ok = client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "student",
            "email": f"bastu_{suffix}@m7.test",
            "username": f"bastu_{suffix}",
            "password": "Student1!",
            "full_name": "BA Student",
            "branch_id": a,
            "batch": f"Morning {suffix}",
        },
    )
    assert ba_ok.status_code == 200, ba_ok.text

    # Teacher may create students on own campus (product supports TeacherAddStudentPage).
    t_ok = client.post(
        "/api/org/users",
        headers=_hdr(teacher["token"]),
        json={
            "role": "student",
            "email": f"tstu_{suffix}@m7.test",
            "username": f"tstu_{suffix}",
            "password": "Student1!",
            "full_name": "T Student",
            "branch_id": a,
            "batch": f"Morning {suffix}",
        },
    )
    assert t_ok.status_code == 200, t_ok.text

    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(ba["token"]),
            json={
                "role": "student",
                "email": f"fx_{suffix}@m7.test",
                "username": f"fx_{suffix}",
                "password": "Student1!",
                "full_name": "FX",
                "branch_id": b,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(student["token"]),
            json={
                "role": "student",
                "email": f"self_{suffix}@m7.test",
                "username": f"self_{suffix}",
                "password": "Student1!",
                "full_name": "Self",
                "branch_id": a,
            },
        ).status_code
        == 403
    )


def test_create_rejects_inactive_branch_foreign_batch_duplicate(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    active = f"m7-ok-{suffix}"
    inactive = f"m7-off-{suffix}"
    other = f"m7-ot-{suffix}"
    _ensure_branch(active, f"OK {suffix}", active=True)
    _ensure_branch(inactive, f"Off {suffix}", active=False)
    _ensure_branch(other, f"OT {suffix}")
    _ensure_batch(active, f"BatchA {suffix}")
    _ensure_batch(other, f"BatchB {suffix}")

    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(sa["token"]),
            json={
                "role": "student",
                "email": f"off_{suffix}@m7.test",
                "username": f"off_{suffix}",
                "password": "Student1!",
                "full_name": "X",
                "branch_id": inactive,
            },
        ).status_code
        == 400
    )

    # Foreign-campus batch name
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(sa["token"]),
            json={
                "role": "student",
                "email": f"fb_{suffix}@m7.test",
                "username": f"fb_{suffix}",
                "password": "Student1!",
                "full_name": "X",
                "branch_id": active,
                "batch": f"BatchB {suffix}",
            },
        ).status_code
        == 400
    )

    first = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"dup_{suffix}@m7.test",
            "username": f"dup_{suffix}",
            "password": "Student1!",
            "full_name": "X",
            "branch_id": active,
            "batch": f"BatchA {suffix}",
        },
    )
    assert first.status_code == 200
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(sa["token"]),
            json={
                "role": "student",
                "email": f"dup_{suffix}@m7.test",
                "username": f"dup2_{suffix}",
                "password": "Student1!",
                "full_name": "X",
                "branch_id": active,
            },
        ).status_code
        == 409
    )


def test_login_deactivate_branch_jwt(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m7-life-{suffix}"
    _ensure_branch(bid, f"Life {suffix}")
    created = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"life_{suffix}@m7.test",
            "username": f"life_{suffix}",
            "password": "Student1!",
            "full_name": "Life",
            "branch_id": bid,
        },
    ).json()
    login = client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Student1!", "role": "student"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert client.get("/api/auth/me", headers=_hdr(token)).status_code == 200

    client.patch(f"/api/org/users/{created['id']}", headers=_hdr(sa["token"]), json={"is_active": False})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Student1!", "role": "student"},
    ).status_code == 403
    assert client.get("/api/auth/me", headers=_hdr(token)).status_code == 403

    client.patch(f"/api/org/users/{created['id']}", headers=_hdr(sa["token"]), json={"is_active": True})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Student1!", "role": "student"},
    ).status_code == 200

    client.patch(f"/api/org/branches/{bid}/status", headers=_hdr(sa["token"]), json={"is_active": False})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Student1!", "role": "student"},
    ).status_code == 403
    client.patch(f"/api/org/branches/{bid}/status", headers=_hdr(sa["token"]), json={"is_active": True})


def test_profile_list_mass_assignment(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-pa-{suffix}"
    b = f"m7-pb-{suffix}"
    _ensure_branch(a, f"PA {suffix}")
    _ensure_branch(b, f"PB {suffix}")
    _ensure_batch(a, f"ABatch {suffix}")
    _ensure_batch(b, f"BBatch {suffix}")
    s_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"pa_{suffix}@m7.test",
            "username": f"pa_{suffix}",
            "password": "Student1!",
            "full_name": "PA",
            "branch_id": a,
            "batch": f"ABatch {suffix}",
        },
    ).json()
    s_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"pb_{suffix}@m7.test",
            "username": f"pb_{suffix}",
            "password": "Student1!",
            "full_name": "PB",
            "branch_id": b,
        },
    ).json()
    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"pa_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    ba = _mk(role="branch_admin", branch_id=a, username=f"bap_{suffix}")
    teacher = _mk(role="teacher", branch_id=a, username=f"tp_{suffix}")

    assert client.get(f"/api/org/users/{s_a['id']}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/org/users/{s_b['id']}", headers=_hdr(tok_a)).status_code == 403
    assert client.get("/api/org/users", headers=_hdr(tok_a)).status_code == 403

    sa_list = client.get("/api/org/users?role=student", headers=_hdr(sa["token"]))
    assert {u["id"] for u in sa_list.json()["users"]} >= {s_a["id"], s_b["id"]}
    ba_list = client.get("/api/org/users?role=student", headers=_hdr(ba["token"]))
    ids = {u["id"] for u in ba_list.json()["users"]}
    assert s_a["id"] in ids and s_b["id"] not in ids

    # Mass assignment from student
    spoof = client.patch(
        f"/api/org/users/{s_a['id']}",
        headers=_hdr(tok_a),
        json={"branch_id": b, "batch": f"BBatch {suffix}", "is_active": False, "full_name": "Still Me"},
    )
    assert spoof.status_code == 403
    assert _user_db(s_a["id"])["branch_id"] == a
    assert _user_db(s_a["id"])["batch"] == f"ABatch {suffix}"

    # Safe self edit
    ok = client.patch(
        f"/api/org/users/{s_a['id']}",
        headers=_hdr(tok_a),
        json={"full_name": "Renamed Student"},
    )
    assert ok.status_code == 200
    assert ok.json()["full_name"] == "Renamed Student"

    # Teacher cannot set foreign batch via batch field when student on A
    assert (
        client.patch(
            f"/api/org/users/{s_a['id']}",
            headers=_hdr(teacher["token"]),
            json={"batch": f"BBatch {suffix}"},
        ).status_code
        == 400
    )


def test_reassignment_clears_batch(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-ra-{suffix}"
    b = f"m7-rb-{suffix}"
    _ensure_branch(a, f"RA {suffix}")
    _ensure_branch(b, f"RB {suffix}")
    _ensure_batch(a, f"RABatch {suffix}")
    student = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"re_{suffix}@m7.test",
            "username": f"re_{suffix}",
            "password": "Student1!",
            "full_name": "Re",
            "branch_id": a,
            "batch": f"RABatch {suffix}",
        },
    ).json()
    moved = client.patch(
        f"/api/org/users/{student['id']}",
        headers=_hdr(sa["token"]),
        json={"branch_id": b},
    )
    assert moved.status_code == 200
    row = _user_db(student["id"])
    assert row["branch_id"] == b
    assert row["batch"] == ""


def test_homework_and_skill_ownership_idor(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-ow-{suffix}"
    b = f"m7-ox-{suffix}"
    _ensure_branch(a, f"OW {suffix}")
    _ensure_branch(b, f"OX {suffix}")
    s_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"owa_{suffix}@m7.test",
            "username": f"owa_{suffix}",
            "password": "Student1!",
            "full_name": "A",
            "branch_id": a,
        },
    ).json()
    s_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"owb_{suffix}@m7.test",
            "username": f"owb_{suffix}",
            "password": "Student1!",
            "full_name": "B",
            "branch_id": b,
        },
    ).json()
    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"owa_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"owb_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]

    db = _TestSessionFactory()
    try:
        hw = HomeworkAssignment(
            id=f"hw-{suffix}",
            title="HW",
            module="writing",
            scope="piece",
            source="bank",
            created_by="teacher",
        )
        hw.student_ids = [s_a["id"]]
        hw.payload = {"prompt": "x"}
        db.add(hw)
        db.add(
            HomeworkSubmission(
                id=f"hs-{suffix}",
                assignment_id=hw.id,
                student_id=s_a["id"],
                status="submitted",
            )
        )
        db.add(
            ReadingAttempt(
                id=f"ra-{suffix}",
                student_id=s_a["id"],
                test_id="test-1",
                test_type="academic",
                mode="full_mock",
                status="submitted",
            )
        )
        db.add(
            ListeningAttempt(
                id=f"la-{suffix}",
                student_id=s_a["id"],
                test_id="test-1",
                mode="full_mock",
                status="submitted",
            )
        )
        wq = WritingQuestion(
            id=f"wq-{suffix}",
            public_id=f"pub-{suffix}",
            prompt="Write",
            test_type="academic",
            task_number=2,
            question_type="essay",
            status="published",
        )
        db.add(wq)
        db.add(
            WritingAttempt(
                id=f"wa-{suffix}",
                student_id=s_a["id"],
                question_id=wq.id,
                status="submitted",
                answer_text="hello",
                word_count=1,
            )
        )
        session, _ = speaking_session.create_session(
            db,
            "stored",
            {
                "id": f"spk-{suffix}",
                "title": "T",
                "part1": ["Q1"],
                "part2": {"topic": "T", "bullets": ["a"]},
                "part3": ["P3"],
            },
            student_id=s_a["id"],
        )
        db.commit()
        speaking_id = session.id
    finally:
        db.close()

    # Homework: A sees own assignment; B does not
    hw_a = client.get("/api/homework/student", headers=_hdr(tok_a))
    assert hw_a.status_code == 200, hw_a.text
    assert any(x["id"] == f"hw-{suffix}" for x in hw_a.json()["assignments"])
    hw_b = client.get("/api/homework/student", headers=_hdr(tok_b))
    assert hw_b.status_code == 200
    assert all(x.get("id") != f"hw-{suffix}" for x in hw_b.json()["assignments"])
    assert client.get(f"/api/homework/student/hw-{suffix}", headers=_hdr(tok_b)).status_code == 404

    # Reading / Listening IDOR
    assert client.get(f"/api/reading/attempts/ra-{suffix}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/reading/attempts/ra-{suffix}", headers=_hdr(tok_b)).status_code == 403
    assert client.get(f"/api/listening/attempts/la-{suffix}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/listening/attempts/la-{suffix}", headers=_hdr(tok_b)).status_code == 403

    # Writing
    assert client.get(f"/api/writing/attempts/wa-{suffix}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/writing/attempts/wa-{suffix}", headers=_hdr(tok_b)).status_code == 403

    # Speaking
    assert client.get(f"/api/history/{speaking_id}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/history/{speaking_id}", headers=_hdr(tok_b)).status_code == 403

    # Header spoof: Student B cannot act as Student A
    spoof = client.get(
        f"/api/writing/attempts/wa-{suffix}",
        headers={**_hdr(tok_b), "X-Student-Id": s_a["id"]},
    )
    assert spoof.status_code == 403


def test_notes_batch_unlock_and_spoof_denied(client, monkeypatch):
    from app.notes.models import ClassNote

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-nt-{suffix}"
    b = f"m7-nu-{suffix}"
    batch_a = f"NotesBatch {suffix}"
    _ensure_branch(a, f"NT {suffix}")
    _ensure_branch(b, f"NU {suffix}")
    _ensure_batch(a, batch_a)
    s_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"nta_{suffix}@m7.test",
            "username": f"nta_{suffix}",
            "password": "Student1!",
            "full_name": "NA",
            "branch_id": a,
            "batch": batch_a,
        },
    ).json()
    s_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"ntb_{suffix}@m7.test",
            "username": f"ntb_{suffix}",
            "password": "Student1!",
            "full_name": "NB",
            "branch_id": b,
        },
    ).json()
    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"nta_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"ntb_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]

    note_id = f"note-{suffix}"
    db = _TestSessionFactory()
    try:
        note = ClassNote(
            id=note_id,
            title="Campus notes",
            description="",
            original_name="notes.pdf",
            stored_name="notes.pdf",
            page_count=1,
            created_by="teacher",
        )
        note.unlocked_batches = [batch_a]
        note.unlocked_student_ids = []
        db.add(note)
        db.commit()
    finally:
        db.close()

    list_a = client.get("/api/notes/student", headers=_hdr(tok_a))
    assert list_a.status_code == 200
    row_a = next(n for n in list_a.json()["notes"] if n["id"] == note_id)
    assert row_a["unlocked"] is True

    list_b = client.get("/api/notes/student", headers=_hdr(tok_b))
    assert list_b.status_code == 200
    # Locked notes are omitted from the student list (no cross-campus metadata leak).
    assert not any(n["id"] == note_id for n in list_b.json()["notes"])
    assert client.get(f"/api/notes/student/{note_id}", headers=_hdr(tok_b)).status_code == 403
    # Spoofing another student's id via header must not unlock
    assert (
        client.get(
            f"/api/notes/student/{note_id}",
            headers={**_hdr(tok_b), "X-Student-Id": s_a["id"], "X-Student-Batch": batch_a},
        ).status_code
        == 403
    )


def test_mock_attempt_ownership(client, monkeypatch):
    from app.mocks.models import MockAssignment, MockAssignmentStudent, MockAttempt

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    monkeypatch.setattr(settings, "ENABLE_LIVE_MOCK_MONITORING", True)
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m7-mk-{suffix}"
    b = f"m7-ml-{suffix}"
    _ensure_branch(a, f"MK {suffix}")
    _ensure_branch(b, f"ML {suffix}")
    s_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"mka_{suffix}@m7.test",
            "username": f"mka_{suffix}",
            "password": "Student1!",
            "full_name": "MA",
            "branch_id": a,
        },
    ).json()
    s_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"mkb_{suffix}@m7.test",
            "username": f"mkb_{suffix}",
            "password": "Student1!",
            "full_name": "MB",
            "branch_id": b,
        },
    ).json()
    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"mka_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"mkb_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]

    aid = f"ma-{suffix}"
    slot_id = f"ms-{suffix}"
    attempt_id = f"matt-{suffix}"
    db = _TestSessionFactory()
    try:
        db.add(
            MockAssignment(
                id=aid,
                title="Mock",
                mock_type="full",
                ielts_type="academic",
                created_by="teacher",
            )
        )
        db.add(
            MockAssignmentStudent(
                id=slot_id,
                assignment_id=aid,
                student_id=s_a["id"],
                student_name="MA",
                status="in_progress",
            )
        )
        db.add(
            MockAttempt(
                id=attempt_id,
                assignment_student_id=slot_id,
                assignment_id=aid,
                student_id=s_a["id"],
                status="in_progress",
            )
        )
        db.commit()
    finally:
        db.close()

    own = client.post(
        f"/api/mocks/attempts/{attempt_id}/answers",
        headers=_hdr(tok_a),
        json={"answers": [{"question_key": "q1", "value": "a"}]},
    )
    assert own.status_code == 200, own.text
    foreign = client.post(
        f"/api/mocks/attempts/{attempt_id}/answers",
        headers=_hdr(tok_b),
        json={"answers": [{"question_key": "q1", "value": "hack"}]},
    )
    assert foreign.status_code == 404
    spoof = client.post(
        f"/api/mocks/attempts/{attempt_id}/answers",
        headers={**_hdr(tok_b), "X-Student-Id": s_a["id"]},
        json={"answers": [{"question_key": "q1", "value": "hack"}]},
    )
    assert spoof.status_code == 404


def test_deactivate_preserves_academic_history(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m7-hist-{suffix}"
    _ensure_branch(bid, f"Hist {suffix}")
    created = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"hist_{suffix}@m7.test",
            "username": f"hist_{suffix}",
            "password": "Student1!",
            "full_name": "Hist",
            "branch_id": bid,
        },
    ).json()
    db = _TestSessionFactory()
    try:
        db.add(
            ReadingAttempt(
                id=f"rh-{suffix}",
                student_id=created["id"],
                test_id="t1",
                test_type="academic",
                mode="full_mock",
                status="submitted",
                estimated_band=6.5,
            )
        )
        db.commit()
    finally:
        db.close()

    client.delete(f"/api/org/users/{created['id']}", headers=_hdr(sa["token"]))
    row = _user_db(created["id"])
    assert row is not None
    assert row["is_active"] is False
    db = _TestSessionFactory()
    try:
        attempt = db.query(ReadingAttempt).filter_by(id=f"rh-{suffix}").first()
        assert attempt is not None
        assert attempt.student_id == created["id"]
        assert attempt.estimated_band == 6.5
    finally:
        db.close()
