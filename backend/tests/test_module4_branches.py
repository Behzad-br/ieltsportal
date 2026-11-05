"""Module 4 — Branch lifecycle: create/list/detail/update/deactivate/reactivate/counts/IDOR."""
from __future__ import annotations

import uuid

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from tests.conftest import _TestSessionFactory


def _mk_user(
    *,
    role: str,
    branch_id: str = "",
    username: str | None = None,
    password: str = "Passw0rd!",
) -> dict:
    db = _TestSessionFactory()
    try:
        uid = f"{role[:2]}-{uuid.uuid4().hex[:10]}"
        uname = username or uid
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m4.test",
            username=uname,
            password_hash=hash_password(password),
            full_name=uname,
            branch_id=branch_id,
            is_active=True,
        )
        db.add(row)
        db.commit()
        token = create_access_token(sub=row.id, role=row.role, extra={"branch_id": branch_id})
        return {"id": row.id, "username": uname, "password": password, "token": token, "branch_id": branch_id}
    finally:
        db.close()


def _hdr(user: dict) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _branch_db(branch_id: str) -> dict | None:
    db = _TestSessionFactory()
    try:
        b = db.query(Branch).filter_by(id=branch_id).first()
        if not b:
            return None
        return {
            "id": b.id,
            "name": b.name,
            "city": b.city,
            "is_active": bool(getattr(b, "is_active", True)),
        }
    finally:
        db.close()


def _ensure_branch(bid: str, name: str) -> None:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=bid).first():
            db.add(Branch(id=bid, name=name, city=name, is_active=True))
            db.commit()
    finally:
        db.close()


def test_sa_create_branch_persists(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    res = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"M4 Create {suffix}",
            "city": "Testville",
            "admins": [{"name": "Admin", "username": f"m4c_{suffix}", "password": "Campus1!"}],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"]
    assert body["isActive"] is True
    assert body["is_active"] is True
    row = _branch_db(body["id"])
    assert row is not None
    assert row["name"] == f"M4 Create {suffix}"
    assert row["is_active"] is True


def test_non_sa_cannot_create_branch(client):
    _ensure_branch("m4-ba-home", "M4 BA Home")
    ba = _mk_user(role="branch_admin", branch_id="m4-ba-home")
    teacher = _mk_user(role="teacher", branch_id="m4-ba-home")
    student = _mk_user(role="student", branch_id="m4-ba-home")
    payload = {
        "name": f"Forbidden {uuid.uuid4().hex[:6]}",
        "city": "X",
        "admins": [{"name": "A", "username": f"x{uuid.uuid4().hex[:6]}", "password": "Campus1!"}],
    }
    assert client.post("/api/org/branches", headers=_hdr(ba), json=payload).status_code == 403
    assert client.post("/api/org/branches", headers=_hdr(teacher), json=payload).status_code == 403
    assert client.post("/api/org/branches", headers=_hdr(student), json=payload).status_code == 403
    assert client.post("/api/org/branches", json=payload).status_code == 401


def test_invalid_and_duplicate_create(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    empty = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={"name": "   ", "city": "X", "admins": [{"name": "A", "username": f"e_{suffix}", "password": "Campus1!"}]},
    )
    assert empty.status_code == 400

    first = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Dup Name {suffix}",
            "city": "A",
            "id": f"m4-dup-{suffix}",
            "admins": [{"name": "A", "username": f"d1_{suffix}", "password": "Campus1!"}],
        },
    )
    assert first.status_code == 200

    dup_id = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Other Name {suffix}",
            "city": "B",
            "id": f"m4-dup-{suffix}",
            "admins": [{"name": "B", "username": f"d2_{suffix}", "password": "Campus1!"}],
        },
    )
    assert dup_id.status_code == 409

    dup_name = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Dup Name {suffix}",
            "city": "C",
            "admins": [{"name": "C", "username": f"d3_{suffix}", "password": "Campus1!"}],
        },
    )
    assert dup_name.status_code == 409


