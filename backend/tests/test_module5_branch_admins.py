"""Module 5 — Branch Admin lifecycle, credentials, IDOR, reassignment, deactivation."""
from __future__ import annotations

import uuid

from app.auth.models import Branch, User
from app.auth.security import create_access_token, hash_password, verify_password
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
) -> dict:
    db = _TestSessionFactory()
    try:
        uid = f"{role[:2]}-{uuid.uuid4().hex[:10]}"
        uname = username or uid
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m5.test",
            username=uname,
            password_hash=hash_password(password),
            full_name=uname,
            branch_id=branch_id,
            is_active=is_active,
        )
        db.add(row)
        db.commit()
        token = create_access_token(sub=row.id, role=row.role, extra={"branch_id": branch_id})
        return {
            "id": row.id,
            "username": uname,
            "password": password,
            "token": token,
            "branch_id": branch_id,
        }
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
            "email": u.email,
            "username": u.username,
            "password_hash": u.password_hash,
            "full_name": u.full_name,
        }
    finally:
        db.close()


def test_sa_creates_branch_admin_persists(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m5-br-{suffix}"
    _ensure_branch(bid, f"M5 Campus {suffix}")

    res = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"ba_{suffix}@m5.test",
            "username": f"ba_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "BA One",
            "branch_id": bid,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["role"] == "branch_admin"
    assert body["branch_id"] == bid
    assert "password" not in body
    assert "password_hash" not in body

    row = _user_db(body["id"])
    assert row is not None
    assert row["role"] == "branch_admin"
    assert row["branch_id"] == bid
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert verify_password("CampusAdmin1!", row["password_hash"])
    assert "CampusAdmin1!" not in row["password_hash"]


def test_non_sa_cannot_create_branch_admin(client):
    suffix = uuid.uuid4().hex[:8]
    bid = f"m5-deny-{suffix}"
    _ensure_branch(bid, f"Deny {suffix}")
    ba = _mk(role="branch_admin", branch_id=bid)
    teacher = _mk(role="teacher", branch_id=bid)
    student = _mk(role="student", branch_id=bid)
    payload = {
        "role": "branch_admin",
        "email": f"x_{suffix}@m5.test",
        "username": f"x_{suffix}",
        "password": "CampusAdmin1!",
        "full_name": "Nope",
        "branch_id": bid,
    }
    assert client.post("/api/org/users", headers=_hdr(ba["token"]), json=payload).status_code == 403
    assert client.post("/api/org/users", headers=_hdr(teacher["token"]), json=payload).status_code == 403
    assert client.post("/api/org/users", headers=_hdr(student["token"]), json=payload).status_code == 403
    assert client.post("/api/org/users", json=payload).status_code == 401


def test_create_rejects_missing_inactive_duplicate(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    active = f"m5-ok-{suffix}"
    inactive = f"m5-off-{suffix}"
    _ensure_branch(active, f"OK {suffix}", active=True)
    _ensure_branch(inactive, f"Off {suffix}", active=False)

    missing = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"miss_{suffix}@m5.test",
            "username": f"miss_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "X",
            "branch_id": f"no-such-{suffix}",
        },
    )
    assert missing.status_code == 404

    off = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"off_{suffix}@m5.test",
            "username": f"off_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "X",
            "branch_id": inactive,
        },
    )
    assert off.status_code == 400

    first = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"dup_{suffix}@m5.test",
            "username": f"dup_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "X",
            "branch_id": active,
        },
    )
    assert first.status_code == 200
    dup = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"dup_{suffix}@m5.test",
            "username": f"dup2_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "X",
            "branch_id": active,
        },
    )
    assert dup.status_code == 409


