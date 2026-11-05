"""Module 8 — Batch/class lifecycle, teacher sync, student assignment, rename, IDOR."""
from __future__ import annotations

import uuid

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.homework.models import HomeworkAssignment
from app.notes.models import ClassNote
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
    batches: str = "",
    is_active: bool = True,
) -> dict:
    db = _TestSessionFactory()
    try:
        uid = f"{role[:2]}-{uuid.uuid4().hex[:10]}"
        uname = username or uid
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m8.test",
            username=uname,
            password_hash=hash_password(password),
            full_name=uname,
            branch_id=branch_id,
            batch=batch,
            batches=batches,
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


def _batch_db(batch_id: str) -> dict | None:
    db = _TestSessionFactory()
    try:
        b = db.query(Batch).filter_by(id=batch_id).first()
        if not b:
            return None
        return {
            "id": b.id,
            "name": b.name,
            "branch_id": b.branch_id,
            "teacher_id": b.teacher_id or "",
            "schedule": b.schedule or "",
        }
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
            "batch": u.batch or "",
            "batches": u.batches or "",
            "branch_id": u.branch_id or "",
            "is_active": bool(u.is_active),
        }
    finally:
        db.close()


def test_create_list_denials(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-a-{suffix}"
    b = f"m8-b-{suffix}"
    _ensure_branch(a, f"A {suffix}")
    _ensure_branch(b, f"B {suffix}")
    inactive = f"m8-off-{suffix}"
    _ensure_branch(inactive, f"Off {suffix}", active=False)
    ba = _mk(role="branch_admin", branch_id=a, username=f"ba_{suffix}")
    teacher = _mk(role="teacher", branch_id=a, username=f"t_{suffix}")
    student = _mk(role="student", branch_id=a, username=f"s_{suffix}")

    sa_ok = client.post(
        "/api/org/batches",
        headers=_hdr(sa["token"]),
        json={"name": f"Evening {suffix}", "branch_id": b, "schedule": "Mon"},
    )
    assert sa_ok.status_code == 200, sa_ok.text
    assert sa_ok.json()["branchId"] == b

    ba_ok = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"Morning {suffix}", "branch_id": a},
    )
    assert ba_ok.status_code == 200, ba_ok.text

    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(ba["token"]),
            json={"name": f"Foreign {suffix}", "branch_id": b},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(teacher["token"]),
            json={"name": f"T {suffix}", "branch_id": a},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(student["token"]),
            json={"name": f"S {suffix}", "branch_id": a},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(sa["token"]),
            json={"name": "   ", "branch_id": a},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(sa["token"]),
            json={"name": f"Dup {suffix}", "branch_id": inactive},
        ).status_code
        == 400
    )
    # Same-branch duplicate (case-insensitive)
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(ba["token"]),
            json={"name": f"morning {suffix}", "branch_id": a},
        ).status_code
        == 409
    )
    # Whitespace-padded duplicate → 409 (not 500)
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(ba["token"]),
            json={"name": f"  Morning {suffix}  ", "branch_id": a},
        ).status_code
        == 409
    )
    # Same name other branch OK
    assert (
        client.post(
            "/api/org/batches",
            headers=_hdr(sa["token"]),
            json={"name": f"Morning {suffix}", "branch_id": b},
        ).status_code
        == 200
    )

    sa_list = client.get("/api/org/batches", headers=_hdr(sa["token"]))
    assert sa_list.status_code == 200
    assert len(sa_list.json()["batches"]) >= 3
    assert "unassigned_students" not in sa_list.json() or sa_list.json().get("unassigned_students") is None
    ba_list = client.get("/api/org/batches", headers=_hdr(ba["token"]))
    assert all(x["branchId"] == a for x in ba_list.json()["batches"])
    assert "unassigned_students" in ba_list.json()
    assert client.get("/api/org/batches", headers=_hdr(student["token"])).status_code == 403


