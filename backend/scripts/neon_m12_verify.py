"""Controlled Neon verification for Module 12 — Speaking (mocked Whisper + AI)."""
from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.auth.models import Branch, User
from app.auth.security import hash_password
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.speaking.models import Answer, Evaluation, TestSession
from app.speaking.services.audio_store import resolve_recording, speaking_audio_root


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_SAMPLE = {
    "id": "m12-neon",
    "title": "M12 Neon Speaking",
    "part1": ["Do you work or study?"],
    "part2": {"topic": "Describe a place", "bullets": ["where", "what", "why"]},
    "part3": ["Why do people travel?"],
}


def main() -> int:
    url = (settings.DATABASE_URL or "").lower()
    if "neon" not in url and "postgresql" not in url:
        print("FAIL: DATABASE_URL is not Neon/PostgreSQL")
        return 1

    settings.AUTH_LEGACY_HEADERS = False
    tag = uuid.uuid4().hex[:8]
    branch_a = f"m12-neon-a-{tag}"
    branch_b = f"m12-neon-b-{tag}"
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
        print("=== Neon speaking integrity snapshot ===")
        sessions = db.query(TestSession).count()
        orphans = (
            db.query(TestSession)
            .filter(TestSession.student_id.isnot(None))
            .filter(~TestSession.student_id.in_(db.query(User.id)))
            .count()
            if sessions
            else 0
        )
        answers = db.query(Answer).count()
        missing_files = 0
        for a in db.query(Answer).filter(Answer.audio_path.isnot(None)).limit(200).all():
            if not resolve_recording(a.audio_path):
                missing_files += 1
        print(f"  sessions={sessions} orphan_student_refs={orphans} answers={answers} missing_audio_files={missing_files}")

        for bid, name in ((branch_a, "A"), (branch_b, "B")):
            db.add(Branch(id=bid, name=f"M12 {name} {tag}", city="M12", is_active=True))
        for role, bid, suffix in (
            ("student", branch_a, "a"),
            ("student", branch_b, "b"),
            ("teacher", branch_a, "ta"),
            ("teacher", branch_b, "tb"),
        ):
            u = User(
                id=str(uuid.uuid4()),
                role=role,
                email=f"m12_{suffix}_{tag}@m12.neon",
                username=f"m12_{suffix}_{tag}",
                password_hash=hash_password("Passw0rd!"),
                full_name=f"M12 {suffix}",
                branch_id=bid,
                is_active=True,
            )
            db.add(u)
            ids.append(u.id)
        db.commit()
        stu_a, stu_b, tea_a, tea_b = ids
    finally:
        db.close()

    tok_a = client.post(
        "/api/auth/login",
        json={"username": f"m12_a_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_b = client.post(
        "/api/auth/login",
        json={"username": f"m12_b_{tag}", "password": "Passw0rd!", "role": "student"},
    ).json()["access_token"]
    tok_ta = client.post(
        "/api/auth/login",
        json={"username": f"m12_ta_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]
    tok_tb = client.post(
        "/api/auth/login",
        json={"username": f"m12_tb_{tag}", "password": "Passw0rd!", "role": "teacher"},
    ).json()["access_token"]

    print("=== Start / upload / IDOR ===")
    with patch("app.speaking.services.question_bank.get_random_test", return_value=_SAMPLE):
        start = client.post(
            "/api/tests/start",
            headers={**_hdr(tok_a), "X-Student-Id": stu_b},
            json={"mode": "stored", "practice_part": 1},
        )
    check(start.status_code == 200, f"start ({start.status_code})")
    body = start.json() if start.status_code == 200 else {}
    sid = body.get("session_id") or ""
    qid = (body.get("first_question") or {}).get("id") or ""
    if sid:
        attempt_ids.append(sid)

    db = SessionLocal()
    try:
        row = db.query(TestSession).filter_by(id=sid).first()
        check(row is not None and row.student_id == stu_a, "DB session owned by Student A")
    finally:
        db.close()

    fake_tx = {
        "transcript": "I study computer science at university every day.",
        "duration": 5.0,
        "word_count": 8,
        "rms": 0.1,
        "no_speech_prob": 0.1,
    }
    with patch("app.speaking.controllers.answers.transcribe_audio", new=AsyncMock(return_value=fake_tx)):
        with patch(
            "app.speaking.controllers.answers.speech_status",
            return_value={"insufficient": False, "reason": None},
        ):
            with patch(
                "app.speaking.controllers.answers.score_relevance",
                return_value={"label": "relevant", "score": 1.0},
            ):
                up = client.post(
                    "/api/answers/submit",
                    headers=_hdr(tok_a),
                    data={"session_id": sid, "question_id": qid},
                    files={"audio": ("ans.webm", io.BytesIO(b"WEBM" + b"\x00" * 80), "audio/webm")},
                )
    check(up.status_code == 200, f"upload ({up.status_code})")
    answer_id = (up.json() or {}).get("answer_id") if up.status_code == 200 else None

    db = SessionLocal()
    try:
        ans = db.query(Answer).filter_by(id=answer_id).first() if answer_id else None
        check(ans is not None and bool(ans.audio_path), "answer has audio_path")
        check(bool(ans and resolve_recording(ans.audio_path)), "media file on disk")
    finally:
        db.close()

    check(client.get(f"/api/answers/{answer_id}/audio", headers=_hdr(tok_a)).status_code == 200, "owner audio")
    check(client.get(f"/api/answers/{answer_id}/audio", headers=_hdr(tok_b)).status_code == 403, "B denied audio")
    check(
        client.post(
            "/api/answers/submit",
            headers=_hdr(tok_b),
            data={"session_id": sid, "question_id": qid},
            files={"audio": ("x.webm", io.BytesIO(b"x" * 40), "audio/webm")},
        ).status_code
        == 403,
        "B cannot upload to A session",
    )

    print("=== Finalize / teacher review ===")
    with patch(
        "app.speaking.controllers.answers.evaluate_session",
        new=AsyncMock(
            return_value={
                "fluency_coherence": 6.0,
                "lexical_resource": 6.0,
                "grammar": 6.0,
                "estimated_band": 6.0,
                "strengths": [],
                "weaknesses": [],
                "corrections": [],
                "pronunciation": "Not assessed in this version",
                "ai_status": "ai",
            }
        ),
    ):
        fin = client.post(f"/api/answers/finalize/{sid}", headers=_hdr(tok_a))
    check(fin.status_code == 200 and fin.json().get("estimated_band") == 6.0, "finalize AI band")

    review = client.post(
        f"/api/speaking/admin/sessions/{sid}/review",
        headers=_hdr(tok_ta),
        json={"teacher_band": 6.5, "teacher_comments": "Good fluency."},
    )
    check(review.status_code == 200 and review.json().get("final_band") == 6.5, "teacher A review")
    check(
        client.post(
            f"/api/speaking/admin/sessions/{sid}/review",
            headers=_hdr(tok_tb),
            json={"teacher_band": 9.0},
        ).status_code
        == 403,
        "teacher B cross-branch denied",
    )
    check(
        client.get(f"/api/speaking/admin/answers/{answer_id}/audio", headers=_hdr(tok_ta)).status_code == 200,
        "teacher A can play audio",
    )
    check(
        client.get(f"/api/speaking/admin/answers/{answer_id}/audio", headers=_hdr(tok_tb)).status_code == 403,
        "teacher B denied audio",
    )

    db = SessionLocal()
    try:
        row = db.query(TestSession).filter_by(id=sid).first()
        ev = db.query(Evaluation).filter_by(session_id=sid).first()
        check(
            row is not None
            and row.status == "completed"
            and ev is not None
            and ev.estimated_band == 6.0
            and ev.teacher_band == 6.5
            and ev.final_band == 6.5,
            "Neon persistence",
        )
    finally:
        db.close()

    print(f"=== Storage root: {speaking_audio_root()} ===")

    print("=== Cleanup ===")
    db = SessionLocal()
    try:
        for sid_del in attempt_ids:
            for a in db.query(Answer).filter_by(session_id=sid_del).all():
                path = resolve_recording(a.audio_path)
                if path and path.is_file():
                    path.unlink(missing_ok=True)
                db.delete(a)
            ev = db.query(Evaluation).filter_by(session_id=sid_del).first()
            if ev:
                db.delete(ev)
            # questions cascade via session delete if configured; delete session
            sess = db.query(TestSession).filter_by(id=sid_del).first()
            if sess:
                db.delete(sess)
        for uid in ids:
            u = db.query(User).filter_by(id=uid).first()
            if u:
                db.delete(u)
        for bid in (branch_a, branch_b):
            br = db.query(Branch).filter_by(id=bid).first()
            if br:
                db.delete(br)
        db.commit()
        print("  cleaned M12 Neon fixtures")
    finally:
        db.close()

    if failures:
        print(f"\nM12_NEON_SPEAKING_FAIL ({len(failures)})")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nM12_NEON_SPEAKING_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
