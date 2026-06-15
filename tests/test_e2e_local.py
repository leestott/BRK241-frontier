import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def mock_d365(monkeypatch):
    port = _free_port()
    env = os.environ.copy()
    env["D365_MOCK_PORT"] = str(port)
    env["D365_MOCK_BASE_URL"] = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("D365_MOCK_PORT", str(port))
    monkeypatch.setenv("D365_MOCK_BASE_URL", f"http://127.0.0.1:{port}")
    # Clear cached settings
    from fibreops import config
    config.get_settings.cache_clear()
    proc = subprocess.Popen(
        [sys.executable, "-m", "fibreops.mocks.d365_service"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    base = f"http://127.0.0.1:{port}"
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.3)
    if not ready:
        proc.terminate()
        pytest.skip("mock D365 did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=4)
    except Exception:
        proc.kill()


def test_full_local_flow(tmp_path, monkeypatch, mock_d365):
    monkeypatch.chdir(tmp_path)
    # Force local agents (no Foundry) and skip Teams real call
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("FIBREOPS_AUTO_DISPATCH", "true")
    from fibreops import config
    config.get_settings.cache_clear()

    from fibreops.orchestrator import handle_signal
    from fibreops.telemetry import generate_signals

    sigs = generate_signals(count=2, seed=11, include_critical=True)
    results = []
    for s in sigs:
        results.append(asyncio.run(handle_signal(s)))

    # Every run produced an analysis + coord step
    for r in results:
        agents = {step["agent"] for step in r["steps"]}
        assert "IncidentAnalysisAgent" in agents
        assert "NetOpsCoordinatorAgent" in agents

    # The critical loss-of-light run should have a dispatch step
    crit = next(r for r in results if r["signal"]["severity"] == "critical")
    assert any(step["agent"] == "FieldDispatchAgent" for step in crit["steps"])

    # D365 has at least one incident
    listing = httpx.get(f"{mock_d365}/api/data/v9.2/incidents", timeout=5).json()
    assert len(listing["value"]) >= 1

    # Optimiser produces a score
    from fibreops.optimiser import run_optimisation
    summary = run_optimisation()
    assert summary["runs"] == len(results)
    assert 0.0 <= summary["avg_score"] <= 1.0
