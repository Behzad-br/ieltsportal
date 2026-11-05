# Cloud staging (GitHub → Vercel + Render)

Use this for **live staging / hosting**. Production VPS stays as documented in `DEPLOYMENT.md`.

## 1. Push to GitHub

```powershell
git push -u origin main
```

Repo: `https://github.com/Behzad-br/ieltsportal.git`

## 2. Backend on Render

1. Open [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect your GitHub repository `Behzad-br/ieltsportal`
3. Confirm `render.yaml` is detected
4. Set these **secret** environment variables in the Render UI:

| Key | Value | Notes |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://neondb_owner:***@...` | Your Neon PostgreSQL connection string |
| `OPENAI_API_KEY` | `sk-proj-...` | For AI evaluation and question generation |
| `AUTH_BOOTSTRAP_PASSWORD` | `Admin@12345` | Strong password for first Super Admin |
| `FRONTEND_URL` | `https://your-app.vercel.app` | Vercel domain (set after deploying frontend) |
| `CORS_ORIGINS` | `https://your-app.vercel.app` | Vercel domain (set after deploying frontend) |

5. Click **Apply** to deploy → Copy your backend service URL (e.g., `https://fti-ielts-api.onrender.com`)

Health check URL: `https://<YOUR-RENDER-APP>.onrender.com/api/health`

**Notes:**
- Render's free tier spins down after inactivity; the first request after idle takes ~30-50 seconds to wake up.
- Using Neon PostgreSQL ensures all student data, branches, and tests persist safely across restarts.

## 3. Frontend on Vercel

1. Open [Vercel Dashboard](https://vercel.com) → **Add New Project** → Import `Behzad-br/ieltsportal`
2. **Root Directory:** Click Edit and select `frontend`
3. **Framework Preset:** Vite (automatically detected)
4. Add Environment Variable:

| Key | Value |
| --- | --- |
| `VITE_API_URL` | Your Render backend URL with **no trailing slash** (e.g., `https://fti-ielts-api.onrender.com`) |

5. Click **Deploy**

`frontend/vercel.json` already contains single-page application (SPA) rewrites for routing.

## 4. Link CORS between Vercel & Render

Once your Vercel deployment completes and you have your live URL (e.g. `https://ieltsportal.vercel.app`):
1. In Render Dashboard → Environment variables:
   - `FRONTEND_URL` = `https://ieltsportal.vercel.app`
   - `CORS_ORIGINS` = `https://ieltsportal.vercel.app`
2. Click **Save Changes** (Render will redeploy with new CORS settings).

## 5. First Login

Super Admin credentials:
- URL: `https://<YOUR-VERCEL-APP>.vercel.app/super-admin-login`
- Username: `admin`
- Password: The `AUTH_BOOTSTRAP_PASSWORD` you configured on Render
