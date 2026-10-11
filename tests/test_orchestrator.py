"""Orchestrator unit tests — small parsers + response-text extraction.

The full-pipeline e2e test lives in :mod:`tests.test_e2e_local`; these are
the in-isolation contracts the orchestrator relies on.
"""
from __future__ import annotations

import pytest

from fibreops.models import Severity, SignalType, TelemetrySignal
from fibreops.orchestrator import (
    _extract_text,
    _parse_analysis_json,
    _preserve_signal_facts,
    _requires_dispatch,
    _sanitise_decision,
)


class _Resp:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_extract_text_from_text_attr():
    assert _extract_text(_Resp(text="hello")) == "hello"


def test_sanitise_decision_strips_routing_and_spam():
    garbled = " to=create_ticket \u3000\u8001\u53f8\u673a to=create_ticket  \u5f69\u795e\u4e89\u9738? "
    assert _sanitise_decision(garbled, "DISPATCH") == "DISPATCH"


def test_sanitise_decision_rejects_structured_tool_payload():
    raw = ' to=create_ticket  \u5927\u5feb\u4e09={"severity":"high","title":"x"} '
    assert _sanitise_decision(raw, "DISPATCH") == "DISPATCH"


def test_sanitise_decision_keeps_clean_text():
    assert _sanitise_decision("HANDOFF:DISPATCH auto-dispatch", "DISPATCH") == "HANDOFF:DISPATCH auto-dispatch"


def test_sanitise_decision_drops_punctuation_only_residue():
    # Live sample ended with `北京赛车如何=""` which previously left a stray `""`.
    raw = " to=create_ticket  \u5317\u4eac\u8d5b\u8f66\u5982\u4f55=\"\"  "
    assert _sanitise_decision(raw, "MONITOR") == "MONITOR"


@pytest.mark.parametrize(
    "raw",
    [
        "code",
        'create_ticket(title="critical outage", severity="critical")',
        "ticket created",
    ],
)
def test_sanitise_decision_rejects_nonsemantic_model_residue(raw):
    assert _sanitise_decision(raw, "DISPATCH") == "DISPATCH"


def test_preserve_signal_facts_repairs_critical_placeholder_analysis():
    signal = TelemetrySignal(
        node_id="FN-LDN-001",
        signal_type=SignalType.LOSS_OF_LIGHT,
        severity=Severity.CRITICAL,
        measured_value=-40,
        unit="dBm",
        raw={"consecutive_polls_dark": 6, "last_good_dbm": -18.4},
    )
    analysis = _parse_analysis_json(
        '{"summary":"Incident detected","probable_cause":"Unknown",'
        '"customer_impact":"Unknown","severity":"medium",'
        '"recommended_actions":[],"sop_refs":[]}'
    )

    result = _preserve_signal_facts(
        analysis,
        signal,
        {
            "node_id": "FN-LDN-001",
            "site": "Shoreditch CO",
            "customers_served": 12400,
        },
    )

    assert result["severity"] == "critical"
    assert "-40 dBm" in result["summary"]
    assert "6 consecutive dark polls" in result["summary"]
    assert "Shoreditch CO" in result["summary"]
    assert "12,400 customers" in result["customer_impact"]
    assert "diagnostics are required" in result["probable_cause"]
    assert len(result["recommended_actions"]) == 3


def test_preserve_signal_facts_repairs_tool_context_refusal():
    signal = TelemetrySignal(
        node_id="FN-LDN-001",
        signal_type=SignalType.LOSS_OF_LIGHT,
        severity=Severity.CRITICAL,
        measured_value=-40,
        unit="dBm",
        raw={"consecutive_polls_dark": 6},
    )
    analysis = {
        "summary": "Unable to complete incident analysis because required SOP lookup was not available in the current tool context.",
        "probable_cause": "Insufficient grounded evidence to determine cause.",
        "customer_impact": "Potentially major service loss, but impact cannot be confirmed.",
        "severity": "critical",
        "recommended_actions": ["Verify the optical path."],
        "sop_refs": [],
        "knowledge": {},
    }

    result = _preserve_signal_facts(
        analysis,
        signal,
        {"site": "Shoreditch CO", "customers_served": 12400},
    )

    assert result["summary"] == (
        "Critical loss of light detected at Shoreditch CO "
        "(FN-LDN-001): -40 dBm across 6 consecutive dark polls."
    )
    assert result["customer_impact"] == (
        "Potential service impact for up to 12,400 customers served by FN-LDN-001."
    )
    assert "Possible fibre cut" in result["probable_cause"]
    assert result["recommended_actions"] == ["Verify the optical path."]


@pytest.mark.parametrize(
    ("coordinator_text", "severity", "auto_dispatch", "expected"),
    [
        ("MONITOR", Severity.CRITICAL, False, True),
        ("MONITOR", Severity.HIGH, False, True),
        ("MONITOR", Severity.MEDIUM, False, False),
        ("HANDOFF:DISPATCH", Severity.MEDIUM, False, True),
        ("MONITOR", Severity.LOW, True, True),
    ],
)
def test_requires_dispatch_is_deterministic_for_high_severity(
    coordinator_text,
    severity,
    auto_dispatch,
    expected,
):
    assert _requires_dispatch(coordinator_text, severity, auto_dispatch) is expected


def test_extract_text_falls_back_to_content_attr():
    assert _extract_text(_Resp(text=None, content="world")) == "world"


def test_extract_text_handles_message_list_with_str_content():
    msg = _Resp(content="line")
    assert _extract_text(_Resp(messages=[msg])) == "line"


def test_extract_text_handles_message_list_with_dict_parts():
    msg = _Resp(content=[{"text": "a"}, {"text": "b"}])
    assert _extract_text(_Resp(messages=[msg])) == "a\nb"


def test_extract_text_none_returns_empty_string():
    assert _extract_text(None) == ""


def test_parse_analysis_json_extracts_object_from_chatty_response():
    text = 'Sure! Here you go: {"severity":"high","summary":"x"} — let me know.'
    out = _parse_analysis_json(text)
    # raw_decode extracts the first JSON object from the chatty text; the result
    # is then normalised to the full analysis schema, so assert the key fields.
    assert out["severity"] == "high"
    assert out["summary"] == "x"


def test_parse_analysis_json_raises_when_no_object():
    with pytest.raises(ValueError):
        _parse_analysis_json("no json here")