def test_teacher_assign_reassign_sync(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-ta-{suffix}"
    b = f"m8-tb-{suffix}"
    _ensure_branch(a, f"TA {suffix}")
    _ensure_branch(b, f"TB {suffix}")
    t1 = _mk(role="teacher", branch_id=a, username=f"t1_{suffix}")
    t2 = _mk(role="teacher", branch_id=a, username=f"t2_{suffix}")
    t_foreign = _mk(role="teacher", branch_id=b, username=f"tf_{suffix}")
    t_inactive = _mk(role="teacher", branch_id=a, username=f"ti_{suffix}", is_active=False)
    ba = _mk(role="branch_admin", branch_id=a, username=f"bat_{suffix}")

    created = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"Cohort {suffix}", "branch_id": a, "teacher_id": t1["id"]},
    )
    assert created.status_code == 200, created.text
    bid = created.json()["id"]
    assert created.json()["teacherId"] == t1["id"]
    assert f"Cohort {suffix}" in _user_db(t1["id"])["batches"]

    assert (
        client.patch(
            f"/api/org/batches/{bid}",
            headers=_hdr(ba["token"]),
            json={"teacher_id": t_foreign["id"]},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/org/batches/{bid}",
            headers=_hdr(ba["token"]),
            json={"teacher_id": t_inactive["id"]},
        ).status_code
        == 400
    )

    moved = client.patch(
        f"/api/org/batches/{bid}",
        headers=_hdr(ba["token"]),
        json={"teacher_id": t2["id"]},
    )
    assert moved.status_code == 200
    assert _batch_db(bid)["teacher_id"] == t2["id"]
    assert f"Cohort {suffix}" not in _user_db(t1["id"])["batches"]
    assert f"Cohort {suffix}" in _user_db(t2["id"])["batches"]

    cleared = client.patch(
        f"/api/org/batches/{bid}",
        headers=_hdr(ba["token"]),
        json={"teacher_id": ""},
    )
    assert cleared.status_code == 200
    assert _batch_db(bid)["teacher_id"] == ""
    assert f"Cohort {suffix}" not in _user_db(t2["id"])["batches"]


