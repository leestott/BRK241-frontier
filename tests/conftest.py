"""Shared pytest fixtures.

All tests run with ``FIBREOPS_AGENT_BACKEND=local`` and Azure-required env vars
unset, so the suite is hermetic — no Azure credentials, no network calls beyond
in-process FastAPI ``TestClient`` and an optional spawned mock-D365 subprocess
in :mod:`tests.test_e2e_local`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


_AZURE_ENVS = (
    "AZURE_AI_PROJECT_ENDPOINT",
    "AZURE_AI_PROJECT_CONNECTION_STRING",
    "EVENT_HUB_FQDN",
    "TEAMS_WEBHOOK_URL",
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every Azure / webhook env var before each test."""
    for key in _AZURE_ENVS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FIBREOPS_AGENT_BACKEND", "local")
    # Settings is lru_cached; flush so the new env wins.
    from fibreops import config

    config.get_settings.cache_clear()


@pytest.fixture
def chdir_state_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into a fresh tmp_path so all ``state/*`` writes are sandboxed."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "state").mkdir(exist_ok=True)
    return tmp_path
