"""Orchestrator unit tests — small parsers + response-text extraction.

The full-pipeline e2e test lives in :mod:`tests.test_e2e_local`; these are
the in-isolation contracts the orchestrator relies on.
"""
from __future__ import annotations

import pytest

from fibreops.orchestrator import _extract_text, _parse_analysis_json, _sanitise_decision


class _Resp:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_extract_text_from_text_attr():
    assert _extract_text(_Resp(text="hello")) == "hello"


def test_sanitise_decision_strips_routing_and_spam():
    garbled = " to=create_ticket \u3000\u8001\u53f8\u673a to=create_ticket  \u5f69\u795e\u4e89\u9738? "
    assert _sanitise_decision(garbled, "DISPATCH") == "DISPATCH"


def test_sanitise_decision_preserves_json_payload():
    raw = ' to=create_ticket  \u5927\u5feb\u4e09={"severity":"high","title":"x"} '
    assert _sanitise_decision(raw, "DISPATCH") == '{"severity":"high","title":"x"}'


def test_sanitise_decision_keeps_clean_text():
    assert _sanitise_decision("HANDOFF:DISPATCH auto-dispatch", "DISPATCH") == "HANDOFF:DISPATCH auto-dispatch"


def test_sanitise_decision_drops_punctuation_only_residue():
    # Live sample ended with `北京赛车如何=""` which previously left a stray `""`.
    raw = " to=create_ticket  \u5317\u4eac\u8d5b\u8f66\u5982\u4f55=\"\"  "
    assert _sanitise_decision(raw, "MONITOR") == "MONITOR"


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