def test_student_assign_move_remove(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-sa-{suffix}"
    b = f"m8-sb-{suffix}"
    _ensure_branch(a, f"SA {suffix}")
    _ensure_branch(b, f"SB {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"bas_{suffix}")
    b1 = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"A1 {suffix}", "branch_id": a},
    ).json()
    b2 = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"A2 {suffix}", "branch_id": a},
    ).json()
    client.post(
        "/api/org/batches",
        headers=_hdr(sa["token"]),
        json={"name": f"B1 {suffix}", "branch_id": b},
    )
    stu = client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "student",
            "email": f"st_{suffix}@m8.test",
            "username": f"st_{suffix}",
            "password": "Student1!",
            "full_name": "Stu",
            "branch_id": a,
            "batch": f"A1 {suffix}",
        },
    )
    assert stu.status_code == 200
    sid = stu.json()["id"]
    assert _user_db(sid)["batch"] == f"A1 {suffix}"

    # Move A1 → A2
    moved = client.patch(
        f"/api/org/users/{sid}",
        headers=_hdr(ba["token"]),
        json={"batch": f"A2 {suffix}"},
    )
    assert moved.status_code == 200
    assert _user_db(sid)["batch"] == f"A2 {suffix}"

    # Foreign batch rejected
    assert (
        client.patch(
            f"/api/org/users/{sid}",
            headers=_hdr(ba["token"]),
            json={"batch": f"B1 {suffix}"},
        ).status_code
        == 400
    )

    # Remove
    cleared = client.patch(
        f"/api/org/users/{sid}",
        headers=_hdr(ba["token"]),
        json={"batch": ""},
    )
    assert cleared.status_code == 200
    assert _user_db(sid)["batch"] == ""
    assert _user_db(sid)["branch_id"] == a

    # Student cannot self-assign
    tok = client.post(
        "/api/auth/login",
        json={"username": f"st_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    assert (
        client.patch(
            f"/api/org/users/{sid}",
            headers=_hdr(tok),
            json={"batch": f"A1 {suffix}"},
        ).status_code
        == 403
    )
    # Detail access
    assert client.get(f"/api/org/batches/{b1['id']}", headers=_hdr(ba["token"])).status_code == 200
    ba_b = _mk(role="branch_admin", branch_id=b, username=f"bab_{suffix}")
    assert client.get(f"/api/org/batches/{b1['id']}", headers=_hdr(ba_b["token"])).status_code == 403
    assert client.get(f"/api/org/batches/{b2['id']}", headers=_hdr(tok)).status_code == 403


def test_rename_cascades_living_labels(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-rn-{suffix}"
    _ensure_branch(a, f"RN {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"barn_{suffix}")
    teacher = _mk(role="teacher", branch_id=a, username=f"trn_{suffix}")
    created = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"Old {suffix}", "branch_id": a, "teacher_id": teacher["id"]},
    ).json()
    student = client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "student",
            "email": f"rn_{suffix}@m8.test",
            "username": f"rn_{suffix}",
            "password": "Student1!",
            "full_name": "RN",
            "branch_id": a,
            "batch": f"Old {suffix}",
        },
    ).json()

    db = _TestSessionFactory()
    try:
        hw = HomeworkAssignment(
            id=f"hw-m8-{suffix}",
            title="HW",
            module="writing",
            scope="piece",
            source="bank",
            batch_label=f"Old {suffix}",
            created_by=teacher["id"],
        )
        hw.payload = {}
        db.add(hw)
        note = ClassNote(
            id=f"note-m8-{suffix}",
            title="N",
            description="",
            original_name="n.pdf",
            stored_name="n.pdf",
            page_count=1,
            created_by=teacher["id"],
        )
        note.unlocked_batches = [f"Old {suffix}"]
        note.unlocked_student_ids = []
        db.add(note)
        db.commit()
    finally:
        db.close()

    renamed = client.patch(
        f"/api/org/batches/{created['id']}",
        headers=_hdr(ba["token"]),
        json={"name": f"New {suffix}"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == f"New {suffix}"
    assert _user_db(student["id"])["batch"] == f"New {suffix}"
    assert f"New {suffix}" in _user_db(teacher["id"])["batches"]
    assert f"Old {suffix}" not in _user_db(teacher["id"])["batches"]

    db = _TestSessionFactory()
    try:
        hw = db.query(HomeworkAssignment).filter_by(id=f"hw-m8-{suffix}").first()
        assert hw.batch_label == f"New {suffix}"
        note = db.query(ClassNote).filter_by(id=f"note-m8-{suffix}").first()
        assert note.unlocked_batches == [f"New {suffix}"]
    finally:
        db.close()


def test_delete_unused_and_refuse_with_students(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-del-{suffix}"
    _ensure_branch(a, f"DEL {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"bad_{suffix}")
    teacher = _mk(role="teacher", branch_id=a, username=f"td_{suffix}")
    empty = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"Empty {suffix}", "branch_id": a, "teacher_id": teacher["id"]},
    ).json()
    filled = client.post(
        "/api/org/batches",
        headers=_hdr(ba["token"]),
        json={"name": f"Filled {suffix}", "branch_id": a},
    ).json()
    client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "student",
            "email": f"del_{suffix}@m8.test",
            "username": f"del_{suffix}",
            "password": "Student1!",
            "full_name": "Del",
            "branch_id": a,
            "batch": f"Filled {suffix}",
        },
    )
    assert client.delete(f"/api/org/batches/{filled['id']}", headers=_hdr(ba["token"])).status_code == 409
    assert _batch_db(filled["id"]) is not None

    deleted = client.delete(f"/api/org/batches/{empty['id']}", headers=_hdr(ba["token"]))
    assert deleted.status_code == 200
    assert _batch_db(empty["id"]) is None
    assert f"Empty {suffix}" not in _user_db(teacher["id"])["batches"]


