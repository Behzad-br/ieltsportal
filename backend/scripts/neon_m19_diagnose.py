"""Read-only Neon / PostgreSQL integrity snapshot for Module 19.

No mutations. Safe to run against staging/production Neon.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal
from app.db_integrity import MIGRATION_HEAD, resolve_skill_owner, skill_ref_cross_user
from app.ielts_bands import is_valid_ielts_band

# Ensure all models register on Base.metadata
import app.auth.models  # noqa: F401
import app.homework.models  # noqa: F401
import app.listening.models  # noqa: F401
import app.mocks.models  # noqa: F401
import app.notes.models  # noqa: F401
import app.reading.models  # noqa: F401
import app.speaking.models  # noqa: F401
import app.writing.models  # noqa: F401

from app.auth.models import Batch, Branch, User
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.listening.models import ListeningAttempt
from app.mocks.models import (
    LmsNotification,
    MockAnswer,
    MockAssignment,
    MockAssignmentStudent,
    MockAttempt,
    MockIntegrityEvent,
    MockLibraryItem,
    TeacherMockAction,
)
from app.notes.models import ClassNote
from app.reading.models import ReadingAttempt
from app.speaking.models import Answer, Evaluation, Question, TestSession
from app.writing.models import (
    WritingAiUsage,
    WritingAttempt,
    WritingAttemptRevision,
    WritingAssignment,
    WritingBookmark,
    WritingGeneration,
    WritingMockSession,
    WritingModelAnswer,
    WritingQuestion,
)


EXPECTED_HEAD = MIGRATION_HEAD

READING_STATUSES = {"in_progress", "submitted"}
LISTENING_STATUSES = {"in_progress", "submitted"}
WRITING_ATTEMPT_STATUSES = {
    "in_progress",
    "submitted",
    "grading",
    "graded",
    "reviewed",
    "published",
    "error",
}
SPEAKING_STATUSES = {"in_progress", "completed", "error"}
HW_STATUSES = {"pending", "in_progress", "submitted", "reviewed", "published"}
MOCK_SLOT_STATUSES = {"assigned", "in_progress", "submitted", "reviewed", "published", "cancelled"}
MOCK_ATTEMPT_STATUSES = {"in_progress", "submitted", "reviewed", "published", "error"}


def _safe_json(raw: str | None):
    if raw is None or raw == "":
        return None, False
    try:
        return json.loads(raw), False
    except (TypeError, json.JSONDecodeError):
        return None, True


def _band_bad(val) -> bool:
    return val is not None and not is_valid_ielts_band(val)


def schema_drift(engine) -> list[str]:
    insp = inspect(engine)
    live_tables = set(insp.get_table_names())
    orm_tables = set(Base.metadata.tables.keys())
    findings: list[str] = []
    for t in sorted(orm_tables - live_tables):
        findings.append(f"ORM_ONLY_TABLE {t}")
    for t in sorted(live_tables - orm_tables - {"alembic_version", "migration_metadata"}):
        findings.append(f"LIVE_ONLY_TABLE {t}")

    for tname, table in sorted(Base.metadata.tables.items()):
        if tname not in live_tables:
            continue
        live_cols = {c["name"]: c for c in insp.get_columns(tname)}
        for col in table.columns:
            if col.name not in live_cols:
                findings.append(f"MISSING_COL {tname}.{col.name}")
                continue
            lc = live_cols[col.name]
            orm_null = bool(col.nullable)
            live_null = bool(lc["nullable"])
            if orm_null != live_null:
                findings.append(
                    f"NULLABLE_DRIFT {tname}.{col.name} orm={orm_null} live={live_null}"
                )
        for name in live_cols:
            if name not in table.c:
                findings.append(f"EXTRA_COL {tname}.{name}")

        orm_fks = {
            (fk.parent.name, fk.column.table.name, fk.column.name)
            for fk in table.foreign_keys
        }
        live_fks = set()
        for fk in insp.get_foreign_keys(tname):
            for a, b in zip(fk["constrained_columns"], fk["referred_columns"]):
                live_fks.add((a, fk["referred_table"], b))
        if orm_fks != live_fks:
            findings.append(f"FK_DRIFT {tname} orm={sorted(orm_fks)} live={sorted(live_fks)}")
    return findings


def main() -> int:
    engine = create_engine(settings.DATABASE_URL)
    db: Session = SessionLocal()
    report: dict = {"head": None, "counts": {}, "findings": []}

    try:
        with engine.connect() as conn:
            head = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            report["head"] = head
            print(f"=== M19 Database diagnostics (read-only) ===")
            print(f"alembic_head={head} expected={EXPECTED_HEAD} match={head == EXPECTED_HEAD}")

        drift = schema_drift(engine)
        print(f"schema_drift_items={len(drift)}")
        for d in drift[:40]:
            print(f"  DRIFT {d}")
        report["findings"].extend(("DRIFT", d) for d in drift)

        # Row counts
        tables = sorted(Base.metadata.tables.keys())
        insp = inspect(engine)
        print("--- row counts ---")
        with engine.connect() as conn:
            for t in tables:
                if t not in insp.get_table_names():
                    continue
                n = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                report["counts"][t] = n
                print(f"  {t}={n}")

        user_ids = {u.id for u in db.query(User.id).all()}
        branch_ids = {b.id for b in db.query(Branch.id).all()}
        branch_by_id = {b.id: b for b in db.query(Branch).all()}
        batches = db.query(Batch).all()
        batch_keys = {(b.branch_id or "", b.name) for b in batches}
        batch_names_by_branch: dict[str, set[str]] = defaultdict(set)
        for b in batches:
            batch_names_by_branch[b.branch_id or ""].add(b.name)

        # --- User identity orphans ---
        def orphan_count(model, col):
            rows = db.query(model).all()
            return sum(1 for r in rows if getattr(r, col) and getattr(r, col) not in user_ids)

        orphan_specs = [
            ("reading_attempts", ReadingAttempt, "student_id"),
            ("listening_attempts", ListeningAttempt, "student_id"),
            ("writing_attempts", WritingAttempt, "student_id"),
            ("writing_mock_sessions", WritingMockSession, "student_id"),
            ("writing_bookmarks", WritingBookmark, "student_id"),
            ("writing_assignments", WritingAssignment, "student_id"),
            ("writing_generations", WritingGeneration, "student_id"),
            ("writing_ai_usage", WritingAiUsage, "student_id"),
            ("test_sessions", TestSession, "student_id"),
            ("homework_assignments.created_by", HomeworkAssignment, "created_by"),
            ("homework_submissions", HomeworkSubmission, "student_id"),
            ("class_notes.created_by", ClassNote, "created_by"),
            ("mock_library.created_by", MockLibraryItem, "created_by"),
            ("mock_assignments.created_by", MockAssignment, "created_by"),
            ("mock_assignment_students", MockAssignmentStudent, "student_id"),
            ("mock_attempts", MockAttempt, "student_id"),
            ("mock_integrity_events", MockIntegrityEvent, "student_id"),
            ("teacher_mock_actions", TeacherMockAction, "teacher_id"),
            ("lms_notifications", LmsNotification, "user_id"),
        ]
        print("--- orphan user refs ---")
        for label, model, col in orphan_specs:
            n = orphan_count(model, col)
            # also null student on speaking
            null_n = 0
            if col == "student_id" and model is TestSession:
                null_n = db.query(TestSession).filter(
                    (TestSession.student_id == None) | (TestSession.student_id == "")  # noqa: E711
                ).count()
            print(f"  {label} orphans={n}" + (f" null/empty={null_n}" if null_n else ""))
            if n:
                report["findings"].append(("ORPHAN_USER", f"{label}={n}"))
            if null_n:
                report["findings"].append(("NULL_OWNER", f"test_sessions={null_n}"))

        # Reading/listening default 'local'
        for label, model in (("reading", ReadingAttempt), ("listening", ListeningAttempt)):
            local_n = db.query(model).filter(model.student_id == "local").count()
            if local_n:
                print(f"  {label}_student_id=local count={local_n}")
                report["findings"].append(("LEGACY_LOCAL", f"{label}={local_n}"))

        # --- Branch integrity ---
        print("--- branch integrity ---")
        users = db.query(User).all()
        bad_branch_users = [
            u for u in users
            if (u.branch_id or "") not in ("",) and u.branch_id not in branch_ids
        ]
        print(f"  users_invalid_branch={len(bad_branch_users)}")
        empty_branch_non_sa = [
            u for u in users
            if (u.branch_id or "") == "" and u.role != "super_admin"
        ]
        print(f"  non_sa_empty_branch={len(empty_branch_non_sa)}")
        bad_batch_branch = [b for b in batches if (b.branch_id or "") and b.branch_id not in branch_ids]
        print(f"  batches_invalid_branch={len(bad_batch_branch)}")
        if bad_branch_users:
            report["findings"].append(("INVALID_BRANCH", f"users={len(bad_branch_users)}"))
        if bad_batch_branch:
            report["findings"].append(("INVALID_BRANCH", f"batches={len(bad_batch_branch)}"))

        # Branch name duplicates
        name_norm = defaultdict(list)
        for b in branch_by_id.values():
            key = (b.name or "").strip().lower()
            name_norm[key].append(b.id)
        case_dups = {k: v for k, v in name_norm.items() if k and len(v) > 1}
        print(f"  branch_name_case_dups={len(case_dups)}")
        if case_dups:
            report["findings"].append(("BRANCH_NAME_DUP", str(case_dups)))

        # --- Batch integrity ---
        print("--- batch integrity ---")
        exact_dups = Counter((b.branch_id or "", b.name) for b in batches)
        exact_dup_n = sum(1 for k, c in exact_dups.items() if c > 1)
        case_keys = Counter((b.branch_id or "", (b.name or "").strip().lower()) for b in batches)
        case_dup_n = sum(1 for k, c in case_keys.items() if k[1] and c > 1)
        ws_keys = Counter((b.branch_id or "", (b.name or "").strip()) for b in batches)
        # whitespace variants: same strip key, different raw names
        by_strip = defaultdict(set)
        for b in batches:
            by_strip[(b.branch_id or "", (b.name or "").strip())].add(b.name)
        ws_dup_n = sum(1 for names in by_strip.values() if len(names) > 1)
        print(f"  exact_branch_name_dups={exact_dup_n} case_dups={case_dup_n} whitespace_variants={ws_dup_n}")
        bad_teacher = [
            b for b in batches
            if (b.teacher_id or "") and b.teacher_id not in user_ids
        ]
        print(f"  batches_invalid_teacher={len(bad_teacher)}")
        if exact_dup_n:
            report["findings"].append(("BATCH_DUP", f"exact={exact_dup_n}"))
        if bad_teacher:
            report["findings"].append(("BATCH_TEACHER", f"invalid={len(bad_teacher)}"))

        # Teacher CSV vs Batch.teacher_id
        teachers = [u for u in users if u.role == "teacher"]
        drift_csv = 0
        for t in teachers:
            csv_labels = {x.strip() for x in (t.batches or "").split(",") if x.strip()}
            owned = {b.name for b in batches if b.teacher_id == t.id}
            if csv_labels != owned:
                # allow subset if batch names from other branches? check branch-scoped
                branch_owned = {
                    b.name for b in batches
                    if b.teacher_id == t.id and (not t.branch_id or b.branch_id == t.branch_id)
                }
                if csv_labels != branch_owned and csv_labels != owned:
                    drift_csv += 1
        print(f"  teacher_csv_vs_batch_teacher_id_drift={drift_csv}")
        if drift_csv:
            report["findings"].append(("TEACHER_CSV_DRIFT", f"n={drift_csv}"))

        # Student batch labels
        students = [u for u in users if u.role == "student"]
        label_stats = Counter()
        for s in students:
            label = (s.batch or "").strip()
            if not label:
                label_stats["empty"] += 1
                continue
            bid = s.branch_id or ""
            names = batch_names_by_branch.get(bid, set())
            if label in names:
                label_stats["exact_valid"] += 1
            elif any(label.lower() == n.lower() for n in names):
                label_stats["case_mismatch"] += 1
            elif any(label == n.strip() or label.strip() == n for n in names):
                label_stats["whitespace_mismatch"] += 1
            elif any(label == n for nameset in batch_names_by_branch.values() for n in nameset):
                label_stats["wrong_branch"] += 1
            else:
                label_stats["nonexistent"] += 1
        print(f"  student_batch_labels={dict(label_stats)}")
        for k, v in label_stats.items():
            if k != "exact_valid" and k != "empty" and v:
                report["findings"].append(("STUDENT_BATCH", f"{k}={v}"))

        # Username / email normalized dups
        print("--- identity uniqueness ---")
        emails_norm = Counter((u.email or "").strip().lower() for u in users)
        users_norm = Counter((u.username or "").strip().lower() for u in users)
        email_dup = sum(1 for k, c in emails_norm.items() if k and c > 1)
        user_dup = sum(1 for k, c in users_norm.items() if k and c > 1)
        print(f"  email_norm_dups={email_dup} username_norm_dups={user_dup}")
        if email_dup or user_dup:
            report["findings"].append(("IDENTITY_NORM_DUP", f"email={email_dup} user={user_dup}"))

        # --- Homework ---
        print("--- homework ---")
        hw_subs = db.query(HomeworkSubmission).all()
        pair = Counter((s.assignment_id, s.student_id) for s in hw_subs)
        hw_dup = sum(1 for c in pair.values() if c > 1)
        hw_orphan_asg = sum(
            1 for s in hw_subs
            if not db.get(HomeworkAssignment, s.assignment_id)
        )
        # faster: load assignment ids
        asg_ids = {a.id for a in db.query(HomeworkAssignment.id).all()}
        hw_orphan_asg = sum(1 for s in hw_subs if s.assignment_id not in asg_ids)
        hw_orphan_stu = sum(1 for s in hw_subs if s.student_id not in user_ids)
        hw_bad_status = sum(1 for s in hw_subs if s.status not in HW_STATUSES)
        print(f"  sub_dups={hw_dup} orphan_assignment={hw_orphan_asg} orphan_student={hw_orphan_stu} bad_status={hw_bad_status}")

        # attempt_ref integrity
        skill_tables = {
            "reading": (ReadingAttempt, {r.id: r for r in db.query(ReadingAttempt).all()}),
            "listening": (ListeningAttempt, {r.id: r for r in db.query(ListeningAttempt).all()}),
            "writing": (WritingAttempt, {r.id: r for r in db.query(WritingAttempt).all()}),
            "speaking": (TestSession, {r.id: r for r in db.query(TestSession).all()}),
        }
        hw_bad_ref = 0
        hw_cross_user = 0
        for s in hw_subs:
            if not s.attempt_ref or not s.attempt_kind:
                continue
            kind = (s.attempt_kind or "").lower()
            mapping = skill_tables.get(kind)
            if not mapping:
                hw_bad_ref += 1
                continue
            _, by_id = mapping
            row = by_id.get(s.attempt_ref)
            if not row:
                hw_bad_ref += 1
                continue
            owner = getattr(row, "student_id", None)
            if owner and owner != s.student_id:
                hw_cross_user += 1
        print(f"  attempt_ref_missing={hw_bad_ref} attempt_ref_cross_user={hw_cross_user}")
        if hw_cross_user:
            report["findings"].append(("P0_HW_CROSS_USER", f"n={hw_cross_user}"))
        if hw_dup:
            report["findings"].append(("HW_DUP_SUB", f"n={hw_dup}"))

        # --- Academic attempts ---
        print("--- academic attempts ---")
        for label, model, statuses in (
            ("reading", ReadingAttempt, READING_STATUSES),
            ("listening", ListeningAttempt, LISTENING_STATUSES),
        ):
            rows = db.query(model).all()
            bad_st = sum(1 for r in rows if r.status not in statuses)
            sub_no_at = sum(1 for r in rows if r.status == "submitted" and not r.submitted_at)
            sub_no_res = sum(1 for r in rows if r.status == "submitted" and not r.result_json)
            inprog_sub = sum(1 for r in rows if r.status == "in_progress" and r.submitted_at)
            bad_band = sum(1 for r in rows if _band_bad(r.estimated_band))
            bad_raw = sum(
                1 for r in rows
                if r.raw_score is not None and (r.raw_score < 0 or r.raw_score > 40)
            )
            ts_bad = sum(
                1 for r in rows
                if r.submitted_at and r.started_at and r.submitted_at < r.started_at
            )
            print(
                f"  {label}: bad_status={bad_st} sub_no_at={sub_no_at} sub_no_result={sub_no_res} "
                f"inprog_has_sub={inprog_sub} bad_band={bad_band} bad_raw={bad_raw} ts_order={ts_bad}"
            )
            if bad_band or bad_raw:
                report["findings"].append((f"{label.upper()}_SCORE", f"band={bad_band} raw={bad_raw}"))

        wa = db.query(WritingAttempt).all()
        w_bad_st = sum(1 for r in wa if r.status not in WRITING_ATTEMPT_STATUSES)
        w_bad_band = sum(
            1 for r in wa
            if _band_bad(r.estimated_band) or _band_bad(r.teacher_band) or _band_bad(r.final_band)
        )
        w_orphan_q = sum(
            1 for r in wa
            if r.question_id and not db.get(WritingQuestion, r.question_id)
        )
        q_ids = {q.id for q in db.query(WritingQuestion.id).all()}
        w_orphan_q = sum(1 for r in wa if r.question_id not in q_ids)
        print(f"  writing: bad_status={w_bad_st} bad_band={w_bad_band} orphan_question={w_orphan_q}")

        sessions = db.query(TestSession).all()
        s_bad = sum(1 for r in sessions if (r.status or "") not in SPEAKING_STATUSES)
        s_null = sum(1 for r in sessions if not r.student_id)
        evals = db.query(Evaluation).all()
        sess_ids = {s.id for s in sessions}
        e_orphan = sum(1 for e in evals if e.session_id not in sess_ids)
        e_band = sum(
            1 for e in evals
            if _band_bad(e.estimated_band) or _band_bad(e.teacher_band) or _band_bad(e.final_band)
        )
        answers = db.query(Answer).all()
        a_orphan = sum(1 for a in answers if a.session_id not in sess_ids)
        print(
            f"  speaking: bad_status={s_bad} null_student={s_null} "
            f"eval_orphan={e_orphan} eval_bad_band={e_band} answer_orphan_session={a_orphan}"
        )

        # --- Mocks ---
        print("--- mocks ---")
        attempts = db.query(MockAttempt).all()
        slot_ids = {s.id for s in db.query(MockAssignmentStudent.id).all()}
        asg_m_ids = {a.id for a in db.query(MockAssignment.id).all()}
        m_orphan_slot = sum(1 for a in attempts if a.assignment_student_id not in slot_ids)
        m_orphan_asg = sum(1 for a in attempts if a.assignment_id not in asg_m_ids)
        m_orphan_stu = sum(1 for a in attempts if a.student_id not in user_ids)
        m_cross = 0
        m_bad_ref = 0
        m_missing_row = 0
        for a in attempts:
            ref, malformed = _safe_json(a.skill_ref_json)
            if malformed:
                m_bad_ref += 1
                continue
            if not ref:
                continue
            if skill_ref_cross_user(db, a.student_id, ref):
                m_cross += 1
            owner, issue = resolve_skill_owner(db, ref)
            if issue == "missing_row":
                m_missing_row += 1
            elif issue and issue not in ("empty", "full_deferred"):
                m_bad_ref += 1
        print(
            f"  attempts orphan_slot={m_orphan_slot} orphan_asg={m_orphan_asg} "
            f"orphan_student={m_orphan_stu} skill_ref_bad={m_bad_ref} "
            f"skill_ref_missing_row={m_missing_row} skill_ref_cross_user={m_cross}"
        )
        if m_cross:
            report["findings"].append(("P0_MOCK_CROSS_USER", f"n={m_cross}"))
        if m_missing_row:
            report["findings"].append(("MOCK_SKILL_MISSING", f"n={m_missing_row}"))

        ans = db.query(MockAnswer).all()
        att_ids = {a.id for a in attempts}
        print(f"  mock_answers_orphan_attempt={sum(1 for x in ans if x.attempt_id not in att_ids)}")
        ev = db.query(MockIntegrityEvent).all()
        print(f"  integrity_events_orphan_attempt={sum(1 for x in ev if x.attempt_id not in att_ids)}")

        # --- Notes ---
        print("--- notes ---")
        notes = db.query(ClassNote).all()
        notes_bad_creator = sum(1 for n in notes if n.created_by and n.created_by not in user_ids)
        notes_bad_students = 0
        notes_bad_batches = 0
        for n in notes:
            for sid in n.unlocked_student_ids:
                if sid and sid not in user_ids:
                    notes_bad_students += 1
            creator = db.get(User, n.created_by) if n.created_by else None
            cbranch = (creator.branch_id or "") if creator else ""
            valid_names = batch_names_by_branch.get(cbranch, set())
            for bl in n.unlocked_batches:
                if bl and bl not in valid_names and cbranch:
                    # also allow SA global? if no branch, skip strict
                    notes_bad_batches += 1
        print(
            f"  bad_creator={notes_bad_creator} unlocked_orphan_students={notes_bad_students} "
            f"unlocked_invalid_batches={notes_bad_batches}"
        )

        # --- JSON malformations (sample key fields) ---
        print("--- json malformations ---")
        json_bad = 0
        for model, fields in (
            (ReadingAttempt, ("snapshot_json", "responses_json", "result_json", "scope_json")),
            (ListeningAttempt, ("snapshot_json", "responses_json", "result_json", "playback_json", "scope_json")),
            (WritingAttempt, ("grading_json", "teacher_feedback_json")),
            (HomeworkAssignment, ("student_ids_json", "payload_json")),
            (MockAttempt, ("skill_ref_json",)),
            (ClassNote, ("unlocked_batches_json", "unlocked_student_ids_json")),
        ):
            for row in db.query(model).all():
                for f in fields:
                    raw = getattr(row, f, None)
                    if raw in (None, ""):
                        continue
                    _, bad = _safe_json(raw)
                    if bad:
                        json_bad += 1
        print(f"  malformed_json_fields={json_bad}")
        if json_bad:
            report["findings"].append(("MALFORMED_JSON", f"n={json_bad}"))

        # --- Filesystem refs (speaking audio + notes) ---
        print("--- filesystem refs ---")
        from app.config import settings as _s

        audio_root = Path(getattr(_s, "SPEAKING_AUDIO_DIR", "") or "")
        if not audio_root.is_absolute():
            # resolve relative to data dir if needed
            data = Path(getattr(_s, "DATA_DIR", "") or Path(__file__).resolve().parents[2] / "data")
            audio_root = data / "speaking_audio" if not str(audio_root) else Path(audio_root)
        missing_audio = 0
        if audio_root.exists():
            for a in answers:
                if not a.audio_path:
                    continue
                p = Path(a.audio_path)
                if not p.is_absolute():
                    p = audio_root / p.name
                if not p.exists():
                    missing_audio += 1
        else:
            print(f"  speaking_audio_root_missing path={audio_root}")
        print(f"  answers_audio_missing_file={missing_audio}")

        notes_root = Path(getattr(_s, "NOTES_DIR", "") or "")
        data = Path(getattr(_s, "DATA_DIR", "") or Path(__file__).resolve().parents[2] / "data")
        if not notes_root or not notes_root.is_absolute():
            notes_root = data / "notes"
        missing_notes = 0
        if notes_root.exists():
            for n in notes:
                p = notes_root / n.stored_name
                if not p.exists():
                    missing_notes += 1
        else:
            print(f"  notes_root_missing path={notes_root}")
        print(f"  notes_missing_file={missing_notes}")
        if missing_audio:
            report["findings"].append(("MISSING_AUDIO", f"n={missing_audio}"))
        if missing_notes:
            report["findings"].append(("MISSING_NOTE_FILE", f"n={missing_notes}"))

        # Unique constraints present
        print("--- constraints ---")
        for t, expect in (
            ("auth_batches", "uq_auth_batches_branch_name"),
            ("homework_submissions", "uq_homework_sub_assignment_student"),
            ("mock_assignment_students", "uq_mock_assignment_students_assignment_student"),
        ):
            if t not in insp.get_table_names():
                print(f"  {t}.{expect} present=False (table missing)")
                continue
            ucs = {c["name"] for c in insp.get_unique_constraints(t)}
            idxs = {i["name"] for i in insp.get_indexes(t) if i.get("unique")}
            ok = expect in ucs or expect in idxs
            print(f"  {t}.{expect} present={ok}")

        print("--- finding summary ---")
        by_type = Counter(t for t, _ in report["findings"])
        for k, v in sorted(by_type.items()):
            print(f"  {k}: {v}")
        print(f"total_finding_records={len(report['findings'])}")
        print("=== end M19 diagnostics ===")
        print("M19_NEON_DIAGNOSE_OK")
        return 0
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
