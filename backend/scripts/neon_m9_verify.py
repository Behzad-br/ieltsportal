"""Controlled Neon verification for Module 9 — Reading."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.reading.models import ReadingAttempt
from app.reading.services import bank
from app.reading.services.scoring import SERVER_ONLY_FIELDS, estimated_band


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _walk_keys(obj):
    keys = set()
    if isinstance(obj, dict):
        keys.update(obj.keys())
        for v in obj.values():
            keys.update(_walk_keys(v))
    elif isinstance(obj, list):
        for item in obj:
            keys.update(_walk_keys(item))
    return keys


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_id = f"m9-neon-{tag}"
    ids: list[str] = []
    attempt_ids: list[str] = []
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
        print("=== Neon reading integrity snapshot ===")
        attempts = db.query(ReadingAttempt).count()
        orphans = db.query(ReadingAttempt).filter(
            ~ReadingAttempt.student_id.in_(db.query(User.id))
        ).count() if attempts else 0
        print(f"  attempts={attempts} orphan_student_refs={orphans}")

        db.add(Branch(id=branch_id, name=f"M9 {tag}", city="M9", is_active=True))
        for role_suffix, role in (("a", "student"), ("b", "student")):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m9_{role_suffix}_{tag}@m9.neon",
                username=f"m9_{role_suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M9 {role_suffix}",
                branch_id=branch_id,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        db.commit()
        stu_a, stu_b = ids[0], ids[1]
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m9_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m9_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]

    print("=== Catalog / leakage ===")
    cat = client.get("/api/reading/catalog")
    check(cat.status_code == 200 and len(cat.json().get("academic") or []) >= 1, "catalog loads")
    preview = client.get("/api/reading/tests/academic-reading-01")
    check(preview.status_code == 200, "test preview")
    check(not (_walk_keys(preview.json()) & SERVER_ONLY_FIELDS), "no answer keys in preview")

    print("=== Start / save / resume ===")
    start = client.post(
        "/api/reading/attempts",
        headers={**_hdr(tok_a), "X-Student-Id": stu_b},
        json={"test_id": "academic-reading-01", "mode": "full_mock", "timed": False},
    )
    check(start.status_code == 200, f"start attempt ({start.status_code})")
    body = start.json() if start.status_code == 200 else {}
    attempt_id = body.get("attempt_id") or ""
    if attempt_id:
        attempt_ids.append(attempt_id)
    check(not (_walk_keys(body.get("test") or {}) & SERVER_ONLY_FIELDS), "no keys on start payload")

    db = SessionLocal()
    try:
        row = db.query(ReadingAttempt).filter_by(id=attempt_id).first()
        check(row is not None and row.student_id == stu_a, "DB attempt owned by Student A")
    finally:
        db.close()

    test = bank.get_test("academic-reading-01")
    q0 = str(test["questions"][0]["number"])
    save = client.patch(
        f"/api/reading/attempts/{attempt_id}/responses",
        headers=_hdr(tok_a),
        json={"responses": {q0: test["questions"][0]["accepted_answers"][0], "x-foreign": "nope"}},
    )
    check(save.status_code == 200, "save responses")
    resumed = client.get(f"/api/reading/attempts/{attempt_id}", headers=_hdr(tok_a))
    check(resumed.status_code == 200 and resumed.json()["responses"].get(q0), "resume restores answer")
    check("x-foreign" not in (resumed.json().get("responses") or {}), "foreign qid ignored")

    print("=== IDOR ===")
    check(client.get(f"/api/reading/attempts/{attempt_id}", headers=_hdr(tok_b)).status_code == 403, "B cannot get A attempt")
    check(
        client.post(
            f"/api/reading/attempts/{attempt_id}/submit",
            headers=_hdr(tok_b),
            json={"responses": {}},
        ).status_code
        == 403,
        "B cannot submit A attempt",
    )

    print("=== Submit / score / persist ===")
    responses = {}
    for i, q in enumerate(test["questions"]):
        if i < 32:
            responses[str(q["number"])] = q["accepted_answers"][0]
    submit = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(tok_a),
        json={"responses": responses},
    )
    check(submit.status_code == 200, f"submit ({submit.status_code})")
    result = submit.json() if submit.status_code == 200 else {}
    expected_band = estimated_band(32, bank.thresholds_for("academic"))
    check(result.get("raw_score") == 32, f"raw_score=32 got {result.get('raw_score')}")
    check(result.get("estimated_band") == expected_band, f"band={expected_band} got {result.get('estimated_band')}")

    dup = client.post(
        f"/api/reading/attempts/{attempt_id}/submit",
        headers=_hdr(tok_a),
        json={"responses": {q0: "WRONG"}},
    )
    check(dup.status_code == 200 and dup.json().get("raw_score") == 32, "duplicate submit safe")

    db = SessionLocal()
    try:
        row = db.query(ReadingAttempt).filter_by(id=attempt_id).first()
        check(
            row is not None
            and row.status == "submitted"
            and row.raw_score == 32
            and row.estimated_band == expected_band
            and row.result is not None,
            "Neon persistence",
        )
    finally:
        db.close()

    check(
        client.get(f"/api/reading/attempts/{attempt_id}/result", headers=_hdr(tok_b)).status_code == 403,
        "B cannot read A result",
    )

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for aid in attempt_ids:
            row = db.query(ReadingAttempt).filter_by(id=aid).first()
            if row:
                db.delete(row)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        br = db.query(Branch).filter_by(id=branch_id).first()
        if br:
            db.delete(br)
        db.commit()
        print("  cleaned M9 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM9_NEON_READING_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM9_NEON_READING_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
