"""Socket.IO for mock telemetry only. Live screens stay on LiveKit.

Connections require a valid JWT (auth.token or Authorization). Room join and
events are bound to DB identity — client-supplied role/studentId are not trusted.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import socketio
except Exception:  # pragma: no cover - optional add-on
    socketio = None  # type: ignore

def _cors_origins():
    try:
        from app.config import settings

        return settings.cors_origin_list() or []
    except Exception:
        return []


sio = (
    socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=_cors_origins() or [],
        logger=False,
        engineio_logger=False,
    )
    if socketio
    else None
)

WARNING_EVENTS = {
    "FULLSCREEN_EXIT",
    "TAB_HIDDEN",
    "WINDOW_BLUR",
    "SCREEN_SHARE_STOPPED",
    "DISCONNECTED",
}


def attach_socketio(fastapi_app):
    if not socketio or sio is None:
        return fastapi_app
    return socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="socket.io")


async def emit_assignment_state(assignment_id: str, payload: dict):
    if sio is None:
        return
    try:
        await sio.emit("mock:state", payload, room=f"assignment:{assignment_id}")
    except Exception as exc:
        logger.warning("Could not emit mock state: %s", exc)


def _token_from_connect(environ, auth) -> str:
    if isinstance(auth, dict):
        raw = auth.get("token") or auth.get("access_token") or ""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        authz = auth.get("Authorization") or auth.get("authorization") or ""
        if isinstance(authz, str) and authz.lower().startswith("bearer "):
            return authz[7:].strip()
    # Engine.IO may expose HTTP headers on the environ.
    headers = environ.get("HTTP_AUTHORIZATION") or ""
    if isinstance(headers, str) and headers.lower().startswith("bearer "):
        return headers[7:].strip()
    return ""


def _user_from_bearer(token: str):
    from app.auth.models import User
    from app.auth.security import decode_access_token
    from app.database import SessionLocal
    from app.auth.access import assert_user_campus_active

    if not token or token == "demo-local-token":
        return None
    try:
        payload = decode_access_token(token)
    except Exception:
        return None
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.is_active:
            return None
        assert_user_campus_active(db, user)
        return {
            "user_id": user.id,
            "role": user.role,
            "branch_id": user.branch_id or "",
        }
    except Exception:
        return None
    finally:
        db.close()


def _teacher_may_join(db, assignment_id: str, user: dict) -> bool:
    from app.mocks.models import MockAssignment, MockAssignmentStudent
    from app.auth.models import User
    from app.auth.access import users_in_branch

    row = db.query(MockAssignment).filter_by(id=assignment_id).first()
    if not row:
        return False
    role = user.get("role")
    uid = user.get("user_id") or ""
    if role == "super_admin":
        return True
    if role == "teacher" and (row.created_by or "") == uid:
        return True
    if role == "branch_admin":
        campus = (user.get("branch_id") or "").strip()
        if not campus:
            return False
        creator = db.query(User).filter_by(id=row.created_by).first() if row.created_by else None
        if creator and (creator.branch_id or "").strip() == campus:
            return True
        slots = db.query(MockAssignmentStudent).filter_by(assignment_id=row.id).all()
        allowed = set(users_in_branch(db, campus, role="student"))
        return any(s.student_id in allowed for s in slots)
    return False


def _student_may_join(db, assignment_id: str, student_id: str) -> bool:
    from app.mocks.models import MockAssignmentStudent

    slot = (
        db.query(MockAssignmentStudent)
        .filter_by(assignment_id=assignment_id, student_id=student_id)
        .first()
    )
    return bool(slot and not slot.cancelled)


if sio is not None:

    @sio.event
    async def connect(sid, environ, auth):
        token = _token_from_connect(environ, auth)
        identity = _user_from_bearer(token)
        if not identity:
            return False
        await sio.save_session(sid, identity)
        return True

    @sio.event
    async def disconnect(sid):
        try:
            session = await sio.get_session(sid)
        except Exception:
            return
        assignment_id = (session or {}).get("assignment_id")
        student_id = (session or {}).get("user_id")
        role = (session or {}).get("role")
        if not assignment_id or role != "student" or not student_id:
            return
        try:
            from app.database import SessionLocal
            from app.mocks.models import MockAttempt

            db = SessionLocal()
            try:
                attempt = (
                    db.query(MockAttempt)
                    .filter_by(student_id=str(student_id), assignment_id=str(assignment_id), status="in_progress")
                    .order_by(MockAttempt.started_at.desc())
                    .first()
                )
                if not attempt:
                    return
                attempt.connection_status = "disconnected"
                attempt.last_warning = "DISCONNECTED"
                db.commit()
                await emit_assignment_state(
                    str(assignment_id),
                    {
                        "assignmentId": assignment_id,
                        "eventType": "DISCONNECTED",
                        "studentId": student_id,
                        "connection": "disconnected",
                        "lastWarning": "DISCONNECTED",
                        "status": "disconnected",
                    },
                )
            finally:
                db.close()
        except Exception as exc:
            logger.warning("mock disconnect handler failed: %s", exc)

    @sio.on("join_assignment")
    async def join_assignment(sid, data):
        data = data or {}
        assignment_id = str(data.get("assignmentId") or "").strip()
        if not assignment_id or len(assignment_id) > 80:
            return {"ok": False}
        try:
            session = await sio.get_session(sid)
        except Exception:
            return {"ok": False}
        if not session or not session.get("user_id"):
            return {"ok": False}

        from app.database import SessionLocal

        db = SessionLocal()
        try:
            role = session.get("role")
            uid = session["user_id"]
            if role == "student":
                if not _student_may_join(db, assignment_id, uid):
                    return {"ok": False}
                room_role = "student"
            elif role in {"teacher", "branch_admin", "super_admin"}:
                if not _teacher_may_join(db, assignment_id, session):
                    return {"ok": False}
                room_role = "teacher"
            else:
                return {"ok": False}
            session["assignment_id"] = assignment_id
            session["room_role"] = room_role
            await sio.save_session(sid, session)
            await sio.enter_room(sid, f"assignment:{assignment_id}")
            return {"ok": True}
        finally:
            db.close()

    @sio.on("teacher:voice")
    async def teacher_voice(sid, data):
        """Broadcast live-talk banner without mutating student attempt rows."""
        data = data or {}
        try:
            session = await sio.get_session(sid)
        except Exception:
            return {"ok": False}
        if str((session or {}).get("room_role") or "") != "teacher":
            return {"ok": False}
        if str((session or {}).get("role") or "") not in {"teacher", "branch_admin", "super_admin"}:
            return {"ok": False}
        assignment_id = str(
            (session or {}).get("assignment_id") or data.get("assignmentId") or ""
        ).strip()
        if not assignment_id or assignment_id != str((session or {}).get("assignment_id") or ""):
            return {"ok": False}
        active = bool(data.get("active"))
        await emit_assignment_state(
            assignment_id,
            {
                "assignmentId": assignment_id,
                "eventType": "TEACHER_VOICE_ON" if active else "TEACHER_VOICE_OFF",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "studentId": data.get("studentId"),
                "attemptId": data.get("attemptId"),
            },
        )
        return {"ok": True}

    @sio.on("mock:event")
    async def mock_event(sid, data):
        data = data or {}
        attempt_id = str(data.get("attemptId") or "")
        event_type = str(data.get("eventType") or "")
        if not attempt_id or not event_type:
            return {"ok": False}
        try:
            from app.database import SessionLocal
            from app.mocks.models import MockAttempt, MockAssignment, MockAssignmentStudent

            db = SessionLocal()
            try:
                attempt = db.query(MockAttempt).filter_by(id=attempt_id).first()
                if not attempt:
                    return {"ok": False}
                session = await sio.get_session(sid)
                role = str((session or {}).get("role") or "")
                room_role = str((session or {}).get("room_role") or "")
                uid = str((session or {}).get("user_id") or "")
                joined = str((session or {}).get("assignment_id") or "")
                if joined and joined != attempt.assignment_id:
                    return {"ok": False}
                # Students may only emit for their own attempt; teachers may notify.
                if role == "student":
                    if attempt.student_id != uid:
                        return {"ok": False}
                elif role in {"teacher", "branch_admin", "super_admin"}:
                    if room_role != "teacher":
                        return {"ok": False}
                else:
                    return {"ok": False}
                now = datetime.utcnow()
                voice_only = event_type in ("TEACHER_VOICE_ON", "TEACHER_VOICE_OFF")
                if not voice_only and role == "student":
                    attempt.last_seen_at = now
                    attempt.connection_status = "stable"
                    if data.get("currentSection"):
                        attempt.current_section = data["currentSection"]
                    if data.get("currentQuestion") is not None:
                        attempt.current_question = int(data["currentQuestion"])
                    if event_type == "SCREEN_SHARE_STARTED":
                        attempt.screen_share_active = True
                    if event_type == "SCREEN_SHARE_STOPPED":
                        attempt.screen_share_active = False
                    if event_type in ("FULLSCREEN_RESTORED", "WINDOW_FOCUS", "RECONNECTED"):
                        attempt.last_warning = None
                    if event_type in WARNING_EVENTS:
                        attempt.last_warning = event_type
                    db.commit()
                assignment = db.query(MockAssignment).filter_by(id=attempt.assignment_id).first()
                slot = db.query(MockAssignmentStudent).filter_by(id=attempt.assignment_student_id).first()
                payload = {
                    "assignmentId": attempt.assignment_id,
                    "eventType": event_type,
                    "timestamp": now.isoformat() + "Z",
                    "studentId": attempt.student_id,
                    "attemptId": attempt.id,
                    "currentSection": attempt.current_section,
                    "currentQuestion": attempt.current_question,
                    "remainingSeconds": attempt.remaining_seconds,
                    "warningCount": attempt.warning_count,
                    "screenSharing": bool(attempt.screen_share_active),
                    "connection": attempt.connection_status or "stable",
                    "lastWarning": attempt.last_warning,
                    "status": attempt.status,
                    "studentName": slot.student_name if slot else attempt.student_id,
                    "slotId": slot.id if slot else None,
                    "questionTotal": attempt.question_total,
                    "paused": bool(attempt.paused),
                    "title": assignment.title if assignment else "",
                }
            finally:
                db.close()
            await emit_assignment_state(payload["assignmentId"], payload)
            return {"ok": True}
        except Exception as exc:
            logger.warning("mock:event failed: %s", exc)
            return {"ok": False}