def test_list_scoped_no_cross_branch_leak(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"List A {suffix}",
            "city": "A",
            "admins": [{"name": "A", "username": f"la_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    b = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"List B {suffix}",
            "city": "B",
            "admins": [{"name": "B", "username": f"lb_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    ba = _mk_user(role="branch_admin", branch_id=a["id"], username=f"ba_list_{suffix}")

    sa_list = client.get("/api/org/branches", headers=_hdr(sa))
    assert sa_list.status_code == 200
    sa_ids = {x["id"] for x in sa_list.json()["branches"]}
    assert a["id"] in sa_ids and b["id"] in sa_ids

    ba_list = client.get("/api/org/branches", headers=_hdr(ba))
    assert ba_list.status_code == 200
    ba_ids = {x["id"] for x in ba_list.json()["branches"]}
    assert ba_ids == {a["id"]}


def test_detail_idor_and_sa_access(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Detail A {suffix}",
            "city": "A",
            "admins": [{"name": "A", "username": f"da_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    b = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Detail B {suffix}",
            "city": "B",
            "admins": [{"name": "B", "username": f"db_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    ba = _mk_user(role="branch_admin", branch_id=a["id"], username=f"ba_det_{suffix}")

    ok = client.get(f"/api/org/branches/{a['id']}", headers=_hdr(ba))
    assert ok.status_code == 200
    assert ok.json()["id"] == a["id"]

    denied = client.get(f"/api/org/branches/{b['id']}", headers=_hdr(ba))
    assert denied.status_code == 403

    sa_ok = client.get(f"/api/org/branches/{b['id']}", headers=_hdr(sa))
    assert sa_ok.status_code == 200


def test_update_persists_and_unauthorized_fails(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    created = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Upd {suffix}",
            "city": "Old",
            "admins": [{"name": "A", "username": f"up_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    ba = _mk_user(role="branch_admin", branch_id=created["id"], username=f"ba_up_{suffix}")

    denied = client.put(
        f"/api/org/branches/{created['id']}",
        headers=_hdr(ba),
        json={
            "name": f"Hacked {suffix}",
            "city": "Hack",
            "admins": [{"name": "A", "username": f"up_{suffix}", "password": ""}],
        },
    )
    assert denied.status_code == 403

    updated = client.put(
        f"/api/org/branches/{created['id']}",
        headers=_hdr(sa),
        json={
            "name": f"Upd Renamed {suffix}",
            "city": "NewCity",
            "admins": [{"name": "A", "username": f"up_{suffix}", "password": ""}],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["city"] == "NewCity"
    row = _branch_db(created["id"])
    assert row["name"] == f"Upd Renamed {suffix}"
    assert row["city"] == "NewCity"


def test_deactivate_reactivate_blocks_login_preserves_users(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    admin_user = f"life_{suffix}"
    created = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Life {suffix}",
            "city": "Life",
            "admins": [{"name": "Life Admin", "username": admin_user, "password": "Campus1!"}],
        },
    ).json()
    bid = created["id"]

    # Dependent teacher + batch + student (history stand-ins)
    db = _TestSessionFactory()
    try:
        db.add(
            User(
                id=f"t-{suffix}",
                role="teacher",
                email=f"t_{suffix}@m4.test",
                username=f"t_{suffix}",
                password_hash=hash_password("Campus1!"),
                full_name="Teacher",
                branch_id=bid,
                is_active=True,
            )
        )
        db.add(
            User(
                id=f"s-{suffix}",
                role="student",
                email=f"s_{suffix}@m4.test",
                username=f"s_{suffix}",
                password_hash=hash_password("Campus1!"),
                full_name="Student",
                branch_id=bid,
                batch="Batch X",
                is_active=True,
            )
        )
        db.add(Batch(id=f"batch-{suffix}", name="Batch X", branch_id=bid))
        db.commit()
    finally:
        db.close()

    login_ok = client.post(
        "/api/auth/login",
        json={"username": admin_user, "password": "Campus1!", "role": "branch_admin"},
    )
    assert login_ok.status_code == 200

    deact = client.patch(
        f"/api/org/branches/{bid}/status",
        headers=_hdr(sa),
        json={"is_active": False},
    )
    assert deact.status_code == 200
    assert deact.json()["isActive"] is False
    assert _branch_db(bid)["is_active"] is False

    login_blocked = client.post(
        "/api/auth/login",
        json={"username": admin_user, "password": "Campus1!", "role": "branch_admin"},
    )
    assert login_blocked.status_code == 403
    teacher_blocked = client.post(
        "/api/auth/login",
        json={"username": f"t_{suffix}", "password": "Campus1!", "role": "teacher"},
    )
    assert teacher_blocked.status_code == 403
    student_blocked = client.post(
        "/api/auth/login",
        json={"username": f"s_{suffix}", "password": "Campus1!", "role": "student"},
    )
    assert student_blocked.status_code == 403

    # History still present
    db = _TestSessionFactory()
    try:
        assert db.query(User).filter_by(branch_id=bid).count() >= 3
        assert db.query(Batch).filter_by(branch_id=bid).count() == 1
        assert db.query(Branch).filter_by(id=bid).first() is not None
    finally:
        db.close()

    # Soft delete endpoint also deactivates
    soft = client.delete(f"/api/org/branches/{bid}", headers=_hdr(sa))
    assert soft.status_code == 200
    assert soft.json()["policy"] == "soft_deactivate"
    assert _branch_db(bid)["is_active"] is False

    react = client.patch(
        f"/api/org/branches/{bid}/status",
        headers=_hdr(sa),
        json={"is_active": True},
    )
    assert react.status_code == 200
    assert _branch_db(bid)["is_active"] is True
    login_again = client.post(
        "/api/auth/login",
        json={"username": admin_user, "password": "Campus1!", "role": "branch_admin"},
    )
    assert login_again.status_code == 200


def test_counts_match_db(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    created = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Counts {suffix}",
            "city": "C",
            "admins": [{"name": "A", "username": f"cnt_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    bid = created["id"]
    db = _TestSessionFactory()
    try:
        for i in range(2):
            db.add(
                User(
                    id=f"ct-{suffix}-{i}",
                    role="teacher",
                    email=f"ct_{suffix}_{i}@m4.test",
                    username=f"ct_{suffix}_{i}",
                    password_hash=hash_password("x"),
                    branch_id=bid,
                    is_active=True,
                )
            )
        for i in range(3):
            db.add(
                User(
                    id=f"cs-{suffix}-{i}",
                    role="student",
                    email=f"cs_{suffix}_{i}@m4.test",
                    username=f"cs_{suffix}_{i}",
                    password_hash=hash_password("x"),
                    branch_id=bid,
                    is_active=True,
                )
            )
        db.add(User(
            id=f"cs-off-{suffix}",
            role="student",
            email=f"cs_off_{suffix}@m4.test",
            username=f"cs_off_{suffix}",
            password_hash=hash_password("x"),
            branch_id=bid,
            is_active=False,
        ))
        db.add(Batch(id=f"cb-{suffix}-1", name="B1", branch_id=bid))
        db.add(Batch(id=f"cb-{suffix}-2", name="B2", branch_id=bid))
        db.commit()
    finally:
        db.close()

    detail = client.get(f"/api/org/branches/{bid}", headers=_hdr(sa))
    assert detail.status_code == 200
    body = detail.json()
    assert body["teachers"] == 2
    assert body["students"] == 3  # inactive excluded
    assert body["batches"] == 2


def test_ba_cannot_mutate_foreign_branch_status(client):
    sa = _mk_user(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Stat A {suffix}",
            "city": "A",
            "admins": [{"name": "A", "username": f"sta_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    b = client.post(
        "/api/org/branches",
        headers=_hdr(sa),
        json={
            "name": f"Stat B {suffix}",
            "city": "B",
            "admins": [{"name": "B", "username": f"stb_{suffix}", "password": "Campus1!"}],
        },
    ).json()
    ba = _mk_user(role="branch_admin", branch_id=a["id"], username=f"ba_st_{suffix}")
    assert (
        client.patch(
            f"/api/org/branches/{b['id']}/status",
            headers=_hdr(ba),
            json={"is_active": False},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/org/branches/{b['id']}", headers=_hdr(ba)).status_code == 403
    assert _branch_db(b["id"])["is_active"] is True
