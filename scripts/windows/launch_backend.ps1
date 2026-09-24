$env:PYTHONPATH = "C:\Users\35845\Desktop\DIGICISU\causalplatform\backend"
$env:ENV = "local"
$env:DEV_AUTH_BYPASS = "true"
$env:HALLUCINATION_RISK_THRESHOLD = "0.7"
$env:MAX_AGENT_CYCLES = "5"
$env:CORS_ALLOWED_ORIGINS = "http://localhost:3000,http://localhost:5173"
Set-Location "C:\Users\35845\Desktop\DIGICISU\causalplatform\backend"
& "C:\Users\35845\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn src.api.main:app --host 127.0.0.1 --port 8002
