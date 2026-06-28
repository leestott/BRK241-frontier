"""Foundry IQ knowledge tests (Web IQ + Work IQ).

The tools serve deterministic local fixtures when no endpoint is configured
and POST to the configured endpoint otherwise. The Incident Analysis agent
attaches grounded snippets to its analysis output, and the UI surfaces every
lookup in the Knowledge sources pane.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import fibreops.ui.app as ui_module
from fibreops.tools import knowledge as knowledge_module
from fibreops.tools import knowledge_base_search, web_iq_search, work_iq_search


def _iq_path(state: Path) -> Path:
    return state / "iq_lookups.jsonl"


def test_knowledge_base_search_falls_back_offline(chdir_state_tmp: Path) -> None:
    # No FOUNDRY_IQ_SEARCH_ENDPOINT/KB configured -> deterministic fixtures, and
    # the lookup is still recorded under the 'foundry_iq' source for the UI.
    from fibreops import config

    config.get_settings.cache_clear()
    assert config.get_settings().knowledge_base_enabled is False
    results = knowledge_base_search(query="high_attenuation FN-LDN-001 SOP remediation", limit=2)
    assert isinstance(results, list) and results
    record = json.loads(
        _iq_path(chdir_state_tmp / "state").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert record["source"] == "foundry_iq"


def test_knowledge_base_enabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from fibreops import config

    monkeypatch.setenv("FOUNDRY_IQ_SEARCH_ENDPOINT", "https://x.search.windows.net")
    monkeypatch.setenv("FOUNDRY_IQ_KNOWLEDGE_BASE", "fibreops-knowledge-base")
    config.get_settings.cache_clear()
    assert config.get_settings().knowledge_base_enabled is True


def test_web_iq_returns_local_fixtures_offline(chdir_state_tmp: Path) -> None:
    results = web_iq_search(query="London fibre outage roadworks", limit=2)
    assert isinstance(results, list)
    assert results, "expected at least one fixture hit"
    titles = " ".join(r["title"] for r in results).lower()
    assert "london" in titles or "shoreditch" in titles
    # outbox is appended for the UI
    assert _iq_path(chdir_state_tmp / "state").exists()
    line = _iq_path(chdir_state_tmp / "state").read_text(encoding="utf-8").splitlines()[-1]
    record = json.loads(line)
    assert record["source"] == "web_iq"
    assert record["query"].startswith("London")


def test_work_iq_returns_local_fixtures_offline(chdir_state_tmp: Path) -> None:
    results = work_iq_search(query="FN-LDN-001 SLA customers Shoreditch", limit=2)
    assert isinstance(results, list) and results
    titles = " ".join(r["title"] for r in results).lower()
    assert "fn-ldn-001" in titles or "sla" in titles
    # work_iq results include last_modified
    assert any("last_modified" in r for r in results)


def test_iq_search_clamps_limit(chdir_state_tmp: Path) -> None:
    assert len(web_iq_search(query="random irrelevant query", limit=99)) <= 10
    assert len(web_iq_search(query="random irrelevant query", limit=0)) >= 1


def test_iq_search_falls_back_when_no_keyword_matches(chdir_state_tmp: Path) -> None:
    # No keyword matches "lithium asteroid" — should still return *something*
    # so agents always have a grounding cushion.
    results = web_iq_search(query="lithium asteroid mining 2087", limit=2)
    assert results, "expected fallback fixtures"


def test_web_iq_uses_configured_endpoint(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOUNDRY_WEB_IQ_ENDPOINT", "https://iq.example.com/web/search")
    monkeypatch.setenv("FOUNDRY_WEB_IQ_API_KEY", "key-web")
    from fibreops import config

    config.get_settings.cache_clear()

    captured: dict[str, object] = {}

    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {"title": "Live web hit", "snippet": "x", "url": "u", "source": "s"}
                ]
            }

    class _FakeClient:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, url, json, headers):  # noqa: A002
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResp()

    monkeypatch.setattr(knowledge_module.httpx, "Client", _FakeClient)

    results = web_iq_search(query="test query", limit=4)
    assert captured["url"] == "https://iq.example.com/web/search"
    assert captured["headers"]["Ocp-Apim-Subscription-Key"] == "key-web"
    assert captured["json"] == {"query": "test query", "limit": 4}
    assert results == [
        {"title": "Live web hit", "snippet": "x", "url": "u", "source": "s"}
    ]


def test_work_iq_uses_configured_endpoint(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOUNDRY_WORK_IQ_ENDPOINT", "https://iq.example.com/work/search")
    monkeypatch.setenv("FOUNDRY_WORK_IQ_API_KEY", "key-work")
    from fibreops import config

    config.get_settings.cache_clear()

    class _FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> list:
            # The tool also accepts a bare list payload.
            return [{"title": "Live work hit", "snippet": "y", "source": "Fabric"}]

    class _FakeClient:
        def __init__(self, *_, **__):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def post(self, *_a, **_kw):
            return _FakeResp()

    monkeypatch.setattr(knowledge_module.httpx, "Client", _FakeClient)
    results = work_iq_search(query="anything", limit=1)
    assert results == [{"title": "Live work hit", "snippet": "y", "source": "Fabric"}]


def test_local_analysis_agent_grounds_with_iq(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The LocalAgent IncidentAnalysis attaches IQ grounding to its output."""
    import asyncio

    monkeypatch.setenv("FIBREOPS_FOUNDRY_IQ", "1")
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents.factory import build_incident_analysis_agent

    agent = build_incident_analysis_agent(prefer="local")
    prompt = json.dumps(
        {
            "signal_id": "SIG-IQ-1",
            "node_id": "FN-LDN-001",
            "site": "Shoreditch CO",
            "region": "London",
            "customers_served": 12400,
            "signal_type": "loss_of_light",
            "severity": "high",
            "measured_value": -40.0,
            "unit": "dBm",
            "raw": {"last_good_dbm": -22.5},
        }
    )
    resp = asyncio.run(agent.run(prompt))
    analysis = json.loads(resp.text)
    assert "knowledge" in analysis
    assert isinstance(analysis["knowledge"]["web_iq"], list)
    assert isinstance(analysis["knowledge"]["work_iq"], list)
    # Local fixtures should hit at least one item each for this prompt.
    assert analysis["knowledge"]["web_iq"]
    assert analysis["knowledge"]["work_iq"]
    # IQ persistence — every call appended a record.
    lines = _iq_path(chdir_state_tmp / "state").read_text(encoding="utf-8").splitlines()
    sources = {json.loads(l)["source"] for l in lines if l.strip()}
    assert {"web_iq", "work_iq"}.issubset(sources)


