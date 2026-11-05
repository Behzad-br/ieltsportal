"""Role-scoped analytics API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics import service
from app.auth.deps import get_current_user, require_roles, resolve_student_id
from app.auth.models import User
from app.database import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/student")
async def analytics_student(
    db: Session = Depends(get_db),
    student_id: str = Depends(resolve_student_id),
    user: User = Depends(get_current_user),
):
    # Students always see own dashboard; staff may impersonate within existing rules.
    if user.role == "student" and student_id != user.id:
        raise HTTPException(403, "Students may only view their own analytics.")
    return service.student_dashboard(db, student_id)


@router.get("/teacher")
async def analytics_teacher(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("teacher")),
):
    return service.teacher_dashboard(db, user)


@router.get("/branch-admin")
async def analytics_branch_admin(
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("branch_admin")),
):
    return service.branch_admin_dashboard(db, user)


@router.get("/super-admin")
async def analytics_super_admin(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("super_admin")),
):
    return service.super_admin_dashboard(db)


@router.get("/student-record/{student_id}")
async def analytics_student_record(
    student_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("teacher", "branch_admin", "super_admin")),
):
    data = service.student_record_analytics(db, student_id, actor=user)
    if data is None:
        raise HTTPException(404, "Student not found or outside your campus.")
    return data
