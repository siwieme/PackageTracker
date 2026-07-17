"""FastAPI web server for PackageTracker.

Serves the browser UI and exposes a JSON tracking endpoint.
The underlying adapters and data model are unchanged.

Run:
    python server.py
    uvicorn server:app --reload   (development)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from adapters.base import CourierError
from core.models import PackageStatus
from main import track as _track

app = FastAPI(title="PackageTracker API", version="1.0.0", docs_url="/api/docs")

_HTML = Path(__file__).parent / "templates" / "index.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    return _HTML.read_text(encoding="utf-8")


@app.get("/api/track", summary="Track a parcel")
async def track_endpoint(
    tracking_number: str,
    postal_code: Optional[str] = None,
    courier: Optional[str] = None,
) -> dict:
    """Detect the courier and return normalised tracking data.

    Args:
        tracking_number: The parcel identifier.
        postal_code: Destination postal code (required by bpost for most parcels).

    Returns:
        A :class:`~core.models.PackageStatus` serialised as JSON,
        or ``{"error": "..."}`` when the lookup fails.
    """
    try:
        status: PackageStatus = await _track(
            tracking_number, postal_code=postal_code, courier=courier
        )
        return status.model_dump(mode="json")
    except (ValueError, CourierError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Onverwachte fout: {exc}"}


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=True)
