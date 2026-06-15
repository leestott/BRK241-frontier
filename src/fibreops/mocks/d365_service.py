"""Mock D365 Field Service.

Implements a small, Dataverse-shaped REST surface so the ticketing tool
(production-shaped) can talk to it without code changes when you later
swap the base URL to a real Dataverse environment.

Endpoints:
  POST /api/data/v9.2/incidents               -> create_ticket
  GET  /api/data/v9.2/incidents/{id}          -> get_ticket
  PATCH /api/data/v9.2/incidents/{id}         -> update_ticket
  POST /api/data/v9.2/bookableresourcebookings -> dispatch
  GET  /api/data/v9.2/incidents               -> list

Run as a child process via `python -m fibreops.mocks.d365_service`.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
STORE_PATH = STATE_DIR / "d365_store.json"
_LOCK = threading.Lock()


def _load() -> dict[str, Any]:
    if STORE_PATH.exists():
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    return {"incidents": {}, "bookings": {}}


def _save(state: dict[str, Any]) -> None:
    STORE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


class IncidentIn(BaseModel):
    title: str
    description: str
    severity: str
    node_id: str
    incident_id: str | None = None


class IncidentPatch(BaseModel):
    status: str | None = None
    assignee: str | None = None
    notes: str | None = None


class BookingIn(BaseModel):
    incident_id: str
    engineer_id: str
    engineer_name: str
    eta_minutes: int
    notes: str = ""


app = FastAPI(title="Mock D365 Field Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-d365", "ts": datetime.now(timezone.utc).isoformat()}


@app.post("/api/data/v9.2/incidents")
def create_incident(payload: IncidentIn) -> dict[str, Any]:
    with _LOCK:
        state = _load()
        ticket_id = payload.incident_id or f"TKT-{uuid.uuid4().hex[:8].upper()}"
        record = {
            "ticket_id": ticket_id,
            "incident_id": payload.incident_id or ticket_id,
            "title": payload.title,
            "description": payload.description,
            "severity": payload.severity,
            "node_id": payload.node_id,
            "status": "new",
            "assignee": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "events": [{"ts": datetime.now(timezone.utc).isoformat(), "event": "created"}],
        }
        state["incidents"][ticket_id] = record
        _save(state)
        return record


@app.get("/api/data/v9.2/incidents/{ticket_id}")
def get_incident(ticket_id: str) -> dict[str, Any]:
    state = _load()
    rec = state["incidents"].get(ticket_id)
    if not rec:
        raise HTTPException(status_code=404, detail="not found")
    return rec


@app.get("/api/data/v9.2/incidents")
def list_incidents() -> dict[str, Any]:
    state = _load()
    return {"value": list(state["incidents"].values())}


@app.patch("/api/data/v9.2/incidents/{ticket_id}")
def patch_incident(ticket_id: str, patch: IncidentPatch) -> dict[str, Any]:
    with _LOCK:
        state = _load()
        rec = state["incidents"].get(ticket_id)
        if not rec:
            raise HTTPException(status_code=404, detail="not found")
        for field in ("status", "assignee"):
            value = getattr(patch, field)
            if value is not None:
                rec[field] = value
        rec["events"].append(
            {"ts": datetime.now(timezone.utc).isoformat(), "event": "updated", "patch": patch.model_dump(exclude_none=True)}
        )
        _save(state)
        return rec


@app.post("/api/data/v9.2/bookableresourcebookings")
def create_booking(payload: BookingIn) -> dict[str, Any]:
    with _LOCK:
        state = _load()
        booking_id = f"BK-{uuid.uuid4().hex[:6].upper()}"
        record = payload.model_dump()
        record.update(
            {
                "booking_id": booking_id,
                "status": "scheduled",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        state["bookings"][booking_id] = record
        incident = state["incidents"].get(payload.incident_id)
        if incident is not None:
            incident["status"] = "assigned"
            incident["assignee"] = payload.engineer_name
            incident["events"].append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "engineer_dispatched",
                    "engineer_id": payload.engineer_id,
                    "eta_minutes": payload.eta_minutes,
                }
            )
        _save(state)
        return record


def run() -> None:
    from ..config import get_settings

    settings = get_settings()
    uvicorn.run(app, host="127.0.0.1", port=settings.d365_mock_port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    run()
