"""Orchestrator unit tests — small parsers + response-text extraction.

The full-pipeline e2e test lives in :mod:`tests.test_e2e_local`; these are
the in-isolation contracts the orchestrator relies on.
"""
from __future__ import annotations

import pytest

from fibreops.orchestrator import _extract_text, _parse_analysis_json


class _Resp:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_extract_text_from_text_attr():
    assert _extract_text(_Resp(text="hello")) == "hello"


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
    assert out == {"severity": "high", "summary": "x"}


def test_parse_analysis_json_raises_when_no_object():
    with pytest.raises(ValueError):
        _parse_analysis_json("no json here")
