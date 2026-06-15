"""Microsoft Teams integration — outbox fallback test.

When ``TEAMS_WEBHOOK_URL`` is not configured (the default in tests, enforced
by ``conftest._hermetic_env``), the post_* helpers must serialise the
Adaptive Card to ``state/teams_outbox.jsonl`` instead of making an HTTP call.
"""
from __future__ import annotations

import json
from pathlib import Path

from fibreops.tools.teams import post_outage_notice, post_status_update


def _read_outbox(root: Path) -> list[dict]:
    path = root / "state" / "teams_outbox.jsonl"
    assert path.exists(), "outbox file was not created"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_outage_notice_falls_back_to_outbox(chdir_state_tmp):
    result = post_outage_notice(
        incident_id="INC-001",
        node_id="FN-LDN-001",
        severity="critical",
        summary="LoS on FN-LDN-001",
        customer_impact="~8,200 customers",
        probable_cause="fibre cut",
    )
    assert result == {"status": "logged-locally"}
    [card] = _read_outbox(chdir_state_tmp)
    body = card["attachments"][0]["content"]["body"]
    title_block = body[0]
    facts = body[2]["facts"]
    assert "Fibre outage detected — CRITICAL" in title_block["text"]
    assert title_block["color"] == "attention"  # red — critical severity
    assert {f["title"] for f in facts} >= {"Incident", "Node", "Probable cause", "Customer impact"}


def test_status_update_includes_engineer_and_eta_facts(chdir_state_tmp):
    post_status_update(
        incident_id="INC-002",
        status="engineer_dispatched",
        note="Engineer en route",
        engineer_name="Priya Shah",
        eta_minutes=18,
    )
    [card] = _read_outbox(chdir_state_tmp)
    facts = card["attachments"][0]["content"]["body"][2]["facts"]
    by_title = {f["title"]: f["value"] for f in facts}
    assert by_title["Status"] == "engineer_dispatched"
    assert by_title["Engineer"] == "Priya Shah"
    assert by_title["ETA"] == "18 min"


def test_status_update_omits_optional_facts_when_absent(chdir_state_tmp):
    post_status_update(incident_id="INC-003", status="acknowledged", note="seen")
    [card] = _read_outbox(chdir_state_tmp)
    titles = {f["title"] for f in card["attachments"][0]["content"]["body"][2]["facts"]}
    assert "Engineer" not in titles
    assert "ETA" not in titles
