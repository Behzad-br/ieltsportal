"""Module 6 — Teacher lifecycle, branch scope, batch links, writing submission scope."""
from __future__ import annotations

import uuid

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password, verify_password
from app.config import settings
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
    is_active: bool = True,
    batches: str = "",
) -> dict:
    db = _TestSessionFactory()
    try:
        uid = f"{role[:2]}-{uuid.uuid4().hex[:10]}"
        uname = username or uid
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m6.test",
            username=uname,
            password_hash=hash_password(password),
            full_name=uname,
            branch_id=branch_id,
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
            "is_active": bool(u.is_active),
            "batches": u.batches or "",
            "password_hash": u.password_hash,
        }
    finally:
        db.close()


def test_sa_and_ba_create_teacher(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m6-a-{suffix}"
    b = f"m6-b-{suffix}"
    _ensure_branch(a, f"A {suffix}")
    _ensure_branch(b, f"B {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"ba_{suffix}")

    sa_create = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"t_sa_{suffix}@m6.test",
            "username": f"t_sa_{suffix}",
            "password": "Teacher1!",
            "full_name": "T SA",
            "branch_id": b,
        },
    )
    assert sa_create.status_code == 200, sa_create.text
    assert sa_create.json()["role"] == "teacher"
    assert sa_create.json()["branch_id"] == b
    assert "password_hash" not in sa_create.json()
    row = _user_db(sa_create.json()["id"])
    assert row and verify_password("Teacher1!", row["password_hash"])

    ba_ok = client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "teacher",
            "email": f"t_ba_{suffix}@m6.test",
            "username": f"t_ba_{suffix}",
            "password": "Teacher1!",
            "full_name": "T BA",
            "branch_id": a,
        },
    )
    assert ba_ok.status_code == 200, ba_ok.text
    assert ba_ok.json()["branch_id"] == a

    ba_foreign = client.post(
        "/api/org/users",
        headers=_hdr(ba["token"]),
        json={
            "role": "teacher",
            "email": f"t_fx_{suffix}@m6.test",
            "username": f"t_fx_{suffix}",
            "password": "Teacher1!",
            "full_name": "T FX",
            "branch_id": b,
        },
    )
    assert ba_foreign.status_code == 403


def test_teacher_student_cannot_create_teacher(client):
    suffix = uuid.uuid4().hex[:8]
    bid = f"m6-deny-{suffix}"
    _ensure_branch(bid, f"Deny {suffix}")
    teacher = _mk(role="teacher", branch_id=bid)
    student = _mk(role="student", branch_id=bid)
    payload = {
        "role": "teacher",
        "email": f"no_{suffix}@m6.test",
        "username": f"no_{suffix}",
        "password": "Teacher1!",
        "full_name": "Nope",
        "branch_id": bid,
    }
    assert client.post("/api/org/users", headers=_hdr(teacher["token"]), json=payload).status_code == 403
    assert client.post("/api/org/users", headers=_hdr(student["token"]), json=payload).status_code == 403


def test_create_rejects_inactive_and_duplicate(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    active = f"m6-ok-{suffix}"
    inactive = f"m6-off-{suffix}"
    _ensure_branch(active, f"OK {suffix}", active=True)
    _ensure_branch(inactive, f"Off {suffix}", active=False)

    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(sa["token"]),
            json={
                "role": "teacher",
                "email": f"off_{suffix}@m6.test",
                "username": f"off_{suffix}",
                "password": "Teacher1!",
                "full_name": "X",
                "branch_id": inactive,
            },
        ).status_code
        == 400
    )

    first = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"dup_{suffix}@m6.test",
            "username": f"dup_{suffix}",
            "password": "Teacher1!",
            "full_name": "X",
            "branch_id": active,
        },
    )
    assert first.status_code == 200
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(sa["token"]),
            json={
                "role": "teacher",
                "email": f"dup_{suffix}@m6.test",
                "username": f"dup2_{suffix}",
                "password": "Teacher1!",
                "full_name": "X",
                "branch_id": active,
            },
        ).status_code
        == 409
    )


def test_login_deactivate_branch_and_jwt(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m6-life-{suffix}"
    _ensure_branch(bid, f"Life {suffix}")
    created = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"life_{suffix}@m6.test",
            "username": f"life_{suffix}",
            "password": "Teacher1!",
            "full_name": "Life",
            "branch_id": bid,
        },
    ).json()

    login = client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Teacher1!", "role": "teacher"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    client.patch(f"/api/org/users/{created['id']}", headers=_hdr(sa["token"]), json={"is_active": False})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).status_code == 403
    assert client.get("/api/org/users", headers=_hdr(token)).status_code == 403

    client.patch(f"/api/org/users/{created['id']}", headers=_hdr(sa["token"]), json={"is_active": True})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).status_code == 200

    client.patch(f"/api/org/branches/{bid}/status", headers=_hdr(sa["token"]), json={"is_active": False})
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).status_code == 403
    client.patch(f"/api/org/branches/{bid}/status", headers=_hdr(sa["token"]), json={"is_active": True})


