"""DB-backed analytics aggregations — scoped by role."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.analytics.bands import average_bands, effective_result_band, sanitize_band
from app.auth.models import Batch, Branch, User
from app.homework.models import HomeworkAssignment, HomeworkSubmission
from app.listening.models import ListeningAttempt
from app.mocks.models import MockAssignment, MockAssignmentStudent, MockAttempt
from app.reading.models import ReadingAttempt
from app.speaking.models import Evaluation, TestSession
from app.writing.models import WritingAttempt


def _student_ids_in_branch(db: Session, branch_id: str, *, active_only: bool = True) -> list[str]:
    q = db.query(User.id).filter(User.role == "student", User.branch_id == branch_id)
    if active_only:
        q = q.filter(User.is_active.is_(True))
    return [r[0] for r in q.all()]


def skill_progress_for_student(db: Session, student_id: str) -> dict:
    """Per-skill averages from submitted/completed attempts only."""
    reading_rows = (
        db.query(ReadingAttempt)
        .filter_by(student_id=student_id, status="submitted")
        .order_by(ReadingAttempt.submitted_at.desc())
        .limit(50)
        .all()
    )
    reading_bands = [sanitize_band(r.estimated_band) for r in reading_rows]
    reading_bands = [b for b in reading_bands if b is not None]

    listening_rows = (
        db.query(ListeningAttempt)
        .filter_by(student_id=student_id, status="submitted")
        .order_by(ListeningAttempt.submitted_at.desc())
        .limit(50)
        .all()
    )
    listening_bands = [sanitize_band(r.estimated_band) for r in listening_rows]
    listening_bands = [b for b in listening_bands if b is not None]

    writing_rows = (
        db.query(WritingAttempt)
        .filter(
            WritingAttempt.student_id == student_id,
            WritingAttempt.status.in_(("graded", "published", "teacher_review")),
            WritingAttempt.submitted_at.isnot(None),
        )
        .order_by(WritingAttempt.submitted_at.desc())
        .limit(50)
        .all()
    )
    writing_effective = [
        effective_result_band(
            teacher_band=a.teacher_band,
            final_band=a.final_band,
            estimated_band=a.estimated_band,
        )
        for a in writing_rows
    ]
    writing_effective = [b for b in writing_effective if b is not None]

    speaking_sessions = (
        db.query(TestSession)
        .filter(TestSession.student_id == student_id, TestSession.status == "completed")
        .order_by(TestSession.completed_at.desc())
        .limit(50)
        .all()
    )
    speaking_effective: list[float] = []
    for s in speaking_sessions:
        ev = db.query(Evaluation).filter_by(session_id=s.id).first()
        band = effective_result_band(
            teacher_band=ev.teacher_band if ev else None,
            final_band=ev.final_band if ev else None,
            estimated_band=(ev.estimated_band if ev and ev.estimated_band is not None else s.estimated_band),
        )
        if band is not None:
            speaking_effective.append(band)

    return {
        "reading": {
            "submitted_count": len(reading_rows),
            "average_band": average_bands(reading_bands),
            "latest_band": reading_bands[0] if reading_bands else None,
        },
        "listening": {
            "submitted_count": len(listening_rows),
            "average_band": average_bands(listening_bands),
            "latest_band": listening_bands[0] if listening_bands else None,
        },
        "writing": {
            "submitted_count": len(writing_rows),
            "average_band": average_bands(writing_effective),
            "latest_band": writing_effective[0] if writing_effective else None,
            "policy": "teacher_final_preferred",
        },
        "speaking": {
            "submitted_count": len(speaking_sessions),
            "average_band": average_bands(speaking_effective),
            "latest_band": speaking_effective[0] if speaking_effective else None,
            "policy": "teacher_final_preferred",
        },
        "overall_ielts": None,
        "overall_note": "Skill averages only — not a four-skill IELTS overall.",
    }


def student_dashboard(db: Session, student_id: str) -> dict:
    hw_subs = db.query(HomeworkSubmission).filter_by(student_id=student_id).all()
    hw_by_status = {"pending": 0, "in_progress": 0, "submitted": 0, "reviewed": 0, "published": 0}
    overdue = 0
    now = datetime.utcnow()
    for sub in hw_subs:
        st = sub.status or "pending"
        if st in hw_by_status:
            hw_by_status[st] += 1
        parent = db.query(HomeworkAssignment).filter_by(id=sub.assignment_id).first()
        if parent and parent.deadline and parent.deadline < now and st in ("pending", "in_progress"):
            overdue += 1

    # Assignments visible but not started count as open via student list semantics:
    # open = not completed (submitted/reviewed/published)
    open_hw = hw_by_status["pending"] + hw_by_status["in_progress"]
    # Also count assigned-but-no-submission via assignment visibility is expensive;
    # frontend already uses homework list for open count. Provide submission-based + note.
    completed_hw = hw_by_status["submitted"] + hw_by_status["reviewed"] + hw_by_status["published"]

    mock_slots = db.query(MockAssignmentStudent).filter_by(student_id=student_id).all()
    mock_open = 0
    mock_submitted = 0
    mock_published = 0
    for slot in mock_slots:
        if slot.cancelled:
            continue
        attempt = (
            db.query(MockAttempt)
            .filter_by(assignment_student_id=slot.id)
            .order_by(MockAttempt.started_at.desc())
            .first()
        )
        if attempt and attempt.published:
            mock_published += 1
            mock_submitted += 1
        elif attempt and attempt.status in ("submitted", "reviewed"):
            mock_submitted += 1
        elif not attempt or attempt.status in ("in_progress", "ready", None):
            mock_open += 1

    skills = skill_progress_for_student(db, student_id)
    return {
        "student_id": student_id,
        "homework": {
            **hw_by_status,
            "open": open_hw,
            "completed": completed_hw,
            "overdue": overdue,
            "pending_review": hw_by_status["submitted"] + hw_by_status["reviewed"],
        },
        "mocks": {
            "open": mock_open,
            "submitted": mock_submitted,
            "published": mock_published,
            "note": "Single-skill mocks — skill_band is not four-skill IELTS overall.",
        },
        "skills": skills,
    }


def _pending_homework_reviews(db: Session, *, branch_id: Optional[str] = None, creator_id: Optional[str] = None) -> int:
    q = (
        db.query(func.count(HomeworkSubmission.id))
        .join(HomeworkAssignment, HomeworkAssignment.id == HomeworkSubmission.assignment_id)
        .filter(HomeworkSubmission.status == "submitted")
    )
    if creator_id:
        q = q.filter(HomeworkAssignment.created_by == creator_id)
    elif branch_id:
        # Assignments touching students in branch OR created by staff in branch
        student_ids = _student_ids_in_branch(db, branch_id)
        if not student_ids:
            return 0
        q = q.filter(HomeworkSubmission.student_id.in_(student_ids))
    return int(q.scalar() or 0)


def _active_homework_count(db: Session, *, creator_id: Optional[str] = None, branch_id: Optional[str] = None) -> int:
    q = db.query(func.count(HomeworkAssignment.id))
    if creator_id:
        q = q.filter(HomeworkAssignment.created_by == creator_id)
    elif branch_id:
        # Creator in branch
        staff_ids = [
            r[0]
            for r in db.query(User.id)
            .filter(User.branch_id == branch_id, User.role.in_(("teacher", "branch_admin")))
            .all()
        ]
        if not staff_ids:
            return 0
        q = q.filter(HomeworkAssignment.created_by.in_(staff_ids))
    return int(q.scalar() or 0)


def _branch_skill_average(db: Session, branch_id: str) -> Optional[float]:
    """Mean of recent effective skill bands for active students in branch (not IELTS overall)."""
    student_ids = _student_ids_in_branch(db, branch_id)
    if not student_ids:
        return None
    samples: list[float] = []

    for band in (
        db.query(ReadingAttempt.estimated_band)
        .filter(ReadingAttempt.student_id.in_(student_ids), ReadingAttempt.status == "submitted")
        .limit(200)
        .all()
    ):
        b = sanitize_band(band[0])
        if b is not None:
            samples.append(b)

    for band in (
        db.query(ListeningAttempt.estimated_band)
        .filter(ListeningAttempt.student_id.in_(student_ids), ListeningAttempt.status == "submitted")
        .limit(200)
        .all()
    ):
        b = sanitize_band(band[0])
        if b is not None:
            samples.append(b)

    for a in (
        db.query(WritingAttempt)
        .filter(
            WritingAttempt.student_id.in_(student_ids),
            WritingAttempt.status.in_(("graded", "published", "teacher_review")),
        )
        .limit(200)
        .all()
    ):
        b = effective_result_band(
            teacher_band=a.teacher_band, final_band=a.final_band, estimated_band=a.estimated_band
        )
        if b is not None:
            samples.append(b)

    for s in (
        db.query(TestSession)
        .filter(TestSession.student_id.in_(student_ids), TestSession.status == "completed")
        .limit(200)
        .all()
    ):
        ev = db.query(Evaluation).filter_by(session_id=s.id).first()
        b = effective_result_band(
            teacher_band=ev.teacher_band if ev else None,
            final_band=ev.final_band if ev else None,
            estimated_band=(ev.estimated_band if ev and ev.estimated_band is not None else s.estimated_band),
        )
        if b is not None:
            samples.append(b)

    return average_bands(samples)


def teacher_dashboard(db: Session, teacher: User) -> dict:
    branch_id = teacher.branch_id or ""
    students = _student_ids_in_branch(db, branch_id) if branch_id else []
    batches = (
        db.query(Batch).filter_by(branch_id=branch_id).order_by(Batch.name.asc()).all() if branch_id else []
    )
    batch_rows = []
    for b in batches:
        count = (
            db.query(func.count(User.id))
            .filter(User.role == "student", User.branch_id == branch_id, User.batch == b.name, User.is_active.is_(True))
            .scalar()
            or 0
        )
        batch_rows.append({"id": b.id, "name": b.name, "active_students": int(count)})

    pending = _pending_homework_reviews(db, creator_id=teacher.id)
    # Also count submitted homework for students in branch on assignments this teacher can see
    # Prefer creator-owned; if zero, fall back to branch-visible submitted for transparency
    if pending == 0 and branch_id:
        pending = _pending_homework_reviews(db, branch_id=branch_id)

    active_hw = _active_homework_count(db, creator_id=teacher.id)
    avg = _branch_skill_average(db, branch_id) if branch_id else None

    pending_items = (
        db.query(HomeworkSubmission)
        .join(HomeworkAssignment, HomeworkAssignment.id == HomeworkSubmission.assignment_id)
        .filter(
            HomeworkSubmission.status == "submitted",
            HomeworkAssignment.created_by == teacher.id,
        )
        .order_by(HomeworkSubmission.submitted_at.desc())
        .limit(8)
        .all()
    )
    recent = []
    for sub in pending_items:
        stu = db.query(User).filter_by(id=sub.student_id).first()
        asn = db.query(HomeworkAssignment).filter_by(id=sub.assignment_id).first()
        recent.append(
            {
                "submission_id": sub.id,
                "assignment_id": sub.assignment_id,
                "title": asn.title if asn else "Homework",
                "student_id": sub.student_id,
                "student_name": (stu.full_name if stu else sub.student_id),
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            }
        )

    return {
        "branch_id": branch_id,
        "active_students": len(students),
        "active_students_label": "Active students on your campus",
        "batches": batch_rows,
        "pending_reviews": pending,
        "active_homework": active_hw,
        "skill_average_band": avg,
        "skill_average_note": "Mean of recent skill results on campus — not four-skill IELTS overall.",
        "recent_pending_reviews": recent,
    }


def branch_admin_dashboard(db: Session, admin: User) -> dict:
    branch_id = admin.branch_id or ""
    if not branch_id:
        return {
            "branch_id": None,
            "active_teachers": 0,
            "active_students": 0,
            "inactive_students": 0,
            "batches": 0,
            "skill_average_band": None,
            "pending_reviews": 0,
            "active_homework": 0,
        }
    active_teachers = (
        db.query(func.count(User.id))
        .filter(User.role == "teacher", User.branch_id == branch_id, User.is_active.is_(True))
        .scalar()
        or 0
    )
    active_students = (
        db.query(func.count(User.id))
        .filter(User.role == "student", User.branch_id == branch_id, User.is_active.is_(True))
        .scalar()
        or 0
    )
    inactive_students = (
        db.query(func.count(User.id))
        .filter(User.role == "student", User.branch_id == branch_id, User.is_active.is_(False))
        .scalar()
        or 0
    )
    batches = db.query(func.count(Batch.id)).filter_by(branch_id=branch_id).scalar() or 0
    return {
        "branch_id": branch_id,
        "active_teachers": int(active_teachers),
        "active_students": int(active_students),
        "inactive_students": int(inactive_students),
        "batches": int(batches),
        "skill_average_band": _branch_skill_average(db, branch_id),
        "skill_average_note": "Campus skill mean — not four-skill IELTS overall.",
        "pending_reviews": _pending_homework_reviews(db, branch_id=branch_id),
        "active_homework": _active_homework_count(db, branch_id=branch_id),
    }


def super_admin_dashboard(db: Session) -> dict:
    branches_total = db.query(func.count(Branch.id)).scalar() or 0
    branches_active = db.query(func.count(Branch.id)).filter(Branch.is_active.is_(True)).scalar() or 0
    students_active = (
        db.query(func.count(User.id)).filter(User.role == "student", User.is_active.is_(True)).scalar() or 0
    )
    students_inactive = (
        db.query(func.count(User.id)).filter(User.role == "student", User.is_active.is_(False)).scalar() or 0
    )
    teachers_active = (
        db.query(func.count(User.id)).filter(User.role == "teacher", User.is_active.is_(True)).scalar() or 0
    )
    branch_admins = (
        db.query(func.count(User.id)).filter(User.role == "branch_admin", User.is_active.is_(True)).scalar() or 0
    )
    batches = db.query(func.count(Batch.id)).scalar() or 0

    # Network skill average across all active students' recent attempts (sampled).
    samples: list[float] = []
    for band in db.query(ReadingAttempt.estimated_band).filter(ReadingAttempt.status == "submitted").limit(300).all():
        b = sanitize_band(band[0])
        if b is not None:
            samples.append(b)
    for band in db.query(ListeningAttempt.estimated_band).filter(ListeningAttempt.status == "submitted").limit(300).all():
        b = sanitize_band(band[0])
        if b is not None:
            samples.append(b)
    for a in (
        db.query(WritingAttempt)
        .filter(WritingAttempt.status.in_(("graded", "published", "teacher_review")))
        .limit(300)
        .all()
    ):
        b = effective_result_band(
            teacher_band=a.teacher_band, final_band=a.final_band, estimated_band=a.estimated_band
        )
        if b is not None:
            samples.append(b)

    return {
        "branches_total": int(branches_total),
        "branches_active": int(branches_active),
        "active_students": int(students_active),
        "inactive_students": int(students_inactive),
        "active_teachers": int(teachers_active),
        "active_branch_admins": int(branch_admins),
        "batches": int(batches),
        "skill_average_band": average_bands(samples),
        "skill_average_note": "Network skill mean from recent attempts — not four-skill IELTS overall. Includes historic inactive-branch data when attempts exist.",
    }


def _homework_visible_to_student(row: HomeworkAssignment, student_id: str, batch: Optional[str]) -> bool:
    ids = row.student_ids or []
    if isinstance(ids, list) and student_id in ids:
        return True
    if row.batch_label and batch and row.batch_label == batch:
        return True
    return False


def student_record_analytics(db: Session, student_id: str, *, actor: User) -> Optional[dict]:
    """Homework + practice counts for a student visible to staff in scope."""
    student = db.query(User).filter_by(id=student_id, role="student").first()
    if not student:
        return None
    if actor.role in ("branch_admin", "teacher") and student.branch_id != actor.branch_id:
        return None

    batch = student.batch
    visible_asns = [
        row
        for row in db.query(HomeworkAssignment).order_by(HomeworkAssignment.created_at.desc()).limit(200).all()
        if _homework_visible_to_student(row, student_id, batch)
    ]
    subs = db.query(HomeworkSubmission).filter_by(student_id=student_id).all()
    sub_by_asn = {s.assignment_id: s for s in subs}
    assigned = len(visible_asns) if visible_asns else len(subs)
    completed = 0
    in_progress = 0
    pending = 0
    for row in visible_asns or []:
        sub = sub_by_asn.get(row.id)
        if not sub or sub.status in ("pending",):
            pending += 1
        elif sub.status == "in_progress":
            in_progress += 1
        elif sub.status in ("submitted", "reviewed", "published"):
            completed += 1
    if not visible_asns:
        completed = len([s for s in subs if s.status in ("submitted", "reviewed", "published")])
        in_progress = len([s for s in subs if s.status == "in_progress"])
        pending = len([s for s in subs if s.status in ("pending", None)])

    reading_n = (
        db.query(func.count(ReadingAttempt.id))
        .filter_by(student_id=student_id, status="submitted")
        .scalar()
        or 0
    )
    listening_n = (
        db.query(func.count(ListeningAttempt.id))
        .filter_by(student_id=student_id, status="submitted")
        .scalar()
        or 0
    )
    writing_n = (
        db.query(func.count(WritingAttempt.id))
        .filter(
            WritingAttempt.student_id == student_id,
            WritingAttempt.status.in_(("graded", "published", "teacher_review")),
        )
        .scalar()
        or 0
    )
    speaking_n = (
        db.query(func.count(TestSession.id))
        .filter(TestSession.student_id == student_id, TestSession.status == "completed")
        .scalar()
        or 0
    )
    practice = int(reading_n) + int(listening_n) + int(writing_n) + int(speaking_n)

    return {
        "student_id": student_id,
        "branch_id": student.branch_id,
        "assigned": assigned,
        "completed": completed,
        "in_progress": in_progress,
        "pending": pending,
        "completion_pct": round((completed / assigned) * 100) if assigned else 0,
        "practice_sessions": practice,
        "skills": [
            {"skill": "Reading", "assigned": int(reading_n), "completed": int(reading_n)},
            {"skill": "Listening", "assigned": int(listening_n), "completed": int(listening_n)},
            {"skill": "Writing", "assigned": int(writing_n), "completed": int(writing_n)},
            {"skill": "Speaking", "assigned": int(speaking_n), "completed": int(speaking_n)},
        ],
        "progress": skill_progress_for_student(db, student_id),
    }
