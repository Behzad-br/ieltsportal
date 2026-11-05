# FTI IELTS LMS — Complete Project Report

**Product name:** FTI IELTS Learning Management System (Writewise / IELTS Speaking AI stack)  
**Organization:** FTI network campuses  
**Production:** https://fti4iltes.tech · API https://api.fti4iltes.tech  
**Report date:** October 2026 (Modules 1–24 complete)  

---

## 1. What this is

A **multi-campus IELTS Learning Management System (LMS)** for FTI branches.

Students, teachers, branch admins, and super admins share one platform for:

- IELTS **Reading, Listening, Writing, Speaking** practice (DB-backed attempts)
- Teacher **homework** assign + review
- **Single-skill Mock Exams** (timed / monitored — one IELTS skill per assignment)
- Class **notes** share
- AI **estimated** band feedback (practice estimate — not an official IELTS score)
- Role-scoped **results** and **analytics**

---

## 2. Tech stack (actual)

| Layer | Technology |
| --- | --- |
| Frontend | React 19 + TypeScript + Vite + Tailwind + Wouter |
| Backend | Python FastAPI + SQLAlchemy + Uvicorn |
| Realtime | Socket.IO (JWT-authenticated mock telemetry) |
| Auth | JWT (`HS256`) + DB-backed identity; PBKDF2-SHA256 passwords |
| Database | **PostgreSQL (Neon)** for staging/verify; **MySQL/MariaDB** for Hestia VPS prod; SQLite for local/tests. **No Supabase client.** |
| Migrations | **Alembic** owns staging/production schema — head `20261012_0007` |
| AI | Server-side LLM (OpenAI / MiniMax) for writing & speaking feedback |
| Speech | OpenAI Whisper + **FFmpeg** (OS package required for Speaking) |
| Deploy | HestiaCP + Nginx + PM2 (API); SPA on `fti4iltes.tech` |

Local topology: Frontend `:5173` → Vite `/api` proxy → Backend `:5000` → DB.

---

## 3. Roles

| Role | Login | Backend value |
| --- | --- | --- |
| Super Admin | `/super-admin-login` | `super_admin` |
| Branch Admin | `/branch-admin-login` | `branch_admin` |
| Teacher | `/login` | `teacher` |
| Student | `/login` | `student` |

Frontend route guards are UX only. **Backend is the authorization authority.**

Production requires:

- `AUTH_LEGACY_HEADERS=false`
- strong `JWT_SECRET` (not the placeholder)
- `AUTO_CREATE_SCHEMA=false` (Alembic owns schema)
- Postgres `sslmode=require` on managed DBs
- `VITE_DEMO_MODE` unset/false

---

## 4. Architecture principles (Modules 1–24)

- **Branch isolation** enforced server-side
- Student work **persisted before AI/STT** when those fail
- Answer keys **hidden** before Reading/Listening submit
- Academic scores are **server-calculated** or teacher-reviewed — not client-trusted
- Socket.IO requires **JWT**; room join is ownership-scoped
- LiveKit token mint is **JWT-role + assignment scoped** (optional when unconfigured)
- Private media (Speaking recordings, Notes, Homework audio) is **ACL-protected**
- Persistent media must live under configured `DATA_DIR` / `SPEAKING_AUDIO_DIR` / `NOTES_DIR` (not ephemeral container disk alone)

---

## 5. Features by role

### Super Admin
Network dashboard, branches create/edit/status, initial Branch Admins, org overview, network analytics.

### Branch Admin
Own campus only: teachers, students, batches, campus analytics. Cannot access other branches.

### Teacher
Campus students/batches, Homework composer, Notes, single-skill Mocks, review/publish, teacher analytics.

### Student
Dashboard, four-skill practice, homework, notes (unlocked only), mock inbox, results, student analytics.

---

## 6. IELTS skills

| Skill | Persistence | Notes |
| --- | --- | --- |
| Reading | `reading_attempts` | Keys stripped pre-submit; idempotent submit |
| Listening | `listening_attempts` | Public pack audio; scored submit |
| Writing | `writing_attempts` | Draft autosave; AI fail → essay kept |
| Speaking | `test_sessions` + audio files | Whisper+FFmpeg; durable audio dir required |

**Practice bands are estimates**, not official IELTS scores.

---

## 7. Mock exams (product limitation)

Mocks are **one IELTS skill per assignment** (Reading **or** Listening **or** Writing **or** Speaking).

They are **not** a combined four-skill full IELTS mock. UI and APIs must not imply an overall four-skill IELTS score from a single-skill assignment.

---

## 8. Security posture (Modules 20–23)

- JWT → reload user from DB (forged role/branch claims ignored)
- Cross-student / cross-branch IDOR matrix covered
- Homework nested answer keys recursively stripped
- Ops `WRITING_ADMIN_TOKEN` disabled in production unless `ALLOW_WRITING_ADMIN_TOKEN=true`
- CORS allowlist (no `*` + credentials)
- Security headers: nosniff, frame deny, referrer, permissions, HSTS on HTTPS
- Error responses sanitized (no secrets/tracebacks in production)

Residual accepted risks: ~12h JWT without server blacklist; token in `localStorage` (XSS surface); public practice pack media by design.

---

## 9. Main API map

| Prefix | Purpose |
| --- | --- |
| `/api/auth/*` | Login, me, change-password |
| `/api/org/*` | Users, branches, batches |
| `/api/health` | Health + DB + Speaking dependency flags |
| `/api/tests/*`, `/api/answers/*`, `/api/history/*` | Speaking |
| `/api/writing/*`, `/api/writing/admin/*` | Writing |
| `/api/reading/*` | Reading |
| `/api/listening/*` | Listening |
| `/api/homework/*` | Homework |
| `/api/notes/*` | Notes |
| `/api/mocks/*` | Single-skill mocks + LiveKit token |
| `/api/analytics/*` | Role-scoped analytics |
| Socket.IO | Mock realtime (JWT required) |

---

## 10. Deployment

Authoritative guide: `DEPLOYMENT.md` (Hestia VPS).

Required OS packages: `python3`, `ffmpeg`.  
Backend: `pip install -r requirements.txt` then `alembic upgrade head` then uvicorn/`asgi_app`.  
Frontend: `npm ci` / `npm install`, set `VITE_API_URL` for production builds, `npm run build`.

Readiness validators:

- `python scripts/check_db_security_config.py`
- `python scripts/check_production_ready.py`
- `python scripts/neon_m24_verify.py` (Neon E2E)

---

## 11. What this is NOT

- Official British Council / IDP IELTS exam platform
- AI band ≠ official score
- Free public consumer app — FTI campus LMS
- Full four-skill combined mock exam product

---

## 12. Docs index

| File | Content |
| --- | --- |
| `README.md` | Quick start |
| `DEPLOYMENT.md` | Production VPS (Hestia) |
| `backend/docs/MIGRATIONS.md` | Alembic |
| `backend/API_CONTRACT.md` | API consistency notes |
| `PROJECT_REPORT.md` | This report |

---

*Modules 1–24 audit/hardening program complete. See Module 24 report for GO/NO-GO.*
