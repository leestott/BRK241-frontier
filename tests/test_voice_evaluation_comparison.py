import json

import pytest

from scripts.compare_voice_evaluations import compare_runs


def test_comparison_requires_matching_contracts_and_completed_evidence(tmp_path):
    paths = [tmp_path / "baseline", tmp_path / "candidate"]
    row = {
        "inputs.case": "case", "inputs.repeat": 1, "inputs.query": "Question",
        "inputs.expected_language": "en", "inputs.expected_meaning": "Required facts",
    }
    summary = {
        "cloud_verified": True, "rows": 1, "portal_run": {"status": "completed"},
        "by_response_language": {"en": {"minimum": 3}},
    }
    for path in paths:
        path.mkdir()
        (path / "summary.json").write_text(json.dumps(summary))
        (path / "sdk-results.json").write_text(json.dumps({"rows": [row]}))
    result = compare_runs(paths)
    assert result["same_dataset_verified"]
    assert not result["runs"][0]["human_listening_approved"]
    (paths[1] / "summary.json").write_text(json.dumps({
        **summary, "evaluation_protocol": "voice-sdk-v2",
    }))
    with pytest.raises(ValueError, match="protocols"):
        compare_runs(paths)
    (paths[1] / "summary.json").write_text(json.dumps(summary))
    changed = {**row, "inputs.expected_meaning": "Weaker requirements"}
    (paths[1] / "sdk-results.json").write_text(json.dumps({"rows": [changed]}))
    with pytest.raises(ValueError, match="identical"):
        compare_runs(paths)
    (paths[1] / "summary.json").write_text(json.dumps({**summary, "cloud_verified": False}))
    with pytest.raises(ValueError, match="Unverified"):
        compare_runs(paths)
