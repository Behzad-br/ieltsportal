# HestiaCP Ubuntu VPS — FTI IELTS API (FastAPI)

Do **not** change `fti4success.com`.

| Host | Role |
| --- | --- |
| `fti4iltes.tech` | Frontend SPA |
| `www.fti4iltes.tech` | Frontend |
| `api.fti4iltes.tech` | FastAPI + Socket.IO → `127.0.0.1:5000` |

Server IP: `187.127.214.96`

## What this backend is

- **Language:** Python 3.10+ (recommend **3.10 or 3.11**)
- **Framework:** FastAPI + SQLAlchemy
- **ASGI server:** **Uvicorn** (required — serves FastAPI **and** Socket.IO)
- **Process manager:** **PM2** supervises Python/uvicorn (not a Node app)
- **Entry point:** `app.main:asgi_app`
- **`backend/package.json`:** optional npm script aliases only (`migrate` / `start`) — **no Node dependencies**
- **`backend/ecosystem.config.cjs`:** correct PM2 config for uvicorn
- **Canonical deps:** `backend/requirements.txt` only (no root `requirements.txt`)
- **Authoritative deploy doc:** this file (`DEPLOYMENT.md`) — obsolete Render/Vercel guides removed

Gunicorn is **optional**. If used, must be:
`gunicorn -k uvicorn.workers.UvicornWorker -w 1 …`
Keep **1 worker** because Socket.IO rooms are in-memory.

Local Windows helper: `scripts/start-all.ps1`

---

## Recommended VPS directory

Upload the contents of the repo `backend/` folder to:

```text
/home/fti4success_admin/apps/itles-backend/
├── app/
├── tests/                  # optional on VPS
├── data/                   # create; put bank JSON + uploads here
├── .venv/                  # created on server
├── .env                    # created on server (secrets)
├── .env.example
├── ecosystem.config.cjs
├── package.json            # scripts only
├── requirements.txt
├── requirements-dev.txt    # not needed on VPS
└── README.md
```

Also copy practice bank JSON from the monorepo `data/` folder into `…/itles-backend/data/`
(`question_bank.json`, `ielts_writing_starter_bank_150.json`, `reading-practice-bank-v3.json`, `listening-database.json`, and any packs you need).

---

## 1. DNS

| Type | Name | Value |
| --- | --- | --- |
| A | `fti4iltes.tech` | `187.127.214.96` |
| A | `api.fti4iltes.tech` | `187.127.214.96` |
| CNAME | `www` | `fti4iltes.tech` |

Then Let's Encrypt in Hestia for both domains.

---

## 2. MariaDB

- DB: `fti4success_admin_itles`
- User: `fti4success_admin_itles_user`
- Host: `localhost`

---

## 3. System packages (Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg
# PM2 (needs Node only as a supervisor host — not for the API itself)
# sudo npm install -g pm2
```

---

## 4. Upload + .env

```bash
mkdir -p /home/fti4success_admin/apps/itles-backend
# upload backend/ contents into that path, then:
cd /home/fti4success_admin/apps/itles-backend
cp .env.example .env
nano .env
```

Set at least:

```env
ENVIRONMENT=production
ENV=production
AUTO_CREATE_SCHEMA=false
REQUIRE_MIGRATIONS_AT_HEAD=true
PORT=5000
DB_HOST=localhost
DB_PORT=3306
DB_NAME=fti4success_admin_itles
DB_USER=fti4success_admin_itles_user
DB_PASSWORD=           # real password — never commit
DATA_DIR=/home/fti4success_admin/apps/itles-backend/data
# Speaking/Notes media MUST survive deploys (same tree under DATA_DIR or absolute mounts):
# SPEAKING_AUDIO_DIR=/home/fti4success_admin/apps/itles-backend/data/speaking_audio
# NOTES_DIR=/home/fti4success_admin/apps/itles-backend/data/notes
FRONTEND_URL=https://fti4iltes.tech
CORS_ORIGINS=https://fti4iltes.tech,https://www.fti4iltes.tech
JWT_SECRET=            # long random string — NOT change-me…
AUTH_BOOTSTRAP_PASSWORD=
AUTH_LEGACY_HEADERS=false
ALLOW_WRITING_ADMIN_TOKEN=false
OPENAI_API_KEY=
WHISPER_MODEL=base
```

OS packages already installed above must include **`ffmpeg`** (Speaking). Confirm with `ffmpeg -version` and `/api/health` fields `ffmpeg_available` / `speaking_deps_ready`.

---

## 5. Install, migrate, start (exact)

```bash
cd /home/fti4success_admin/apps/itles-backend

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Schema: Alembic owns staging/production (head 20261012_0007)
alembic upgrade head
alembic current   # must print 20261012_0007

