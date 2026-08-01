# API App

This is a deployment facade for the FastAPI backend.

Current canonical import:

```text
pairs_trading.backend.app:app
```

Stable future-facing import:

```text
apps.api.main:app
```

Run either form locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000
```

```powershell
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --host 127.0.0.1 --port 8000
```
