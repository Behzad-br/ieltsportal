# FTI IELTS LMS — Complete Project Report

**Product name:** FTI IELTS Learning Management System (Writewise / IELTS Speaking AI stack)  
**Organization:** FTI network campuses  
**Production:** https://fti4iltes.tech · API https://api.fti4iltes.tech  
**Report date:** September 2026  

---

## 1. Project kya hai?

Ye ek **multi-campus IELTS Learning Management System (LMS)** hai jo FTI branches ke liye bana hai.

Isme students, teachers, branch admins aur super admin ek hi platform pe kaam karte hain:

- IELTS **Reading, Listening, Writing, Speaking** practice  
- Teacher **homework** assign + review  
- **Mock exams** (timed / monitored)  
- Class **notes** share  
- AI se **estimated band feedback** (official IELTS score nahi — practice estimate)

---

## 2. Kyun bana hai? (Purpose)

| Problem | Solution is project se |
| --- | --- |
| Har campus alag process / WhatsApp pe homework | Central LMS — har branch apna workspace |
| Students ko structured IELTS practice nahi milti | 4 skills + banks + AI feedback |
| Teachers ko submissions track karna mushkil | Homework queue, reviews, student record |
| Super admin ko network-wide control chahiye | Branches + branch admins manage |
| Official test se pehle practice feedback | AI estimated bands (Writing / Speaking) |

**Goal:** FTI students ko class ke bahar bhi continuous IELTS practice + teachers ko campus-level control.

---

## 3. Tech stack

| Layer | Technology |
| --- | --- |
| Frontend | React 19 + TypeScript + Vite + Tailwind + Wouter |
| Backend | Python FastAPI + SQLAlchemy + Uvicorn |
| Realtime | Socket.IO (mock live monitoring) |
| Auth | JWT (`access_token`) + PBKDF2 password hashing |
| Database | MySQL (production) / SQLite (local/dev) |
| AI | OpenAI / LLM for writing & speaking feedback |
| Speech | OpenAI Whisper (speaking transcription) |
| Deploy (prod) | HestiaCP + Nginx + PM2 |
| Staging option | Vercel (frontend) + Render (backend) |

---

## 4. User roles (4 types)

| Role | Login page | Backend role value |
| --- | --- | --- |
| Super Admin | `/super-admin-login` | `super_admin` |
| Branch Admin | `/branch-admin-login` | `branch_admin` |
| Teacher | `/login` | `teacher` |
| Student | `/login` | `student` |

Har login ke baad JWT milta hai: `{ access_token, user: { id, role, branch_id, ... } }`.

---

## 5. Features by role (complete)

### 5.1 Super Admin

- Network-wide dashboard  
- **Branches create / edit / remove** (campus name, city, image)  
- **Branch Admin accounts** create with username + password (hashed in DB)  
- View branch admins list  
- Teachers / students overview across network  
- Reports  
- Super Admin settings (profile / password)  
- Practice papers management (UI)  

### 5.2 Branch Admin

- **Only apni branch** dekhta / manage karta hai  
- Teachers add / edit / remove (branch-scoped)  
- Students add / edit / remove  
- Batches create / manage  
- Branch reports  
- Own account settings (name, username, password → live API)  

### 5.3 Teacher

- Dashboard (homework queue, batches snapshot)  
- **Batches** list + detail  
- **Students** roster + student detail / work record  
- **Homework Composer** — assign Reading / Listening / Writing / Speaking  
  - From practice bank, manual, or AI-assisted preview  
  - Entire batch or specific students  
  - Optional AI grading flag  
- Homework list + submissions + review  
- **Review queue** for student writing  
- **Notes** — upload PDF/docs, unlock by batch or student  
- **Mock exams**  
  - Library  
  - Assign mock  
  - Assigned list  
  - Live monitoring (Socket.IO / LiveKit optional)  
  - Results / publish review  
- Teacher settings  

### 5.4 Student

- Dashboard (open homework, mocks, practice shortcuts)  
- **Homework** list → open → do → submit  
- **Notes** library → page reader (watermarked where locked rules apply)  
- **IELTS Practice** hub — Reading / Listening / Writing / Speaking  
- Practice rooms (timed writing, audio listening, mic speaking, reading highlights)  
- AI / estimated feedback screens  
- Results + history  
- **Mock tests** inbox → briefing → timed mock room → submit  
- Profile / notifications  
- Sign out clears JWT session  