# Optional readiness gate (never prints secrets)
python scripts/check_production_ready.py

# Production process (preferred)
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup   # once, follow printed systemd command if asked
```

**Verified production start command (without PM2):**

```bash
cd /home/fti4success_admin/apps/itles-backend
source .venv/bin/activate
python -m uvicorn app.main:asgi_app --host 127.0.0.1 --port 5000 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Health:

```bash
curl -sS http://127.0.0.1:5000/api/health
# expect: "status":"ok","database":true
# also check: ffmpeg_available, whisper_importable, speaking_deps_ready, media_dirs_writable
```

### Updates later

```bash
cd /home/fti4success_admin/apps/itles-backend
source .venv/bin/activate
# upload new code, then:
pip install -r requirements.txt
alembic upgrade head
alembic current
pm2 restart itles-api
pm2 save
```

**Do not** use `AUTO_CREATE_SCHEMA=true` or legacy `create_all` on production. Prefer `alembic upgrade head`. (`python -m app.cli.migrate --alembic` is an acceptable wrapper.)

---

## 6. Nginx for `api.fti4iltes.tech`

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    client_max_body_size 50M;
}
```

Frontend SPA (`fti4iltes.tech` → `public_html`) needs:

```nginx
try_files $uri $uri/ /index.html;
```

---

## 7. Frontend build reminder

```bash
# on build machine or VPS
cd frontend
cp .env.production.example .env.production
# VITE_API_URL=https://api.fti4iltes.tech
npm install && npm run build
rsync -a --delete dist/ /home/fti4success_admin/web/fti4iltes.tech/public_html/
```

---

## 8. Backup / recovery (operator checklist)

Before any production cutover:

1. Record Alembic revision (`alembic current` → expect `20261012_0007`).
2. Take a DB recovery point:
   - **MariaDB VPS:** `mysqldump fti4success_admin_itles > itles-YYYY-MM-DD.sql`
   - **Neon staging/prod:** confirm PITR / branch restore owner and retention.
3. Copy persistent media: `/home/fti4success_admin/apps/itles-backend/data/` (Speaking + Notes).
4. Private encrypted copy of `.env`.

**Rollback:** redeploy previous frontend `dist/` and previous backend commit/image. Prefer **forward-fix or restore dump** for DB — do **not** blindly `alembic downgrade` unless the migration is known reversible. Media volumes must outlive app rollbacks.

---

## 9. Safe deployment order

1. Confirm recovery point (DB + media + `.env`)
2. Set production env (`ENVIRONMENT=production`, strong `JWT_SECRET`, `AUTH_LEGACY_HEADERS=false`, `AUTO_CREATE_SCHEMA=false`)
3. Ensure persistent `DATA_DIR` / Speaking / Notes dirs exist and are writable
4. `alembic upgrade head` → verify `alembic current`
5. Deploy backend → `curl /api/health`
6. `python scripts/check_production_ready.py`
7. Deploy frontend (`VITE_API_URL=https://api.fti4iltes.tech`, `VITE_DEMO_MODE` unset)
8. Auth smoke (all four roles) + one skill each + cross-branch denial
9. Monitor 5xx / login / media / AI / Whisper for 24h

---

## Do NOT upload to the VPS

- `.venv/` (create on server)
- `.pytest_cache/`, `__pycache__/`, `*.pyc`
- `.env` with secrets from your laptop (create fresh on VPS)
- `node_modules/`
- Local SQLite `*.db` unless you intentionally migrate data
- Monorepo `frontend/` into the API path (deploy frontend to `public_html` separately)
