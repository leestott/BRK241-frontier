"""Dashboard controls and incident-card markup."""
from __future__ import annotations

from fastapi.testclient import TestClient

from fibreops.ui.app import app
from tests.test_ui import _write_run


def test_dashboard_separates_actions_and_operations(chdir_state_tmp):
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert 'id="activity-status"' in response.text
    assert 'id="voice-status"' in response.text
    assert "Voice idle" in response.text
    assert 'class="operations-menu"' in response.text
    assert 'hx-confirm="Permanently clear demo incidents' in response.text
    assert '/static/dashboard.js?v=1' in response.text


def test_incident_cards_expose_selection_and_elapsed_time(chdir_state_tmp):
    _write_run(chdir_state_tmp / "state")
    response = TestClient(app).get("/partials/runs")
    assert response.status_code == 200
    assert 'data-run-id="run-aaaa1111"' in response.text
    assert 'aria-pressed="false"' in response.text
    assert 'datetime="2026-06-13T10:00:00+00:00"' in response.text
    assert 'title="LoS on FN-LDN-001"' in response.text
    assert 'class="card-summary"' in response.text


def test_dashboard_script_is_served(chdir_state_tmp):
    response = TestClient(app).get("/static/dashboard.js")
    assert response.status_code == 200
    assert "showVoiceError" in response.text
    assert "selectRun" in response.text
