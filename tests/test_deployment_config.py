from pathlib import Path

from scripts.deployment_config import parse_azd_values, sync_env_file


def test_parse_azd_values_does_not_evaluate_shell_text():
    values = parse_azd_values(
        'AZURE_LOCATION="northcentralus"\n'
        'AZURE_AI_PROJECT_ENDPOINT="https://example.test/api/projects/demo"\n'
        'IGNORED=$(Write-Host unsafe)\n'
    )

    assert values["AZURE_LOCATION"] == "northcentralus"
    assert values["IGNORED"] == "$(Write-Host"


def test_sync_env_updates_owned_values_and_preserves_local_values(tmp_path: Path):
    destination = tmp_path / ".env"
    destination.write_text(
        'AZURE_LOCATION="old-region"\n'
        'TEAMS_WEBHOOK_URL="local-secret"\n'
        'AZURE_VOICE_LIVE_API_KEY="must-stay-local"\n',
        encoding="utf-8",
    )

    updated = sync_env_file(
        {
            "AZURE_LOCATION": "northcentralus",
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/demo",
            "AZURE_VOICE_LIVE_API_KEY": "must-not-copy",
            "FIBREOPS_AAD_CLIENT_SECRET": "must-not-copy",
        },
        destination,
    )
    content = destination.read_text(encoding="utf-8")

    assert "AZURE_LOCATION=\"northcentralus\"" in content
    assert "AZURE_AI_PROJECT_ENDPOINT=\"https://example.test/api/projects/demo\"" in content
    assert 'TEAMS_WEBHOOK_URL="local-secret"' in content
    assert 'AZURE_VOICE_LIVE_API_KEY="must-stay-local"' in content
    assert "must-not-copy" not in content
    assert updated == ["AZURE_AI_PROJECT_ENDPOINT", "AZURE_LOCATION"]


def test_infrastructure_parameters_are_azd_configurable():
    parameters = (
        Path(__file__).resolve().parents[1] / "infra" / "main.parameters.json"
    ).read_text(encoding="utf-8")

    for variable in (
        "FIBREOPS_NAME_PREFIX",
        "AZURE_FOUNDRY_ACCOUNT_NAME",
        "AZURE_FOUNDRY_PROJECT_NAME",
        "AZURE_AI_MODEL_NAME",
        "AZURE_AI_MODEL_VERSION",
        "AZURE_AI_MODEL_SKU",
        "AZURE_AI_MODEL_CAPACITY",
        "FIBREOPS_HOSTED_AGENT_NAME",
    ):
        assert variable in parameters


def test_derived_foundry_account_name_is_used_for_custom_subdomain():
    template = (
        Path(__file__).resolve().parents[1] / "infra" / "main.bicep"
    ).read_text(encoding="utf-8")

    assert "customSubDomainName: resolvedFoundryAccountName" in template


def test_clean_deploy_defaults_to_resolvable_self_deployed_voice_model():
    root = Path(__file__).resolve().parents[1]
    template = (root / "infra" / "main.bicep").read_text(encoding="utf-8")
    parameters = (root / "infra" / "main.parameters.json").read_text(
        encoding="utf-8"
    )
    postdeploy = (root / "scripts" / "postdeploy.ps1").read_text(encoding="utf-8")

    assert "param provisionVoiceProject bool = true" in template
    assert "param azureVoiceAgentModelType string = 'self_deployed'" in template
    assert "${PROVISION_VOICE_PROJECT=true}" in parameters
    assert "${AZURE_VOICE_AGENT_MODEL_TYPE=self_deployed}" in parameters
    assert "$voiceProjectProvisioned = $env:PROVISION_VOICE_PROJECT -ne 'false'" in postdeploy
    assert "$env:FIBREOPS_PUBLISH_VOICE -eq 'true'" in postdeploy
    assert (
        "$env:FIBREOPS_PUBLISH_VOICE -ne 'false' -and $voiceProjectProvisioned"
        in postdeploy
    )


def test_agent_checker_has_no_user_specific_path():
    checker = (Path(__file__).resolve().parents[1] / "check_agents.py").read_text(
        encoding="utf-8"
    )

    assert "C:/Users/" not in checker
    assert "fibreops-demo" not in checker


def test_postdeploy_has_resumable_named_phases():
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "postdeploy.ps1"
    ).read_text(encoding="utf-8")

    for phase in ("rbac", "iq", "prompt", "hosted", "voice", "sync"):
        assert f'"{phase}"' in script
    assert "FIBREOPS_RESUME_DEPLOYMENT" in script
    assert "postdeploy-state.json" in script
    assert "App Service has no published-agent registry" in script
    assert "FIBREOPS_PUBLISHED_AGENTS" in script


def test_runtime_identity_can_call_project_responses_api():
    script = (
        Path(__file__).resolve().parents[1] / "scripts" / "grant-mi-roles.ps1"
    ).read_text(encoding="utf-8")

    assert '@{ Role = "Foundry User"; Scope = "$foundry/projects/$FoundryProjectName"' in script
    assert '@{ Role = "Foundry Agent Consumer"; Scope = "$foundry/projects/$FoundryProjectName"' not in script
