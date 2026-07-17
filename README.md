# PackageTracker

[![CI](https://github.com/siwieme/PackageTracker/actions/workflows/ci.yml/badge.svg)](https://github.com/siwieme/PackageTracker/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)

A lightweight, modular Python API for tracking parcels across multiple couriers. Raw courier responses are normalized into a single strict data model, with specific facility names (e.g. "LOKEREN X", "AMSTERDAM DC") extracted and preserved.

## Supported couriers

| Courier | Tracking number formats |
|---------|------------------------|
| bpost   | 24 digits (`3232…`), S10 Belgium (`CD123456789BE`) |
| PostNL  | `3S…`, S10 Netherlands (`RR123456789NL`), `KG…` |
| DHL     | `JVGL…`, `JJD…`, 10-digit Express |

## Tech stack

- **Python 3.11+**
- [`httpx`](https://www.python-httpx.org/) — async HTTP
- [`pydantic`](https://docs.pydantic.dev/) — data validation and serialization
- [`fastapi`](https://fastapi.tiangolo.com/) + [`uvicorn`](https://www.uvicorn.org/) — web server for the browser UI
- [`customtkinter`](https://github.com/TomSchimansky/CustomTkinter) — cross-platform desktop GUI
- [`pytest`](https://pytest.org/) + [`respx`](https://lundberg.github.io/respx/) — testing with mocked HTTP

## Project structure

```
PackageTracker/
├── core/
│   ├── models.py       # Pydantic schemas (TrackingEvent, PackageStatus)
│   └── router.py       # Regex-based courier detection
├── adapters/
│   ├── base.py         # Abstract base class + shared helpers
│   ├── bpost.py
│   ├── postnl.py
│   └── dhl.py
├── tests/
│   ├── test_bpost.py
│   ├── test_postnl.py
│   ├── test_dhl.py
│   └── test_router.py
├── main.py             # CLI entry point / library API
├── server.py           # FastAPI web server (browser UI)
├── ui.py               # Desktop GUI entry point
├── templates/
│   └── index.html      # Single-file SPA (HTML + CSS + JS)
├── requirements.txt
├── requirements-dev.txt
└── Dockerfile
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes test deps
```

## Usage

### Browser UI

```bash
python server.py
```

Opens at **http://127.0.0.1:8000**. Enter a tracking number and optionally a postal code, then click **Zoeken**. Results appear as a hero status card with a full event timeline. Supports light/dark mode (follows system preference, with a manual override).

For development with auto-reload:

```bash
uvicorn server:app --reload
```

The REST endpoint is also available directly:

```
GET /api/track?tracking_number=323204736100000008192030&postal_code=9160
```

Interactive API docs are served at `/api/docs`.

### Desktop UI

```bash
python ui.py
```

A cross-platform window opens (Windows / Linux / macOS). Enter a tracking number and optionally a postal code, then click **Opzoeken**. Results appear in a scrollable list with colour-coded statuses. Use the toggle button to switch between dark and light mode.

### CLI

```bash
# Basic lookup — courier detected automatically
python -m main 323212345678901234567890

# With postal code (required by bpost for registered mail)
python -m main 323212345678901234567890 --postal-code 9000

# Multiple parcels at once
python -m main 323212345678901234567890 3SABC123456789 -p 1000
```

Output is newline-separated JSON per parcel:

```json
{
  "tracking_number": "323212345678901234567890",
  "courier": "bpost",
  "is_delivered": true,
  "latest_event": {
    "timestamp": "2026-07-16T14:32:00",
    "status_code": "DELIVERED",
    "description": "Delivered",
    "location": "BRUSSEL 1"
  },
  "history": [...]
}
```

### As a library

```python
import asyncio
from main import track

# Without postal code
status = asyncio.run(track("323212345678901234567890"))

# With postal code
status = asyncio.run(track("323212345678901234567890", postal_code="9000"))
print(status.model_dump_json(indent=2))
```

For DHL with an API key:

```python
from adapters.dhl import DHLAdapter

async with DHLAdapter(api_key="your-key") as adapter:
    status = await adapter.fetch_tracking("JVGL0123456789", postal_code="1000")
```

## Data model

```python
class TrackingEvent(BaseModel):
    timestamp: datetime
    status_code: StatusCode   # TRANSIT | DELIVERED | EXCEPTION
    description: str          # Original courier description
    location: Optional[str]   # Facility name, e.g. "LOKEREN X"

class PackageStatus(BaseModel):
    tracking_number: str
    courier: str
    is_delivered: bool
    latest_event: TrackingEvent
    history: List[TrackingEvent]   # Chronological, oldest first
```

## Running tests

```bash
pytest          # all 50 tests
pytest -v       # verbose
pytest tests/test_bpost.py -v   # single file
```

All tests mock outbound HTTP with `respx` — no real courier calls are made.

## Docker

Build and run for Oracle Cloud (OCI):

```bash
docker build -t packagetracker .
docker run --rm packagetracker python -m main 323212345678901234567890
```

The image uses `python:3.11-slim` and runs as a non-root user (`appuser`, uid 10001).
