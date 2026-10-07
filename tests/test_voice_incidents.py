import json
import struct
from collections import Counter
from types import SimpleNamespace

import pytest

from fibreops.voice_live.agent_tools import _summarise_run
from scripts.validate_voice_audio import cases_for_suite, case_manifest, turn, validate
from scripts.voice_incident_cases import IncidentToolFixture, incident_cases


def test_incident_cases_are_balanced_and_keep_holdout_separate():
    cases = incident_cases()
    assert len(cases) == 36
    assert len({case.name for case in cases}) == 36
    assert Counter(case.expected_language for case in cases) == {"en": 12, "pl": 12, "ar": 12}
    assert Counter(case.split for case in cases) == {"development": 24, "held-out": 12}
    assert all(case.incident and "Source facts" not in case.question for case in cases)
    assert len(cases_for_suite("language")) == 16
    with pytest.raises(ValueError, match="Unknown audio suite"):
        cases_for_suite("unknown")


def test_v2_relabels_inspected_holdout_and_freezes_distinct_cases():
    original = incident_cases()
    cases = cases_for_suite("incidents-v2")
    assert len(cases) == len({case.name for case in cases}) == 48
    assert Counter(case.expected_language for case in cases) == {"en": 16, "pl": 16, "ar": 16}
    assert Counter(case.split for case in cases) == {"development": 24, "regression": 12, "held-out": 12}
    assert all(case.split == "held-out" for case in original if case.name.startswith("incident-stale"))
    old_names = {case.name for case in original}
    assert all(case.name not in old_names for case in cases if case.split == "held-out")
    assert case_manifest(cases) == case_manifest(cases_for_suite("incidents-v2"))
    assert case_manifest(cases)[1] != case_manifest(original)[1]


def test_incident_tools_never_dispatch_real_actions():
    fixture = IncidentToolFixture(incident_cases()[0])
    assert not fixture.passed
    response = json.loads(fixture.dispatch("list_recent_incidents", '{"limit": 1}'))
    assert response["incidents"][0]["potentially_affected_customers"] == 800
    assert fixture.passed
    assert "error" in json.loads(fixture.dispatch("notify_teams", '{"note": "send now"}'))
    assert not fixture.passed


@pytest.mark.parametrize("name,args", [
    ("lookup_incident", '{"id": "unknown"}'),
    ("list_recent_incidents", '{"limit": 0}'),
    ("lookup_node", '{"node_id": "FN-MAN-007"}'),
])
def test_incident_fixture_rejects_unexpected_calls(name, args):
    fixture = IncidentToolFixture(incident_cases()[0])
    assert "error" in json.loads(fixture.dispatch(name, args))
    assert not fixture.passed


def test_voice_lookup_separates_known_counts_cause_and_arrival():
    summary = _summarise_run({
        "signal": {"signal_type": "high_attenuation", "measured_value": 7.19, "unit": "dB"},
        "node_context": {"customers_served": 800},
        "steps": [
            {"agent": "IncidentAnalysisAgent", "output": {"probable_cause": "fibre cut"}},
            {"agent": "FieldDispatchAgent", "result": "DISPATCHED",
             "metadata": {"dispatch": {"engineer_name": "Test Engineer", "eta_minutes": 15}}},
        ],
    })
    assert summary["potentially_affected_customers"] == 800
    assert not summary["customer_impact_confirmed"]
    assert summary["suspected_cause"] == "fibre cut"
    assert not summary["cause_confirmed"]
    assert summary["technician_arrival_eta_minutes"] == 15
    assert summary["service_restoration_eta_minutes"] is None
    assert summary["measurement"] == {"value": 7.19, "unit": "dB"}
    assert _summarise_run({})["potentially_affected_customers"] is None


