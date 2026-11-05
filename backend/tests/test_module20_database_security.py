"""Module 20 — Database / Neon security: secrets, isolation, injection, sessions."""
from __future__ import annotations

import uuid
from pathlib import Path
import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.models import Batch, Branch, User
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.db_security import (
    assert_no_auto_create_in_managed,
    assert_postgres_tls_policy,
    assert_safe_database_target,
    looks_like_managed_cloud_db,
    public_exception_detail,
    redact_database_url,
    redact_secrets,
)
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from tests.conftest import _TestSessionFactory


BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk(role: str, branch_id: str, *, batch: str | None = None) -> dict:
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch_id).first():
            db.add(Branch(id=branch_id, name=f"M20 {branch_id}", city="M20", is_active=True))
        tag = uuid.uuid4().hex[:8]
        uid = f"{role[:3]}-{tag}"
        uname = f"m20_{role[0]}_{tag}"
        row = User(
            id=uid,
            role=role,
            email=f"{uname}@m20.test",
            username=uname,
            password_hash=hash_password("Passw0rd!"),
            full_name=uname,
            branch_id=branch_id,
            batch=batch or "",
            is_active=True,
        )
        db.add(row)
        if batch and not db.query(Batch).filter_by(branch_id=branch_id, name=batch).first():
            db.add(Batch(id=f"bat-{tag}", branch_id=branch_id, name=batch))
        db.commit()
        token = create_access_token(sub=row.id, role=role, extra={"branch_id": branch_id})
        return {"id": row.id, "token": token, "branch_id": branch_id}
    finally:
        db.close()


# ── Secrets / disclosure ─────────────────────────────────────────────────────


def test_redact_database_url_hides_password():
    url = "postgresql+psycopg2://myuser:SuperSecret99@ep-x.neon.tech/neondb?sslmode=require"
    out = redact_database_url(url)
    assert "SuperSecret99" not in out
    assert "***" in out
    assert "sslmode=require" in out
    assert "myuser" in out


def test_redact_secrets_masks_embedded_url_and_bearer():
    raw = (
        "failed: postgresql://u:hunter2@db.example/app "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"
    )
    out = redact_secrets(raw)
    assert "hunter2" not in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "***" in out


def test_public_exception_detail_production_hides_exc():
    detail = public_exception_detail(
        Exception("postgresql://u:pw@host/db boom"),
        is_production=True,
    )
    assert detail == "An internal error occurred."
    assert "pw" not in detail


def test_public_exception_detail_dev_redacts():
    detail = public_exception_detail(
        Exception("could not connect postgresql://u:pw@host/db"),
        is_production=False,
    )
    assert "pw" not in detail
    assert "***" in detail


def test_health_does_not_expose_database_url(client):
    res = client.get("/api/health")
    assert res.status_code in (200, 503)
    body = res.text.lower()
    assert "password" not in body
    assert "postgresql://" not in body
    assert "neon.tech" not in body
    data = res.json()
    assert "database" in data
    assert isinstance(data["database"], bool)


def test_generic_exception_handler_redacts_db_url(monkeypatch):
    import asyncio

    from starlette.requests import Request

    from app.main import generic_exception_handler

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    scope = {"type": "http", "method": "GET", "path": "/api/x", "headers": []}
    request = Request(scope)

    async def _run():
        return await generic_exception_handler(
            request,
            RuntimeError("postgresql://leakuser:LeakPass99@secret-host/db failed"),
        )

    resp = asyncio.run(_run())
    assert resp.status_code == 500
    body = resp.body.decode()
    assert "LeakPass99" not in body
    assert "***" in body or "internal error" in body.lower()


def test_frontend_sources_have_no_database_credentials():
    if not FRONTEND.is_dir():
        pytest.skip("frontend not present")
    patterns = ("DATABASE_URL", "postgresql://", "PGPASSWORD", "neon.tech")
    hits = []
    for path in FRONTEND.joinpath("src").rglob("*"):
        if path.suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".env"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in patterns:
            if pat.lower() in text.lower():
                hits.append(f"{path.relative_to(FRONTEND)}:{pat}")
    assert not hits, f"Frontend must not contain DB credentials: {hits}"


# ── Environment / schema safety ──────────────────────────────────────────────


def test_managed_env_rejects_auto_create_schema():
    with pytest.raises(RuntimeError, match="AUTO_CREATE_SCHEMA"):
        assert_no_auto_create_in_managed("production", True)
    with pytest.raises(RuntimeError, match="AUTO_CREATE_SCHEMA"):
        assert_no_auto_create_in_managed("staging", True)
    assert_no_auto_create_in_managed("development", True)  # allowed


def test_managed_env_requires_postgres_sslmode():
    bare = "postgresql+psycopg2://u:p@ep-x.neon.tech/neondb"
    with pytest.raises(RuntimeError, match="sslmode"):
        assert_postgres_tls_policy(bare, environment="production")
    ok = bare + "?sslmode=require"
    assert_postgres_tls_policy(ok, environment="production")
    # Dev may omit sslmode (not recommended, but not blocked here).
    assert_postgres_tls_policy(bare, environment="development")


