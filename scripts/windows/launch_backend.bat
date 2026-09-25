@echo off
rem Windows launcher for the causal-platform backend (cmd).
rem Paths are resolved from this script's location; the interpreter is the
rem backend venv's if present, else python on PATH.
set "BACKEND=%~dp0..\..\backend"
set "PYTHONPATH=%BACKEND%"
set ENV=local
set DEV_AUTH_BYPASS=true
set CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
cd /d "%BACKEND%"
set "PY=%BACKEND%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
start /b "" "%PY%" -m uvicorn src.api.main:app --host 127.0.0.1 --port 49234
