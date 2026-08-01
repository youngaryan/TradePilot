"""API application facade for future deployment layouts.

The canonical FastAPI app still lives in `pairs_trading.backend.app`. This
module gives deployment tools a stable `apps.api.main:app` import path without
moving the backend package yet.
"""

from pairs_trading.backend.app import app

__all__ = ["app"]
