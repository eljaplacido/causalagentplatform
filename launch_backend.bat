@echo off
set PYTHONPATH=C:\Users\35845\Desktop\DIGICISU\causalplatform\backend
set ENV=local
set DEV_AUTH_BYPASS=true
set HALLUCINATION_RISK_THRESHOLD=0.7
set MAX_AGENT_CYCLES=5
set CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
cd /d C:\Users\35845\Desktop\DIGICISU\causalplatform\backend
start /b "" "C:\Users\35845\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn src.api.main:app --host 127.0.0.1 --port 49234