def test_list_detail_update_idor(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m6-la-{suffix}"
    b = f"m6-lb-{suffix}"
    _ensure_branch(a, f"LA {suffix}")
    _ensure_branch(b, f"LB {suffix}")
    ba = _mk(role="branch_admin", branch_id=a, username=f"bal_{suffix}")
    t_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"ta_{suffix}@m6.test",
            "username": f"ta_{suffix}",
            "password": "Teacher1!",
            "full_name": "TA",
            "branch_id": a,
        },
    ).json()
    t_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"tb_{suffix}@m6.test",
            "username": f"tb_{suffix}",
            "password": "Teacher1!",
            "full_name": "TB",
            "branch_id": b,
        },
    ).json()
    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"ta_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).json()["access_token"]

    sa_list = client.get("/api/org/users?role=teacher", headers=_hdr(sa["token"]))
    assert {u["id"] for u in sa_list.json()["users"]} >= {t_a["id"], t_b["id"]}

    ba_list = client.get("/api/org/users?role=teacher", headers=_hdr(ba["token"]))
    assert ba_list.status_code == 200
    ids = {u["id"] for u in ba_list.json()["users"]}
    assert t_a["id"] in ids and t_b["id"] not in ids

    assert client.get(f"/api/org/users/{t_a['id']}", headers=_hdr(tok_a)).status_code == 200
    assert client.get(f"/api/org/users/{t_b['id']}", headers=_hdr(tok_a)).status_code == 403
    assert client.get(f"/api/org/users/{t_b['id']}", headers=_hdr(ba["token"])).status_code == 403

    # Role escalation / branch spoof
    assert (
        client.patch(
            f"/api/org/users/{t_a['id']}",
            headers=_hdr(tok_a),
            json={"branch_id": b, "full_name": "Hacked"},
        ).status_code
        == 403
    )
    assert _user_db(t_a["id"])["branch_id"] == a

    # Peer teacher mutate
    assert (
        client.patch(
            f"/api/org/users/{t_b['id']}",
            headers=_hdr(tok_a),
            json={"full_name": "Nope"},
        ).status_code
        == 403
    )


def test_batch_assignment_sync_and_reassignment(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m6-ba-{suffix}"
    b = f"m6-bb-{suffix}"
    _ensure_branch(a, f"BA {suffix}")
    _ensure_branch(b, f"BB {suffix}")
    db = _TestSessionFactory()
    try:
        db.add(Batch(id=f"batch-{suffix}", name=f"Morning {suffix}", branch_id=a, teacher_id=""))
        db.commit()
    finally:
        db.close()

    teacher = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"bt_{suffix}@m6.test",
            "username": f"bt_{suffix}",
            "password": "Teacher1!",
            "full_name": "BT",
            "branch_id": a,
        },
    ).json()

    patched = client.patch(
        f"/api/org/users/{teacher['id']}",
        headers=_hdr(sa["token"]),
        json={"batches": f"Morning {suffix}"},
    )
    assert patched.status_code == 200, patched.text
    db = _TestSessionFactory()
    try:
        batch = db.query(Batch).filter_by(id=f"batch-{suffix}").first()
        assert batch.teacher_id == teacher["id"]
    finally:
        db.close()

    moved = client.patch(
        f"/api/org/users/{teacher['id']}",
        headers=_hdr(sa["token"]),
        json={"branch_id": b},
    )
    assert moved.status_code == 200
    assert _user_db(teacher["id"])["branch_id"] == b
    assert _user_db(teacher["id"])["batches"] == ""
    db = _TestSessionFactory()
    try:
        batch = db.query(Batch).filter_by(id=f"batch-{suffix}").first()
        assert batch.teacher_id == ""
    finally:
        db.close()


