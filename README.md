# FTI IELTS Learning Management System

```
frontend/     React + Vite SPA
backend/      FastAPI + SQLAlchemy + Socket.IO
data/         Practice banks, media, local SQLite (runtime/seed — not source code)
scripts/      Local helper scripts
DEPLOYMENT.md HestiaCP / Ubuntu production guide
```

Practice scores are estimates, not official IELTS results.

Full product overview (roles + features): **[PROJECT_REPORT.md](./PROJECT_REPORT.md)**

## Local development

**Required local topology**

```
Frontend  http://localhost:5173
    ↓  Vite /api proxy
Backend   http://127.0.0.1:5000
    ↓
Database  SQLite (default) or test PostgreSQL / MySQL via DATABASE_URL
```

This repo does **not** use Supabase. Persistence is SQLAlchemy → SQLite (local default), MySQL (production), or PostgreSQL when `DATABASE_URL` is set.

```powershell
.\scripts\start-all.ps1
```

- App: http://localhost:5173  
- API: http://127.0.0.1:5000/api/health  

First-time setup:

```powershell
cd frontend
npm install

cd ..\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# Prefer full install when disk allows (includes Whisper/torch):
#   pip install -r requirements-dev.txt
# Core API only (no Whisper) if disk is limited:
pip install -r requirements-core.txt
```

Copy `.env.example` → `.env` at the repo root (or `backend/.env`) and set API keys. Never commit real secrets.

Verify foundation (DB connect + read + write):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "."
python -m app.cli.check_foundation
```

## Production (HestiaCP)

See **[DEPLOYMENT.md](./DEPLOYMENT.md)** for Ubuntu VPS paths, MySQL, PM2, and Nginx.

- Frontend: https://fti4iltes.tech  
- API: https://api.fti4iltes.tech  

## Cloud staging (Vercel + Render)

For GitHub-based live testing (not production VPS), see **[STAGING.md](./STAGING.md)**.

- Frontend → Vercel (`frontend/`)
- Backend → Render (`render.yaml`)

## Notes

- Canonical Python deps: `backend/requirements.txt`  
- Backend PM2: `backend/ecosystem.config.cjs` (supervises Uvicorn, not a Node app)  
- `backend/package.json` is script wrappers only (`migrate` / `start`)