def test_pytest_blocks_managed_cloud_without_allow(monkeypatch):
    monkeypatch.delenv("ALLOW_NEON_TESTS", raising=False)
    neon = "postgresql://u:p@ep-demo.neon.tech/neondb?sslmode=require"
    assert looks_like_managed_cloud_db(neon)
    with pytest.raises(RuntimeError, match="managed cloud"):
        assert_safe_database_target(neon, purpose="unit-test truncate", environment="test")


def test_allow_neon_tests_bypass(monkeypatch):
    monkeypatch.setenv("ALLOW_NEON_TESTS", "1")
    neon = "postgresql://u:p@ep-demo.neon.tech/neondb?sslmode=require"
    assert_safe_database_target(neon, purpose="intentional neon verify", environment="test")


# ── SQL injection / search ───────────────────────────────────────────────────


def test_writing_search_injection_payload_safe(client, monkeypatch):
    """ILIKE uses bound parameters — injection string must not 500 or dump schema."""
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m20-inj-{uuid.uuid4().hex[:6]}"
    stu = _mk("student", branch, batch="Morning")
    payload = "'; DROP TABLE auth_users;--"
    res = client.get(
        "/api/writing/questions",
        headers=_hdr(stu["token"]),
        params={"search": payload, "topic": payload},
    )
    assert res.status_code in (200, 401, 403, 404, 422)
    assert "syntax error" not in res.text.lower()
    assert "postgresql://" not in res.text.lower()


def test_writing_admin_search_injection_safe(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m20-adm-{uuid.uuid4().hex[:6]}"
    db = _TestSessionFactory()
    try:
        if not db.query(Branch).filter_by(id=branch).first():
            db.add(Branch(id=branch, name="M20 Adm", is_active=True))
        tag = uuid.uuid4().hex[:8]
        sa = User(
            id=f"sa-{tag}",
            role="super_admin",
            email=f"sa_{tag}@m20.test",
            username=f"sa_{tag}",
            password_hash=hash_password("Passw0rd!"),
            full_name="SA",
            branch_id="",
            is_active=True,
        )
        db.add(sa)
        db.commit()
        token = create_access_token(sub=sa.id, role="super_admin", extra={"branch_id": ""})
    finally:
        db.close()
    evil = "1' OR '1'='1"
    res = client.get(
        "/api/writing/admin/questions",
        headers=_hdr(token),
        params={"q": evil, "topic": evil},
    )
    assert res.status_code in (200, 403, 404)
    assert "password" not in res.text.lower()


# ── Sessions / conflicts / ownership ─────────────────────────────────────────


def test_duplicate_homework_returns_integrity_not_leak(db):
    aid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    db.add(HomeworkAssignment(id=aid, title="M20", module="writing", created_by="t"))
    db.add(HomeworkSubmission(assignment_id=aid, student_id=sid, status="pending"))
    db.commit()
    db.add(HomeworkSubmission(assignment_id=aid, student_id=sid, status="pending"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_foreign_reading_attempt_denied(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    a = f"m20-a-{uuid.uuid4().hex[:6]}"
    b = f"m20-b-{uuid.uuid4().hex[:6]}"
    owner = _mk("student", a, batch="Morning")
    other = _mk("student", b, batch="Morning")
    from app.reading.models import ReadingAttempt

    db = _TestSessionFactory()
    try:
        row = ReadingAttempt(
            student_id=owner["id"],
            test_id="test-001",
            test_type="academic",
            status="in_progress",
        )
        db.add(row)
        db.commit()
        aid = row.id
    finally:
        db.close()
    res = client.get(f"/api/reading/attempts/{aid}", headers=_hdr(other["token"]))
    assert res.status_code in (403, 404)
    if res.status_code == 404:
        assert "password" not in res.text.lower()


def test_get_db_rolls_back_on_exception(client, monkeypatch):
    from app import homework as hw_pkg

    monkeypatch.setattr(settings, "AUTH_LEGACY_HEADERS", False)
    branch = f"m20-rb-{uuid.uuid4().hex[:6]}"
    teacher = _mk("teacher", branch, batch="Morning")
    stu = _mk("student", branch, batch="Morning")
    create = client.post(
        "/api/homework/teacher/assignments",
        headers=_hdr(teacher["token"]),
        json={
            "title": "M20 RB",
            "module": "writing",
            "source": "manual",
            "payload": {"prompt": "x"},
            "student_ids": [stu["id"]],
            "assignment_target": "specific",
        },
    )
    assert create.status_code == 200, create.text
    aid = create.json()["id"]

    async def boom(*_a, **_k):
        raise RuntimeError("m20 simulated failure")

    monkeypatch.setattr(hw_pkg.controllers, "_open_skill", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/homework/student/{aid}/start", headers=_hdr(stu["token"]))
    db = _TestSessionFactory()
    try:
        n = db.query(HomeworkSubmission).filter_by(assignment_id=aid, student_id=stu["id"]).count()
        assert n == 0
    finally:
        db.close()
