"""Tests for the M365 Copilot declarative agent package builder.

Validates manifest shape, PNG validity, zip contents, env-var hot-swap of
the action base URL + publisher metadata, and the ``publish-m365`` CLI
subcommand.
"""
from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fibreops.dist.m365 import (
    build_action_plugin,
    build_app_manifest,
    build_declarative_agent,
    build_m365_package,
)


_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def test_declarative_agent_shape(chdir_state_tmp: Path) -> None:
    da = build_declarative_agent()
    assert da["name"] == "FibreOps NOC Copilot"
    assert "instructions" in da and "FibreOps" in da["instructions"]
    starters = [s["title"] for s in da["conversation_starters"]]
    assert any("latest" in s.lower() for s in starters)
    assert any("optimiser" in s.lower() or "performing" in s.lower() for s in starters)
    # Action references the action plugin file.
    action_files = [a["file"] for a in da["actions"]]
    assert "fibreops-action.json" in action_files


def test_declarative_agent_honours_env_overrides(
    chdir_state_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("M365_ACTION_BASE_URL", "https://fibreops.contoso.com")
    monkeypatch.setenv("M365_PUBLISHER_NAME", "Contoso")
    monkeypatch.setenv("M365_PUBLISHER_WEBSITE", "https://contoso.com/fibreops")
    from fibreops import config

    config.get_settings.cache_clear()

    da = build_declarative_agent()
    assert da["metadata"]["actionBaseUrl"] == "https://fibreops.contoso.com"
    assert da["metadata"]["publisher"] == "Contoso"
    plugin = build_action_plugin()
    assert plugin["runtimes"][0]["spec"]["url"] == "https://fibreops.contoso.com/openapi.json"


def test_action_plugin_shape(chdir_state_tmp: Path) -> None:
    plugin = build_action_plugin()
    assert plugin["namespace"] == "fibreops"
    fn = next(f for f in plugin["functions"] if f["name"] == "ask")
    assert "prompt" in fn["parameters"]["properties"]
    assert "prompt" in fn["parameters"]["required"]


def test_app_manifest_shape(chdir_state_tmp: Path) -> None:
    m = build_app_manifest()
    assert m["id"] == "fibreops-copilot-agent"
    assert m["icons"] == {"color": "color.png", "outline": "outline.png"}
    # Copilot declarative-agent wrapper present.
    decl = m["copilotAgents"]["declarativeAgents"][0]
    assert decl["file"] == "declarativeAgent.json"


def test_build_m365_package_emits_zip(chdir_state_tmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "m365out"
    paths = build_m365_package(out)
    # All required artefacts exist
    for key in ("declarativeAgent", "actionPlugin", "manifest", "color", "outline", "zip"):
        assert paths[key].exists(), f"{key} missing"
    # Manifest + declarative are valid JSON
    json.loads(paths["declarativeAgent"].read_text(encoding="utf-8"))
    json.loads(paths["manifest"].read_text(encoding="utf-8"))
    # PNGs are real PNGs (signature + valid IHDR width)
    color_bytes = paths["color"].read_bytes()
    assert color_bytes.startswith(_PNG_SIG)
    width = struct.unpack(">I", color_bytes[16:20])[0]
    assert width == 192
    outline_bytes = paths["outline"].read_bytes()
    assert outline_bytes.startswith(_PNG_SIG)
    assert struct.unpack(">I", outline_bytes[16:20])[0] == 32


def test_zip_contains_all_artefacts(chdir_state_tmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "m365out2"
    paths = build_m365_package(out)
    with zipfile.ZipFile(paths["zip"]) as zf:
        names = set(zf.namelist())
    # All filenames are at root level (no parent dirs)
    assert names == {
        "declarativeAgent.json",
        "fibreops-action.json",
        "manifest.json",
        "color.png",
        "outline.png",
    }
    # Manifest survives the round trip
    with zipfile.ZipFile(paths["zip"]) as zf:
        with zf.open("manifest.json") as f:
            parsed = json.loads(f.read())
    assert parsed["copilotAgents"]["declarativeAgents"][0]["file"] == "declarativeAgent.json"


def test_publish_m365_cli(chdir_state_tmp: Path, tmp_path: Path) -> None:
    """The `python -m fibreops.demo publish-m365` command writes the package."""
    from fibreops.demo import app as demo_app

    out = tmp_path / "cli-m365"
    runner = CliRunner()
    result = runner.invoke(demo_app, ["publish-m365", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "fibreops-copilot.zip").exists()
    assert (out / "declarativeAgent.json").exists()
    assert "M365 Copilot package" in result.output


def test_publish_m365_cli_warns_without_base_url(
    chdir_state_tmp: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("M365_ACTION_BASE_URL", raising=False)
    from fibreops import config

    config.get_settings.cache_clear()

    from fibreops.demo import app as demo_app

    runner = CliRunner()
    result = runner.invoke(demo_app, ["publish-m365", "--out", str(tmp_path / "out3")])
    assert result.exit_code == 0
    assert "M365_ACTION_BASE_URL is not set" in result.output
