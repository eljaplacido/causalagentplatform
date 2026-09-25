# Windows launcher for the causal-platform backend (PowerShell).
# Paths are resolved from this script's location; the interpreter is the
# backend venv's if present, else `python` on PATH.
$Backend = Resolve-Path (Join-Path $PSScriptRoot "..\..\backend")
$env:PYTHONPATH = "$Backend"
$env:ENV = "local"
$env:DEV_AUTH_BYPASS = "true"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://localhost:5173"
Set-Location $Backend
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = "python" }
& $Py -m uvicorn src.api.main:app --host 127.0.0.1 --port 49234