def test_local_analysis_skips_iq_when_disabled(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIBREOPS_FOUNDRY_IQ=0 disables grounding lookups entirely."""
    import asyncio

    monkeypatch.setenv("FIBREOPS_FOUNDRY_IQ", "0")
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.agents.factory import build_incident_analysis_agent

    agent = build_incident_analysis_agent(prefer="local")
    prompt = json.dumps(
        {
            "signal_id": "SIG-IQ-2",
            "node_id": "FN-BRS-002",
            "site": "Temple Quay",
            "region": "Bristol",
            "customers_served": 1200,
            "signal_type": "ber_degradation",
            "severity": "medium",
            "measured_value": 5.0e-7,
            "unit": "ratio",
            "raw": {},
        }
    )
    resp = asyncio.run(agent.run(prompt))
    analysis = json.loads(resp.text)
    assert analysis["knowledge"]["web_iq"] == []
    assert analysis["knowledge"]["work_iq"] == []


# --- UI -------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, chdir_state_tmp: Path) -> TestClient:
    monkeypatch.setenv("FIBREOPS_UI_SKIP_MOCK_D365", "1")
    return TestClient(ui_module.app)


def test_iq_partial_empty_state(client: TestClient) -> None:
    r = client.get("/partials/iq")
    assert r.status_code == 200
    assert "NO IQ LOOKUPS" in r.text


def test_iq_partial_renders_lookups(client: TestClient, chdir_state_tmp: Path) -> None:
    lookups = [
        {
            "ts": "2026-06-13T10:01:02+00:00",
            "source": "web_iq",
            "query": "London fibre outage",
            "result_count": 1,
            "results": [
                {
                    "title": "Roadworks alert: A10",
                    "snippet": "Lane closures near Shoreditch.",
                    "url": "https://example.com/a10",
                    "source": "tfl.gov.uk",
                }
            ],
        },
        {
            "ts": "2026-06-13T10:01:03+00:00",
            "source": "work_iq",
            "query": "FN-LDN-001 SLA",
            "result_count": 1,
            "results": [
                {
                    "title": "FN-LDN-001 — site survey",
                    "snippet": "Critical metro hub.",
                    "source": "Fabric / Topology",
                    "url": "fabric://...",
                    "last_modified": "2026-03-14",
                }
            ],
        },
    ]
    (chdir_state_tmp / "state" / "iq_lookups.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lookups) + "\n", encoding="utf-8"
    )
    r = client.get("/partials/iq")
    assert r.status_code == 200
    assert "Web IQ" in r.text
    assert "Work IQ" in r.text
    assert "Roadworks alert" in r.text
    assert "FN-LDN-001" in r.text


def test_action_reset_clears_iq_lookups(client: TestClient, chdir_state_tmp: Path) -> None:
    iq = chdir_state_tmp / "state" / "iq_lookups.jsonl"
    iq.write_text(json.dumps({"x": 1}) + "\n", encoding="utf-8")
    assert iq.exists()
    r = client.post("/actions/reset")
    assert r.status_code == 200
    assert not iq.exists()