def test_login_matrix_account_and_branch_active(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m5-life-{suffix}"
    _ensure_branch(bid, f"Life {suffix}", active=True)
    created = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"life_{suffix}@m5.test",
            "username": f"life_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "Life",
            "branch_id": bid,
        },
    ).json()

    ok = client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    )
    assert ok.status_code == 200
    token = ok.json()["access_token"]

    # Deactivate account
    deact = client.patch(
        f"/api/org/users/{created['id']}",
        headers=_hdr(sa["token"]),
        json={"is_active": False},
    )
    assert deact.status_code == 200
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).status_code == 403
    # Old JWT rejected
    assert client.get("/api/org/users", headers=_hdr(token)).status_code == 403

    # Reactivate account
    client.patch(
        f"/api/org/users/{created['id']}",
        headers=_hdr(sa["token"]),
        json={"is_active": True},
    )
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).status_code == 200

    # Active BA + inactive branch
    client.patch(
        f"/api/org/branches/{bid}/status",
        headers=_hdr(sa["token"]),
        json={"is_active": False},
    )
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).status_code == 403

    client.patch(
        f"/api/org/branches/{bid}/status",
        headers=_hdr(sa["token"]),
        json={"is_active": True},
    )
    assert client.post(
        "/api/auth/login",
        json={"username": f"life_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).status_code == 200


def test_list_detail_idor(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m5-a-{suffix}"
    b = f"m5-b-{suffix}"
    _ensure_branch(a, f"A {suffix}")
    _ensure_branch(b, f"B {suffix}")
    ba_a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"aa_{suffix}@m5.test",
            "username": f"aa_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "A",
            "branch_id": a,
        },
    ).json()
    ba_b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"bb_{suffix}@m5.test",
            "username": f"bb_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "B",
            "branch_id": b,
        },
    ).json()
    login_a = client.post(
        "/api/auth/login",
        json={"username": f"aa_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).json()
    token_a = login_a["access_token"]
    teacher = _mk(role="teacher", branch_id=a)
    student = _mk(role="student", branch_id=a)

    sa_list = client.get("/api/org/users?role=branch_admin", headers=_hdr(sa["token"]))
    assert sa_list.status_code == 200
    ids = {u["id"] for u in sa_list.json()["users"]}
    assert ba_a["id"] in ids and ba_b["id"] in ids

    assert client.get("/api/org/users?role=branch_admin", headers=_hdr(token_a)).status_code == 403
    ba_users = client.get("/api/org/users", headers=_hdr(token_a))
    assert ba_users.status_code == 200
    for u in ba_users.json()["users"]:
        assert u["role"] in {"teacher", "student"}
        assert u["branch_id"] == a

    assert client.get(f"/api/org/users/{ba_a['id']}", headers=_hdr(sa["token"])).status_code == 200
    assert client.get(f"/api/org/users/{ba_a['id']}", headers=_hdr(token_a)).status_code == 200  # self
    assert client.get(f"/api/org/users/{ba_b['id']}", headers=_hdr(token_a)).status_code == 403
    assert client.get(f"/api/org/users/{ba_a['id']}", headers=_hdr(teacher["token"])).status_code == 403
    assert client.get(f"/api/org/users/{ba_a['id']}", headers=_hdr(student["token"])).status_code == 403


def test_ba_cannot_mutate_peer_or_escalate(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m5-peer-{suffix}"
    _ensure_branch(bid, f"Peer {suffix}")
    a = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"p1_{suffix}@m5.test",
            "username": f"p1_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "P1",
            "branch_id": bid,
        },
    ).json()
    b = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"p2_{suffix}@m5.test",
            "username": f"p2_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "P2",
            "branch_id": bid,
        },
    ).json()
    token_a = client.post(
        "/api/auth/login",
        json={"username": f"p1_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).json()["access_token"]

    assert (
        client.patch(
            f"/api/org/users/{b['id']}",
            headers=_hdr(token_a),
            json={"is_active": False},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/org/users/{b['id']}",
            headers=_hdr(token_a),
            json={"full_name": "Hacked"},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/org/users/{b['id']}", headers=_hdr(token_a)).status_code == 403

    # Self branch spoof / role fields ignored or blocked
    spoof = client.patch(
        f"/api/org/users/{a['id']}",
        headers=_hdr(token_a),
        json={"branch_id": "other-branch", "full_name": "Still Me"},
    )
    assert spoof.status_code == 403
    assert _user_db(a["id"])["branch_id"] == bid

    # Multiple admins both login
    assert client.post(
        "/api/auth/login",
        json={"username": f"p2_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).status_code == 200


def test_sa_reassignment_updates_db_scope(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    a = f"m5-ra-{suffix}"
    b = f"m5-rb-{suffix}"
    _ensure_branch(a, f"RA {suffix}")
    _ensure_branch(b, f"RB {suffix}")
    # Seed a student on A for scope check
    _mk(role="student", branch_id=a, username=f"st_a_{suffix}")
    _mk(role="student", branch_id=b, username=f"st_b_{suffix}")

    ba = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"re_{suffix}@m5.test",
            "username": f"re_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "Re",
            "branch_id": a,
        },
    ).json()
    token = client.post(
        "/api/auth/login",
        json={"username": f"re_{suffix}", "password": "CampusAdmin1!", "role": "branch_admin"},
    ).json()["access_token"]

    before = client.get("/api/org/users", headers=_hdr(token))
    assert before.status_code == 200
    assert all(u["branch_id"] == a for u in before.json()["users"])

    moved = client.patch(
        f"/api/org/users/{ba['id']}",
        headers=_hdr(sa["token"]),
        json={"branch_id": b},
    )
    assert moved.status_code == 200, moved.text
    assert _user_db(ba["id"])["branch_id"] == b

    # Same JWT: backend reloads user from DB → Branch B scope
    after = client.get("/api/org/users", headers=_hdr(token))
    assert after.status_code == 200
    assert all(u["branch_id"] == b for u in after.json()["users"])
    assert client.get(f"/api/org/branches/{a}", headers=_hdr(token)).status_code == 403
    assert client.get(f"/api/org/branches/{b}", headers=_hdr(token)).status_code == 200


def test_zero_admin_allowed_via_soft_deactivate(client):
    sa = _mk(role="super_admin")
    suffix = uuid.uuid4().hex[:8]
    bid = f"m5-zero-{suffix}"
    _ensure_branch(bid, f"Zero {suffix}")
    only = client.post(
        "/api/org/users",
        headers=_hdr(sa["token"]),
        json={
            "role": "branch_admin",
            "email": f"z_{suffix}@m5.test",
            "username": f"z_{suffix}",
            "password": "CampusAdmin1!",
            "full_name": "Only",
            "branch_id": bid,
        },
    ).json()
    soft = client.delete(f"/api/org/users/{only['id']}", headers=_hdr(sa["token"]))
    assert soft.status_code == 200
    assert soft.json().get("policy") == "soft_deactivate"
    assert _user_db(only["id"])["is_active"] is False
    # Branch still exists/active
    detail = client.get(f"/api/org/branches/{bid}", headers=_hdr(sa["token"]))
    assert detail.status_code == 200
    assert detail.json()["isActive"] is True