---

## 6. IELTS skill modules (detail)

### Reading
- Catalog of Academic / General tests from banks  
- Passage practice, highlights  
- Submit answers → scored result  
- History of attempts  

### Listening
- Catalog of listening tests + audio  
- Section practice / full mock style attempts  
- Submit answers → score  
- History  

### Writing
- Task questions + full mock tests  
- Autosave drafts  
- Submit for AI estimated band / teacher review  
- Teacher override / feedback  
- Admin question bank tools (writing admin API)  

### Speaking
- Stored / fresh tests  
- Mic recording  
- Whisper transcription  
- AI evaluation (fluency, lexical, grammar, pronunciation style feedback)  
- Session history + progress  

**Important:** Practice bands are **estimates**, not official IELTS scores.

---

## 7. Cross-cutting features

| Feature | Description |
| --- | --- |
| JWT Auth | Login, `/auth/me`, change password, role guards |
| Org API | Users, branches, batches CRUD with branch isolation |
| Homework API | Teacher assign / student start & submit / review |
| Notes API | Teacher upload + access control; student page images |
| Mocks API | Assign, attempt, heartbeat, submit, review, LiveKit token |
| Notifications | Student notification bell |
| Hydrate org | After login, pulls backend org data into UI store |
| Security | Rate-limited login, hashed passwords, AUTH_LEGACY_HEADERS off in prod, speaking ownership checks |
| CORS | Production locked to `fti4iltes.tech` |

---

## 8. Main API map

| Prefix | Purpose |
| --- | --- |
| `/api/auth/*` | Login, me, change-password |
| `/api/org/*` | Users, branches, batches |
| `/api/health` | Health check |
| `/api/tests/*`, `/api/answers/*`, `/api/history/*`, `/api/progress` | Speaking |
| `/api/writing/*`, `/api/writing/admin/*` | Writing |
| `/api/reading/*` | Reading |
| `/api/listening/*` | Listening |
| `/api/homework/*` | Homework |
| `/api/notes/*` | Notes |
| `/api/mocks/*` | Mock exams + live |
| Socket.IO | Mock realtime events |

---

## 9. Repo structure (high level)

```text
frontend/          React SPA (student / teacher / admin / branch-admin UIs)
backend/           FastAPI app (auth, org, 4 skills, homework, notes, mocks)
data/              Practice banks, media (large packs usually not in git)
scripts/           Local start helpers (start-all.ps1)
DEPLOYMENT.md      HestiaCP production guide
STAGING.md         Vercel + Render staging guide
render.yaml        Render backend blueprint
frontend/vercel.json  Vercel SPA rewrites
```

### Frontend key areas
- `src/App.tsx` — routes, login, shells  
- `src/admin/` — Super Admin + Branch Admin workspace  
- `src/teacher/` — homework, notes, mocks, students  
- `src/student/` — dashboard, homework, notes, mocks, practice  
- `src/practice/` — Reading / Listening / Writing / Speaking rooms  
- `src/lib/*-api.ts` — API clients + auth  

### Backend key areas
- `app/auth/` — users, branches, JWT, bootstrap  
- `app/speaking|reading|listening|writing/` — skill modules  
- `app/homework/`, `app/notes/`, `app/mocks/` — LMS features  
- `app/security_hardening.py` — rate limits / production gates  

---

## 10. Typical user journeys

1. **Super Admin** branch banata hai + branch admin credentials set karta hai  
2. **Branch Admin** login → teachers, students, batches add karta hai  
3. **Teacher** homework / notes / mock assign karta hai  
4. **Student** practice karta hai, homework submit karta hai, mock attempt karta hai  
5. **Teacher** review / band publish karta hai  

---

## 11. What this is NOT

- Official British Council / IDP IELTS exam platform nahi  
- AI band = **estimate only**  
- Free public consumer app nahi — FTI campus LMS hai  

---

## 12. Docs index

| File | Content |
| --- | --- |
| `README.md` | Quick start |
| `DEPLOYMENT.md` | Production VPS (Hestia) |
| `STAGING.md` | Cloud staging (Vercel/Render) |
| `PROJECT_REPORT.md` | Ye report — product + features |

---

*End of report.*
