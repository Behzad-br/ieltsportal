# One stack: FastAPI backend (:5000) + Vite frontend (:5173)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")

$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  $py = "python"
  Write-Host "WARNING: backend\.venv not found — using system python. Prefer: cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements-dev.txt"
}

Write-Host "Starting FastAPI backend on http://127.0.0.1:5000"
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "Set-Location '$root\backend'; `$env:PYTHONPATH='.'; & '$py' -m uvicorn app.main:asgi_app --reload --host 127.0.0.1 --port 5000"
)

Write-Host "Starting frontend on http://localhost:5173"
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "Open:  http://localhost:5173"
Write-Host "API:   http://127.0.0.1:5000/api/health"
Write-Host "Proxy: Vite /api → http://127.0.0.1:5000"