def test_repair_teacher_csv_from_batch_pointers():
    from app.auth.access import repair_teacher_csv_from_batch_pointers

    suffix = uuid.uuid4().hex[:8]
    bid = f"m8-fix-{suffix}"
    label = f"Linked {suffix}"
    _ensure_branch(bid, f"Fix {suffix}")
    teacher = _mk(role="teacher", branch_id=bid, username=f"tfix_{suffix}", batches="")
    db = _TestSessionFactory()
    try:
        db.add(
            Batch(
                id=f"bfix-{suffix}",
                name=label,
                branch_id=bid,
                teacher_id=teacher["id"],
            )
        )
        db.commit()
        assert label not in (_user_db(teacher["id"])["batches"] or "")
        n = repair_teacher_csv_from_batch_pointers(db)
        db.commit()
        assert n >= 1
        assert label in _user_db(teacher["id"])["batches"]
        # Idempotent for this teacher: second pass leaves CSV unchanged.
        before = _user_db(teacher["id"])["batches"]
        repair_teacher_csv_from_batch_pointers(db)
        db.commit()
        assert _user_db(teacher["id"])["batches"] == before
    finally:
        db.close()


def test_counts_homework_notes_and_idor(client, monkeypatch):
    from app.config import settings
    from app.homework.models import HomeworkAssignment
    from app.notes.models import ClassNote

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m8-ia-{suffix}"
    b = f"m8-ib-{suffix}"
    _ensure_branch(a, f"IA {suffix}")
    _ensure_branch(b, f"IB {suffix}")
    ba_a = _mk(role="branch_admin", branch_id=a, username=f"bia_{suffix}")
    ba_b = _mk(role="branch_admin", branch_id=b, username=f"bib_{suffix}")
    t_a = _mk(role="teacher", branch_id=a, username=f"tia_{suffix}")
    t_b = _mk(role="teacher", branch_id=b, username=f"tib_{suffix}")
    batch_a = client.post(
        "/api/org/batches",
        headers=_hdr(ba_a["token"]),
        json={"name": f"AA {suffix}", "branch_id": a, "teacher_id": t_a["id"]},
    ).json()
    batch_b = client.post(
        "/api/org/batches",
        headers=_hdr(ba_b["token"]),
        json={"name": f"BB {suffix}", "branch_id": b},
    ).json()
    stu_a = client.post(
        "/api/org/users",
        headers=_hdr(ba_a["token"]),
        json={
            "role": "student",
            "email": f"ida_{suffix}@m8.test",
            "username": f"ida_{suffix}",
            "password": "Student1!",
            "full_name": "A",
            "branch_id": a,
            "batch": f"AA {suffix}",
        },
    ).json()
    # Unassigned student on A
    client.post(
        "/api/org/users",
        headers=_hdr(ba_a["token"]),
        json={
            "role": "student",
            "email": f"idu_{suffix}@m8.test",
            "username": f"idu_{suffix}",
            "password": "Student1!",
            "full_name": "U",
            "branch_id": a,
        },
    )
    # Same batch name on B must not inflate A's count
    client.post(
        "/api/org/users",
        headers=_hdr(ba_b["token"]),
        json={
            "role": "student",
            "email": f"idb_{suffix}@m8.test",
            "username": f"idb_{suffix}",
            "password": "Student1!",
            "full_name": "B",
            "branch_id": b,
            "batch": f"BB {suffix}",
        },
    )
    # Create same-named batch on B for cross-count trap
    client.post(
        "/api/org/batches",
        headers=_hdr(ba_b["token"]),
        json={"name": f"AA {suffix}", "branch_id": b},
    )
    client.post(
        "/api/org/users",
        headers=_hdr(ba_b["token"]),
        json={
            "role": "student",
            "email": f"idba_{suffix}@m8.test",
            "username": f"idba_{suffix}",
            "password": "Student1!",
            "full_name": "BA",
            "branch_id": b,
            "batch": f"AA {suffix}",
        },
    )

    listed = client.get("/api/org/batches", headers=_hdr(ba_a["token"]))
    assert listed.status_code == 200
    row = next(x for x in listed.json()["batches"] if x["id"] == batch_a["id"])
    assert row["students"] == 1
    assert row["teacherId"] == t_a["id"]
    assert listed.json()["unassigned_students"] == 1

    # Homework: foreign batch_label rejected
    hw_foreign = client.post(
        "/api/homework/teacher/assignments",
        headers={**_hdr(t_a["token"]), "X-Teacher-Id": t_a["id"]},
        json={
            "title": "HW",
            "module": "writing",
            "scope": "piece",
            "source": "bank",
            "batch_label": f"BB {suffix}",
            "student_ids": [],
            "payload": {"prompt": "x"},
        },
    )
    assert hw_foreign.status_code == 400, hw_foreign.text

    db = _TestSessionFactory()
    try:
        hw = HomeworkAssignment(
            id=f"hw-cnt-{suffix}",
            title="Own HW",
            module="writing",
            scope="piece",
            source="bank",
            batch_label=f"AA {suffix}",
            created_by=t_a["id"],
        )
        hw.payload = {"prompt": "x"}
        db.add(hw)
        note = ClassNote(
            id=f"note-cnt-{suffix}",
            title="N",
            description="",
            original_name="n.pdf",
            stored_name="n.pdf",
            page_count=1,
            created_by=t_a["id"],
        )
        note.unlocked_batches = [f"AA {suffix}"]
        note.unlocked_student_ids = []
        db.add(note)
        db.commit()
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"ida_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"idb_{suffix}", "password": "Student1!", "role": "student"},
    ).json()["access_token"]

    notes_a = client.get("/api/notes/student", headers=_hdr(tok_a))
    row_n = next(n for n in notes_a.json()["notes"] if n["id"] == f"note-cnt-{suffix}")
    assert row_n["unlocked"] is True
    notes_b = client.get("/api/notes/student", headers=_hdr(tok_b))
    assert not any(n["id"] == f"note-cnt-{suffix}" for n in notes_b.json()["notes"])
    assert (
        client.get(
            f"/api/notes/student/note-cnt-{suffix}",
            headers={**_hdr(tok_b), "X-Student-Id": stu_a["id"], "X-Student-Batch": f"AA {suffix}"},
        ).status_code
        == 403
    )

    # Cross-branch IDOR
    assert (
        client.patch(
            f"/api/org/batches/{batch_b['id']}",
            headers=_hdr(ba_a["token"]),
            json={"schedule": "hack"},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/org/batches/{batch_b['id']}", headers=_hdr(ba_a["token"])).status_code == 403
    assert client.get(f"/api/org/batches/{batch_b['id']}", headers=_hdr(t_a["token"])).status_code == 403
    assert (
        client.patch(
            f"/api/org/batches/{batch_a['id']}",
            headers=_hdr(ba_a["token"]),
            json={"teacher_id": t_b["id"]},
        ).status_code
        == 400
    )
    # BA A cannot assign Student B (foreign) to local batch via user patch
    stu_b = client.get("/api/org/users?role=student", headers=_hdr(ba_b["token"])).json()["users"]
    foreign_stu = next(u for u in stu_b if u["username"] == f"idb_{suffix}")
    assert (
        client.patch(
            f"/api/org/users/{foreign_stu['id']}",
            headers=_hdr(ba_a["token"]),
            json={"batch": f"AA {suffix}"},
        ).status_code
        == 403
    )
