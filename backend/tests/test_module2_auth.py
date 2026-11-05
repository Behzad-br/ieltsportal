"""Module 2 — Authentication matrix (JWT + DB-backed /me)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth.models import Branch, User
from app.auth.security import ALGORITHM, create_access_token, hash_password
from app.config import settings


@pytest.fixture
def auth_matrix(db, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    if not db.query(Branch).filter_by(id="m2-branch").first():
        db.add(Branch(id="m2-branch", name="Module2 Branch", city="Test"))
        db.commit()

    pw = hash_password("Module2Pass!")
    specs = [
        ("m2-super", "super_admin", "m2super@test.com", "m2super", "", True),
        ("m2-ba", "branch_admin", "m2ba@test.com", "m2ba", "m2-branch", True),
        ("m2-teacher", "teacher", "m2teacher@test.com", "m2teacher", "m2-branch", True),
        ("m2-student", "student", "m2student@test.com", "m2student", "m2-branch", True),
        ("m2-inactive", "student", "m2inactive@test.com", "m2inactive", "m2-branch", False),
    ]
    users = {}
    for uid, role, email, username, branch_id, active in specs:
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
                is_active=active,
            )
            db.add(row)
        else:
            row.role = role
            row.email = email
            row.username = username
            row.password_hash = pw
            row.branch_id = branch_id
            row.is_active = active
        users[role if active else "inactive"] = {
            "id": uid,
            "role": role,
            "username": username,
            "email": email,
            "branch_id": branch_id,
        }
    db.commit()
    db.expunge_all()
    return users


def test_each_role_login_and_me_matches_db(client, auth_matrix):
    for role in ("super_admin", "branch_admin", "teacher", "student"):
        u = auth_matrix[role]
        res = client.post(
            "/api/auth/login",
            json={"username": u["username"], "password": "Module2Pass!", "role": role},
        )
        assert res.status_code == 200, role
        body = res.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["id"] == u["id"]
        assert body["user"]["role"] == role
        payload = jwt.decode(body["access_token"], settings.JWT_SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == u["id"]
        assert payload["role"] == role
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["id"] == u["id"]
        assert me.json()["role"] == role
        assert me.json()["username"] == u["username"]


def test_bad_password_and_unknown_user(client, auth_matrix):
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "m2student", "password": "WrongPass!"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "no-such-user-xyz", "password": "Module2Pass!"},
        ).status_code
        == 401
    )


def test_inactive_user_login_forbidden(client, auth_matrix):
    u = auth_matrix["inactive"]
    assert (
        client.post(
            "/api/auth/login",
            json={"username": u["username"], "password": "Module2Pass!"},
        ).status_code
        == 403
    )


def test_missing_and_malformed_authorization(client, auth_matrix):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Token abc"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer "}).status_code == 401


def test_invalid_and_expired_token(client, auth_matrix, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401

    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {
            "sub": auth_matrix["student"]["id"],
            "role": "student",
            "iat": int((now - timedelta(hours=5)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_jwt_role_claim_ignored_for_authorization(client, auth_matrix, db):
    """Forged role claim must not elevate privileges; DB role wins."""
    student = auth_matrix["student"]
    forged = create_access_token(sub=student["id"], role="super_admin")
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert me.status_code == 200
    assert me.json()["role"] == "student"
    # Org list requires staff roles — student DB role must stay 403.
    org = client.get("/api/org/users", headers={"Authorization": f"Bearer {forged}"})
    assert org.status_code == 403


def test_wrong_role_hint_does_not_login_other_role(client, auth_matrix):
    student = auth_matrix["student"]
    res = client.post(
        "/api/auth/login",
        json={"username": student["username"], "password": "Module2Pass!", "role": "super_admin"},
    )
    assert res.status_code == 401


def test_short_password_rejected_by_validation(client):
    res = client.post("/api/auth/login", json={"username": "anyone", "password": "123"})
    assert res.status_code == 422


def test_demo_token_and_protected_org_require_auth(client, auth_matrix, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer demo-local-token"}).status_code == 401
    assert client.get("/api/org/users").status_code == 401


def test_deactivate_after_login_revokes_me(client, auth_matrix, db):
    u = auth_matrix["teacher"]
    login = client.post(
        "/api/auth/login",
        json={"username": u["username"], "password": "Module2Pass!"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    row = db.query(User).filter_by(id=u["id"]).first()
    row.is_active = False
    db.commit()
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    # Restore for other tests sharing the fixture DB.
    row.is_active = True
    db.commit()


def test_change_password_requires_current(client, auth_matrix):
    u = auth_matrix["branch_admin"]
    login = client.post(
        "/api/auth/login",
        json={"username": u["username"], "password": "Module2Pass!"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    bad = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "WrongPass!", "new_password": "Module2New!"},
    )
    assert bad.status_code == 400
    ok = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"current_password": "Module2Pass!", "new_password": "Module2New!"},
    )
    assert ok.status_code == 200
    # Old password fails; new works.
    assert (
        client.post(
            "/api/auth/login",
            json={"username": u["username"], "password": "Module2Pass!"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": u["username"], "password": "Module2New!"},
        ).status_code
        == 200
    )
    # Restore password for fixture reuse.
    restore = client.post(
        "/api/auth/login",
        json={"username": u["username"], "password": "Module2New!"},
    )
    client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {restore.json()['access_token']}"},
        json={"current_password": "Module2New!", "new_password": "Module2Pass!"},
    )
