"""Direct in-process tests for the mock D365 Field Service FastAPI app.

Uses ``fastapi.testclient.TestClient`` so we don't need a subprocess. The
service writes to ``state/d365_store.json``; we monkeypatch ``STORE_PATH``
to a tmp file so tests are isolated.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fibreops.mocks import d365_service


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    store = tmp_path / "d365_store.json"
    monkeypatch.setattr(d365_service, "STORE_PATH", store)
    return TestClient(d365_service.app)


def _new_ticket(client: TestClient, **overrides):
    payload = {
        "incident_id": "INC-AAA",
        "node_id": "FN-LDN-001",
        "severity": "high",
        "title": "[HIGH] LoS on FN-LDN-001",
        "description": "fibre cut",
    }
    payload.update(overrides)
    return client.post("/api/data/v9.2/incidents", json=payload)


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "mock-d365"


def test_create_and_get_incident(client):
    created = _new_ticket(client).json()
    assert created["ticket_id"].startswith(("TKT-", "INC-"))
    assert created["status"] == "new"
    assert created["events"][0]["event"] == "created"
    fetched = client.get(f"/api/data/v9.2/incidents/{created['ticket_id']}").json()
    assert fetched["ticket_id"] == created["ticket_id"]


def test_create_persists_incident_id_when_provided(client):
    r = _new_ticket(client, incident_id="INC-EXPLICIT").json()
    assert r["ticket_id"] == "INC-EXPLICIT"
    assert r["incident_id"] == "INC-EXPLICIT"


def test_list_incidents_returns_value_array(client):
    _new_ticket(client, incident_id="INC-1")
    _new_ticket(client, incident_id="INC-2")
    payload = client.get("/api/data/v9.2/incidents").json()
    assert "value" in payload
    ids = {row["ticket_id"] for row in payload["value"]}
    assert {"INC-1", "INC-2"}.issubset(ids)


def test_patch_updates_status_and_assignee(client):
    created = _new_ticket(client, incident_id="INC-P").json()
    patched = client.patch(
        f"/api/data/v9.2/incidents/{created['ticket_id']}",
        json={"status": "in_progress", "assignee": "Engineer Q"},
    ).json()
    assert patched["status"] == "in_progress"
    assert patched["assignee"] == "Engineer Q"
    assert patched["events"][-1]["event"] == "updated"


def test_get_404_for_missing_incident(client):
    assert client.get("/api/data/v9.2/incidents/nope").status_code == 404


def test_patch_404_for_missing_incident(client):
    r = client.patch("/api/data/v9.2/incidents/nope", json={"status": "x"})
    assert r.status_code == 404


def test_booking_marks_incident_assigned(client):
    created = _new_ticket(client, incident_id="INC-B").json()
    booking = client.post(
        "/api/data/v9.2/bookableresourcebookings",
        json={
            "incident_id": created["ticket_id"],
            "engineer_id": "ENG-1",
            "engineer_name": "Test Engineer",
            "eta_minutes": 22,
            "notes": "auto",
        },
    ).json()
    assert booking["booking_id"].startswith("BK-")
    assert booking["status"] == "scheduled"
    # Side effect: the incident is now assigned to the engineer.
    after = client.get(f"/api/data/v9.2/incidents/{created['ticket_id']}").json()
    assert after["status"] == "assigned"
    assert after["assignee"] == "Test Engineer"
    assert after["events"][-1]["event"] == "engineer_dispatched"


def test_booking_for_unknown_incident_still_records_booking(client):
    booking = client.post(
        "/api/data/v9.2/bookableresourcebookings",
        json={
            "incident_id": "INC-DOES-NOT-EXIST",
            "engineer_id": "ENG-2",
            "engineer_name": "Other Engineer",
            "eta_minutes": 30,
        },
    ).json()
    # Booking is still persisted even when the incident is unknown — by design,
    # the mock prioritises tolerance over strict referential integrity so a
    # demo doesn't get stuck on a missing-link error.
    assert booking["booking_id"].startswith("BK-")