def test_teacher_student_scope_and_homework(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m6-ha-{suffix}"
    b = f"m6-hb-{suffix}"
    _ensure_branch(a, f"HA {suffix}")
    _ensure_branch(b, f"HB {suffix}")
    db = _TestSessionFactory()
    try:
        db.add(Batch(id=f"hbm-{suffix}", name=f"Morning {suffix}", branch_id=a))
        db.commit()
    finally:
        db.close()
    t_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"hta_{suffix}@m6.test",
            "username": f"hta_{suffix}",
            "password": "Teacher1!",
            "full_name": "HTA",
            "branch_id": a,
        },
    ).json()
    s_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"hsa_{suffix}@m6.test",
            "username": f"hsa_{suffix}",
            "password": "Student1!",
            "full_name": "HSA",
            "branch_id": a,
            "batch": f"Morning {suffix}",
        },
    ).json()
    s_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "student",
            "email": f"hsb_{suffix}@m6.test",
            "username": f"hsb_{suffix}",
            "password": "Student1!",
            "full_name": "HSB",
            "branch_id": b,
        },
    ).json()
    tok = client.post(
        "/api/auth/login",
        json={"username": f"hta_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).json()["access_token"]

    users = client.get("/api/org/users", headers=_hdr(tok))
    assert users.status_code == 200
    ids = {u["id"] for u in users.json()["users"]}
    assert s_a["id"] in ids and s_b["id"] not in ids
    assert all(u["role"] == "student" for u in users.json()["users"])

    ok = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok),
        json={
            "title": "HW A",
            "module": "writing",
            "student_ids": [s_a["id"]],
            "payload": {"prompt": "x"},
        },
    )
    assert ok.status_code == 200, ok.text

    denied = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(tok),
        json={
            "title": "HW B",
            "module": "writing",
            "student_ids": [s_b["id"]],
            "payload": {"prompt": "x"},
        },
    )
    assert denied.status_code == 403

    # Teacher cannot promote self
    assert (
        client.post(
            "/api/org/users",
            headers=_hdr(tok),
            json={
                "role": "branch_admin",
                "email": f"promo_{suffix}@m6.test",
                "username": f"promo_{suffix}",
                "password": "Teacher1!",
                "full_name": "Promo",
                "branch_id": a,
            },
        ).status_code
        == 403
    )
    _ = t_a  # created


def test_writing_submissions_branch_scoped_with_jwt(client, monkeypatch):
    monkeypatch.setattr(settings, "WRITING_ADMIN_TOKEN", "legacy-secret")
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m6-wa-{suffix}"
    b = f"m6-wb-{suffix}"
    _ensure_branch(a, f"WA {suffix}")
    _ensure_branch(b, f"WB {suffix}")
    t_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "teacher",
            "email": f"wta_{suffix}@m6.test",
            "username": f"wta_{suffix}",
            "password": "Teacher1!",
            "full_name": "WTA",
            "branch_id": a,
        },
    ).json()
    s_a = _mk(role="student", branch_id=a, username=f"wsa_{suffix}")
    s_b = _mk(role="student", branch_id=b, username=f"wsb_{suffix}")

    db = _TestSessionFactory()
    try:
        q = WritingQuestion(
            id=f"wq-{suffix}",
            public_id=f"pub-{suffix}",
            prompt="Describe a place",
            test_type="academic",
            task_number=2,
            question_type="essay",
            status="published",
        )
        db.add(q)
        db.add(
            WritingAttempt(
                id=f"wa-a-{suffix}",
                student_id=s_a["id"],
                question_id=q.id,
                status="submitted",
                answer_text="hello",
                word_count=1,
            )
        )
        db.add(
            WritingAttempt(
                id=f"wa-b-{suffix}",
                student_id=s_b["id"],
                question_id=q.id,
                status="submitted",
                answer_text="foreign",
                word_count=1,
            )
        )
        db.commit()
    finally:
        db.close()

    tok = client.post(
        "/api/auth/login",
        json={"username": f"wta_{suffix}", "password": "Teacher1!", "role": "teacher"},
    ).json()["access_token"]

    # JWT preferred even when WRITING_ADMIN_TOKEN is configured
    listed = client.get("/api/writing/admin/submissions", headers=_hdr(tok))
    assert listed.status_code == 200, listed.text
    ids = {x["id"] for x in listed.json()["submissions"]}
    assert f"wa-a-{suffix}" in ids
    assert f"wa-b-{suffix}" not in ids

    assert client.get(f"/api/writing/admin/submissions/wa-b-{suffix}", headers=_hdr(tok)).status_code == 403
    assert client.get(f"/api/writing/admin/submissions/wa-a-{suffix}", headers=_hdr(tok)).status_code == 200

    # Legacy token still works unscoped
    token_list = client.get(
        "/api/writing/admin/submissions",
        headers={"X-Admin-Token": "legacy-secret"},
    )
    assert token_list.status_code == 200
    token_ids = {x["id"] for x in token_list.json()["submissions"]}
    assert f"wa-a-{suffix}" in token_ids and f"wa-b-{suffix}" in token_ids
    _ = t_a
