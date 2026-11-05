"""Module 3 — RBAC, branch isolation, IDOR, mass-assignment."""
from __future__ import annotations

import pytest

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.mocks.models import MockAssignment, MockAttempt, MockIntegrityEvent
from app.notes.models import ClassNote


def _hdr(user: dict) -> dict:
    token = create_access_token(
        sub=user["id"], role=user["role"], extra={"branch_id": user.get("branch_id") or ""}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    for bid, name in (("branch-a", "Branch A"), ("branch-b", "Branch B")):
        if not db.query(Branch).filter_by(id=bid).first():
            db.add(Branch(id=bid, name=name, city=name))
    db.commit()

    pw = hash_password("Module3Pass!")
    specs = [
        ("m3-sa", "super_admin", "m3sa@test.com", "m3sa", ""),
        ("m3-ba-a", "branch_admin", "m3baa@test.com", "m3baa", "branch-a"),
        ("m3-ba-b", "branch_admin", "m3bab@test.com", "m3bab", "branch-b"),
        ("m3-t-a", "teacher", "m3ta@test.com", "m3ta", "branch-a"),
        ("m3-t-b", "teacher", "m3tb@test.com", "m3tb", "branch-b"),
        ("m3-s-a", "student", "m3sa1@test.com", "m3sa1", "branch-a"),
        ("m3-s-b", "student", "m3sb1@test.com", "m3sb1", "branch-b"),
    ]
    users = {}
    for uid, role, email, username, branch_id in specs:
        row = db.query(User).filter_by(id=uid).first()
        if not row:
            row = User(
                id=uid,
                role=role,
                email=email,
                username=username,
                password_hash=pw,
                full_name=username,
                branch_id=branch_id,
                batch="Morning A" if uid == "m3-s-a" else ("Morning B" if uid == "m3-s-b" else ""),
                is_active=True,
            )
            db.add(row)
        else:
            row.role = role
            row.email = email
            row.username = username
            row.password_hash = pw
            row.branch_id = branch_id
            row.batch = "Morning A" if uid == "m3-s-a" else ("Morning B" if uid == "m3-s-b" else "")
            row.is_active = True
        users[uid] = {
            "id": uid,
            "role": role,
            "username": username,
            "branch_id": branch_id,
            "batch": row.batch,
        }

    for bid, name, tid in (("batch-a", "Morning A", "m3-t-a"), ("batch-b", "Morning B", "m3-t-b")):
        if not db.query(Batch).filter_by(id=bid).first():
            db.add(
                Batch(
                    id=bid,
                    name=name,
                    branch_id="branch-a" if bid.endswith("a") else "branch-b",
                    teacher_id=tid,
                )
            )
    db.commit()

    # Homework owned by teacher A targeting student A
    hw = db.query(HomeworkAssignment).filter_by(id="hw-m3-a").first()
    if not hw:
        hw = HomeworkAssignment(
            id="hw-m3-a",
            title="HW A",
            module="writing",
            created_by="m3-t-a",
            batch_label="Morning A",
        )
        hw.student_ids = ["m3-s-a"]
        db.add(hw)
    else:
        hw.created_by = "m3-t-a"
        hw.student_ids = ["m3-s-a"]
        hw.batch_label = "Morning A"

    hw_b = db.query(HomeworkAssignment).filter_by(id="hw-m3-b").first()
    if not hw_b:
        hw_b = HomeworkAssignment(
            id="hw-m3-b",
            title="HW B",
            module="writing",
            created_by="m3-t-b",
            batch_label="Morning B",
        )
        hw_b.student_ids = ["m3-s-b"]
        db.add(hw_b)
    else:
        hw_b.created_by = "m3-t-b"
        hw_b.student_ids = ["m3-s-b"]

    # Entire-batch assignment for branch B only (no student_ids)
    hw_batch_b = db.query(HomeworkAssignment).filter_by(id="hw-m3-batch-b").first()
    if not hw_batch_b:
        hw_batch_b = HomeworkAssignment(
            id="hw-m3-batch-b",
            title="Entire B",
            module="reading",
            created_by="m3-t-b",
            batch_label="Morning B",
        )
        hw_batch_b.student_ids = []
        db.add(hw_batch_b)

    note_a = db.query(ClassNote).filter_by(id="note-m3-a").first()
    if not note_a:
        note_a = ClassNote(
            id="note-m3-a",
            title="Note A",
            original_name="a.txt",
            stored_name="a.txt",
            page_count=1,
            created_by="m3-t-a",
        )
        note_a.unlocked_student_ids = ["m3-s-a"]
        note_a.unlocked_batches = []
        db.add(note_a)

    note_b = db.query(ClassNote).filter_by(id="note-m3-b").first()
    if not note_b:
        note_b = ClassNote(
            id="note-m3-b",
            title="Note B",
            original_name="b.txt",
            stored_name="b.txt",
            page_count=1,
            created_by="m3-t-b",
        )
        note_b.unlocked_student_ids = ["m3-s-b"]
        note_b.unlocked_batches = []
        db.add(note_b)

    mock = db.query(MockAssignment).filter_by(id="mock-m3-b").first()
    if not mock:
        mock = MockAssignment(
            id="mock-m3-b",
            title="Mock B",
            mock_type="reading",
            ielts_type="academic",
            created_by="m3-t-b",
            duration_minutes=60,
        )
        db.add(mock)
        db.flush()
        attempt = MockAttempt(
            id="att-m3-b",
            assignment_id="mock-m3-b",
            assignment_student_id="slot-m3-b",
            student_id="m3-s-b",
            status="in_progress",
        )
        db.add(attempt)
        db.add(
            MockIntegrityEvent(
                id="evt-m3-b",
                attempt_id="att-m3-b",
                student_id="m3-s-b",
                event_type="blur",
                metadata_json="{}",
            )
        )
    db.commit()
    db.expunge_all()
    return users


def test_unauthenticated_protected(client, world, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    assert client.get("/api/org/users").status_code == 401
    assert client.get("/api/org/branches").status_code == 401
    assert client.get("/api/homework/teacher/assignments").status_code == 403


def test_role_escalation_denied(client, world):
    student = world["m3-s-a"]
    teacher = world["m3-t-a"]
    ba = world["m3-ba-a"]
    assert client.post("/api/org/branches", headers=_hdr(student), json={"name": "X", "admins": []}).status_code == 403
    assert client.post("/api/org/branches", headers=_hdr(teacher), json={"name": "X", "admins": []}).status_code == 403
    assert client.post("/api/org/branches", headers=_hdr(ba), json={"name": "X", "admins": []}).status_code == 403


def test_branch_admin_cannot_list_other_branch_users(client, world):
    ba_a = world["m3-ba-a"]
    res = client.get("/api/org/users", headers=_hdr(ba_a))
    assert res.status_code == 200
    ids = {u["id"] for u in res.json()["users"]}
    assert "m3-s-a" in ids or "m3-t-a" in ids
    assert "m3-s-b" not in ids
    assert "m3-t-b" not in ids
    assert "m3-ba-b" not in ids


def test_branch_admin_cannot_patch_other_branch_student(client, world):
    ba_a = world["m3-ba-a"]
    foreign = world["m3-s-b"]
    res = client.patch(
        f"/api/org/users/{foreign['id']}",
        headers=_hdr(ba_a),
        json={"full_name": "Hacked"},
    )
    assert res.status_code == 403


def test_branch_admin_cannot_deactivate_other_branch_teacher(client, world):
    ba_a = world["m3-ba-a"]
    foreign = world["m3-t-b"]
    assert client.delete(f"/api/org/users/{foreign['id']}", headers=_hdr(ba_a)).status_code == 403


def test_teacher_cannot_patch_other_branch_student(client, world):
    teacher = world["m3-t-a"]
    foreign = world["m3-s-b"]
    assert (
        client.patch(
            f"/api/org/users/{foreign['id']}",
            headers=_hdr(teacher),
            json={"full_name": "Nope"},
        ).status_code
        == 403
    )


def test_teacher_can_patch_same_branch_student(client, world):
    teacher = world["m3-t-a"]
    student = world["m3-s-a"]
    res = client.patch(
        f"/api/org/users/{student['id']}",
        headers=_hdr(teacher),
        json={"full_name": "Student A Updated"},
    )
    assert res.status_code == 200
    assert res.json()["full_name"] == "Student A Updated"


def test_create_user_cannot_set_foreign_branch(client, world):
    teacher = world["m3-t-a"]
    res = client.post(
        "/api/org/users",
        headers=_hdr(teacher),
        json={
            "role": "student",
            "email": "crossbranch@test.com",
            "username": "crossbranch",
            "password": "Module3Pass!",
            "full_name": "Cross",
            "branch_id": "branch-b",
        },
    )
    assert res.status_code == 403


def test_mass_assignment_role_promotion_blocked(client, world):
    ba = world["m3-ba-a"]
    res = client.post(
        "/api/org/users",
        headers=_hdr(ba),
        json={
            "role": "super_admin",
            "email": "evil@test.com",
            "username": "eviladmin",
            "password": "Module3Pass!",
            "full_name": "Evil",
            "branch_id": "branch-a",
        },
    )
    assert res.status_code == 403


def test_batches_query_cannot_cross_branch(client, world):
    ba_a = world["m3-ba-a"]
    denied = client.get("/api/org/batches?branch_id=branch-b", headers=_hdr(ba_a))
    assert denied.status_code == 403
    ok = client.get("/api/org/batches", headers=_hdr(ba_a))
    assert ok.status_code == 200
    ids = {b["id"] for b in ok.json()["batches"]}
    assert "batch-b" not in ids


def test_branches_list_scoped(client, world):
    ba_a = world["m3-ba-a"]
    res = client.get("/api/org/branches", headers=_hdr(ba_a))
    assert res.status_code == 200
    ids = {b["id"] for b in res.json()["branches"]}
    assert ids == {"branch-a"}


def test_super_admin_sees_both_branches(client, world):
    sa = world["m3-sa"]
    res = client.get("/api/org/branches", headers=_hdr(sa))
    assert res.status_code == 200
    ids = {b["id"] for b in res.json()["branches"]}
    assert "branch-a" in ids and "branch-b" in ids


def test_forged_jwt_role_claim_no_escalation(client, world):
    student = world["m3-s-a"]
    forged = create_access_token(sub=student["id"], role="super_admin")
    headers = {"Authorization": f"Bearer {forged}"}
    assert client.get("/api/org/users", headers=headers).status_code == 403
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["role"] == "student"


def test_homework_teacher_isolation(client, world):
    teacher_a = world["m3-t-a"]
    listed = client.get("/api/homework/teacher/assignments", headers=_hdr(teacher_a))
    assert listed.status_code == 200
    ids = {a["id"] for a in listed.json()["assignments"]}
    assert "hw-m3-a" in ids
    assert "hw-m3-b" not in ids
    assert client.delete("/api/homework/teacher/assignments/hw-m3-b", headers=_hdr(teacher_a)).status_code == 403
    assert (
        client.get("/api/homework/teacher/assignments/hw-m3-b/submissions", headers=_hdr(teacher_a)).status_code
        == 403
    )


def test_homework_student_cannot_see_other_branch_entire_batch(client, world):
    student_a = world["m3-s-a"]
    # Spoof header must not unlock Branch B entire-batch homework.
    res = client.get(
        "/api/homework/student",
        headers={**_hdr(student_a), "X-Student-Batch": "Morning B"},
    )
    assert res.status_code == 200
    ids = {a["id"] for a in res.json()["assignments"]}
    assert "hw-m3-batch-b" not in ids
    assert "hw-m3-b" not in ids
    assert "hw-m3-a" in ids


def test_notes_student_header_batch_spoof_blocked(client, world):
    student_a = world["m3-s-a"]
    # Note B unlocked for student B only — spoofed batch header must not unlock.
    res = client.get(
        "/api/notes/student/note-m3-b",
        headers={**_hdr(student_a), "X-Student-Batch": "Morning B"},
    )
    assert res.status_code == 403


def test_notes_teacher_cannot_mutate_foreign_note(client, world):
    teacher_a = world["m3-t-a"]
    assert (
        client.put(
            "/api/notes/teacher/note-m3-b/access",
            headers=_hdr(teacher_a),
            json={"unlocked_batches": [], "unlocked_student_ids": ["m3-s-a"]},
        ).status_code
        == 403
    )


def test_mock_attempt_events_idor(client, world):
    teacher_a = world["m3-t-a"]
    # Hidden foreign attempt: 404 (do not leak existence).
    assert client.get("/api/mocks/attempts/att-m3-b/events", headers=_hdr(teacher_a)).status_code == 404


def test_student_cannot_access_org_user_lists(client, world):
    student = world["m3-s-a"]
    assert client.get("/api/org/users", headers=_hdr(student)).status_code == 403
