# Worker App

This is the future home for durable background workers.

The current backend still runs jobs with in-process thread pools, but job metadata is now mirrored into SQLite so a future worker can pick up the same contracts.

Inspect local metadata:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py
```

List paper jobs:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --kind paper
```

List backtest jobs:

```powershell
.\.venv\Scripts\python.exe apps\worker\main.py --kind backtest
```
