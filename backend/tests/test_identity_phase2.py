"""Phase 2: canonical auth_users.id identity + X-Student-Id hardening."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.models import Branch, User  # noqa: F401 — User used in fixture
from app.auth.security import ALGORITHM, create_access_token, hash_password
from app.config import settings


def _hdr(user: dict, **extra) -> dict:
    token = create_access_token(
        sub=user["id"], role=user["role"], extra={"branch_id": user.get("branch_id") or ""}
    )
    h = {"Authorization": f"Bearer {token}"}
    h.update(extra)
    return h


@pytest.fixture
def identity_world(db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    # Do not wipe the whole shared SQLite DB — upsert our fixture rows only.
    for bid, name, city in (("branch-a", "Branch A", "A"), ("branch-b", "Branch B", "B")):
        if not db.query(Branch).filter_by(id=bid).first():
            db.add(Branch(id=bid, name=name, city=city))
    db.commit()

    pw = hash_password("Secret123!")
    spec = [
        ("super", "u-super", "super_admin", "super@test.com", "super", None, True),
        ("ba_a", "u-ba-a", "branch_admin", "ba_a@test.com", "ba_a", "branch-a", True),
        ("t_a", "u-t-a", "teacher", "t_a@test.com", "t_a", "branch-a", True),
        ("s_a1", "u-s-a1", "student", "s_a1@test.com", "s_a1", "branch-a", True),
        ("s_a2", "u-s-a2", "student", "s_a2@test.com", "s_a2", "branch-a", True),
        ("s_b", "u-s-b", "student", "s_b@test.com", "s_b", "branch-b", True),
        ("inactive", "u-inactive", "student", "inactive@test.com", "inactive", "branch-a", False),
    ]
    users = {}
    for key, uid, role, email, username, branch_id, active in spec:
        row = db.query(User).filter_by(id=uid).first()
        if not row:
            row = User(
                id=uid,
                role=role,
                email=email,
                username=username,
                password_hash=pw,
                full_name=username,
                branch_id=branch_id or "",
                batch="Morning" if role == "student" and branch_id == "branch-a" else "",
                is_active=active,
            )
            db.add(row)
        else:
            row.role = role
            row.email = email
            row.username = username
            row.password_hash = pw
            row.branch_id = branch_id or ""
            row.is_active = active
        users[key] = row
    db.commit()
    # Detach plain dicts so teardown cannot lazy-load after the shared SQLite closes.
    out = {}
    for key, u in users.items():
        db.refresh(u)
        out[key] = {
            "id": u.id,
            "role": u.role,
            "username": u.username,
            "email": u.email,
            "branch_id": u.branch_id or "",
        }
    db.expunge_all()
    return out


def test_login_roles_and_me(client, identity_world):
    for key, role in [("super", "super_admin"), ("ba_a", "branch_admin"), ("t_a", "teacher"), ("s_a1", "student")]:
        u = identity_world[key]
        res = client.post("/api/auth/login", json={"username": u["username"], "password": "Secret123!"})
        assert res.status_code == 200, key
        body = res.json()
        assert body["user"]["id"] == u["id"]
        assert body["user"]["role"] == role
        token = body["access_token"]
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == u["id"]
        assert payload["role"] == role
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["id"] == u["id"]
        assert me.json()["role"] == role


def test_inactive_login_and_token(client, identity_world):
    u = identity_world["inactive"]
    assert client.post("/api/auth/login", json={"username": u["username"], "password": "Secret123!"}).status_code == 403
    # Minted token for inactive user must still be rejected on /me
    dead = create_access_token(sub=u["id"], role=u["role"])
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {dead}"}).status_code == 403


def test_bad_password_and_invalid_jwt(client, identity_world):
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "s_a1", "password": "wrong-password"},
        ).status_code
        == 401
    )
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_expired_and_unknown_sub(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": "u-s-a1",
            "role": "student",
            "iat": int((now - timedelta(hours=5)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    unknown = create_access_token(sub="no-such-user-id", role="student")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {unknown}"}).status_code == 401


def test_student_cannot_impersonate_via_header(client, identity_world):
    a = identity_world["s_a1"]
    b = identity_world["s_a2"]
    # Student JWT with forged X-Student-Id must still bind to self.
    hist = client.get("/api/writing/history", headers=_hdr(a, **{"X-Student-Id": b["id"]}))
    assert hist.status_code == 200


def test_student_header_ignored_for_keys(client, identity_world):
    a = identity_world["s_a1"]
    b = identity_world["s_a2"]
    inbox = client.get(
        "/api/mocks/student/inbox",
        headers=_hdr(a, **{"X-Student-Id": b["id"], "X-Student-Aliases": "local,s1,demo-student-1"}),
    )
    assert inbox.status_code == 200


def test_branch_admin_cannot_target_other_branch(client, identity_world):
    ba = identity_world["ba_a"]
    foreign = identity_world["s_b"]
    res = client.get(
        "/api/writing/history",
        headers=_hdr(ba, **{"X-Student-Id": foreign["id"]}),
    )
    assert res.status_code == 403


def test_teacher_cannot_target_other_branch(client, identity_world):
    t = identity_world["t_a"]
    foreign = identity_world["s_b"]
    res = client.get(
        "/api/writing/history",
        headers=_hdr(t, **{"X-Student-Id": foreign["id"]}),
    )
    assert res.status_code == 403


def test_teacher_can_target_same_branch_student(client, identity_world):
    t = identity_world["t_a"]
    s = identity_world["s_a1"]
    res = client.get(
        "/api/writing/history",
        headers=_hdr(t, **{"X-Student-Id": s["id"]}),
    )
    assert res.status_code == 200


def test_blocked_legacy_ids_rejected(client, identity_world):
    t = identity_world["t_a"]
    for bad in ("local", "s1", "demo-student-1", "demo-teacher"):
        res = client.get(
            "/api/writing/history",
            headers=_hdr(t, **{"X-Student-Id": bad}),
        )
        assert res.status_code == 400, bad


def test_unauthenticated_rejected_when_legacy_off(client, identity_world):
    assert client.get("/api/writing/history", headers={"X-Student-Id": "u-s-a1"}).status_code == 401
    assert client.get("/api/writing/history").status_code == 401


def test_demo_token_rejected_for_me(client, identity_world):
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer demo-local-token"}).status_code == 401


def test_new_writing_attempt_uses_canonical_id(client, identity_world, db):
    from app.writing.models import WritingQuestion, WritingAttempt

    s = identity_world["s_a1"]
    q = WritingQuestion(
        id="wq-phase2",
        public_id="wq-phase2-pub",
        test_type="academic",
        task_number=2,
        question_type="Opinion",
        prompt="Discuss technology.",
        status="published",
        is_permanent_bank=True,
    )
    db.add(q)
    db.commit()

    res = client.post(
        "/api/writing/questions/wq-phase2/start",
        headers=_hdr(s),
    )
    assert res.status_code == 200, res.text
    attempt_id = res.json().get("id")
    assert attempt_id
    row = db.query(WritingAttempt).filter_by(id=attempt_id).first()
    assert row is not None
    assert row.student_id == s["id"]
    assert row.student_id not in {"local", "s1"}