@pytest.mark.asyncio
async def test_audio_turn_waits_for_tool_result_and_final_spoken_response():
    events = [
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Latest incident please"},
        {"type": "response.function_call_arguments.done", "name": "list_recent_incidents", "arguments": "{}", "call_id": "c1"},
        {"type": "response.done", "response": {"status": "completed"}},
        {"type": "response.output_audio_transcript.delta", "delta": "Critical loss of optical signal."},
        {"type": "response.output_audio.delta", "delta": "AQABAA=="},
        {"type": "response.done", "response": {"status": "completed"}},
    ]

    class Socket:
        sent = []

        async def recv(self):
            return json.dumps(events.pop(0))

        async def send(self, value):
            self.sent.append(json.loads(value))

    socket = Socket()
    fixture = IncidentToolFixture(incident_cases()[0])
    transcript, reply, audio = await turn(socket, struct.pack("<h", 1000), fixture)
    assert fixture.passed
    assert transcript == "Latest incident please"
    assert reply == "Critical loss of optical signal."
    assert audio == b"\x01\x00\x01\x00"
    results = [event for event in socket.sent if event["type"] == "conversation.item.create"]
    assert results[0]["item"]["call_id"] == "c1"
    assert any(event["type"] == "response.create" for event in socket.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("transcript_done", [False, True])
async def test_audio_turn_preserves_all_preambles_and_response_boundaries(transcript_done):
    events = [
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "Latest incident please"},
        {"type": "response.output_audio_transcript.delta", "delta": "I will "},
        {"type": "response.output_audio_transcript.delta", "delta": "check."},
    ]
    if transcript_done:
        events.append({"type": "response.output_audio_transcript.done", "transcript": "I will check."})
    events.extend([
        {"type": "response.function_call_arguments.done", "name": "list_recent_incidents", "arguments": "{}", "call_id": "c1"},
        {"type": "response.done", "response": {"status": "completed"}},
        {"type": "response.audio_transcript.delta", "delta": "لم يتم "},
        {"type": "response.audio_transcript.delta", "delta": "إرسال أي رسائل."},
        {"type": "response.audio.delta", "delta": "AQABAA=="},
        {"type": "response.done", "response": {"status": "completed"}},
    ])

    class Socket:
        async def recv(self):
            return json.dumps(events.pop(0))

        async def send(self, value):
            pass

    segments = []
    _, reply, audio = await turn(
        Socket(), struct.pack("<h", 1000), IncidentToolFixture(incident_cases()[0]),
        transcript_segments=segments,
    )
    assert segments == ["I will check.", "لم يتم إرسال أي رسائل."]
    assert reply == "I will check.\nلم يتم إرسال أي رسائل."
    assert audio == b"\x01\x00\x01\x00"


@pytest.mark.asyncio
async def test_resume_refuses_mixed_transcript_protocols(tmp_path):
    previous = tmp_path / "previous"
    previous.mkdir()
    cases = incident_cases()
    (previous / "report.json").write_text(json.dumps({
        "suite": "incidents", "agent": "test", "version": "2", "expected_turns": 72,
        "results": [{"repeat": 1, "case": cases[0].name}],
        "transcript_protocol": "legacy-concatenated-v1",
    }))
    args = SimpleNamespace(
        suite="incidents", agent="test", version="2", repeats=2,
        output_dir=tmp_path / "resume", resume_from=previous,
    )
    with pytest.raises(ValueError, match="transcript_protocol"):
        await validate(args)


@pytest.mark.asyncio
async def test_resume_refuses_selected_cases_before_any_cloud_calls(tmp_path):
    previous = tmp_path / "previous"
    previous.mkdir()
    cases = incident_cases()
    (previous / "report.json").write_text(json.dumps({
        "suite": "incidents", "agent": "test", "version": "2", "expected_turns": 72,
        "results": [{"repeat": 1, "case": cases[1].name}],
    }))
    args = SimpleNamespace(
        suite="incidents", agent="test", version="2", repeats=2,
        output_dir=tmp_path / "resume", resume_from=previous,
    )
    with pytest.raises(ValueError, match="sequential prefix"):
        await validate(args)


@pytest.mark.asyncio
async def test_resume_refuses_shared_language_conversations(tmp_path):
    args = SimpleNamespace(
        suite="language", agent="test", version="2", repeats=2,
        output_dir=tmp_path / "resume", resume_from=tmp_path / "previous",
    )
    with pytest.raises(ValueError, match="isolated incident"):
        await validate(args)
