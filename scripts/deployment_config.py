"""Portable helpers for AZD-backed FibreOps deployments."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Mapping


DEPLOYMENT_OWNED_KEYS = (
    "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "AZURE_AI_MODEL_DEPLOYMENT",
    "AZURE_AI_PROJECT_ENDPOINT",
    "AZURE_APP_SERVICE_HOSTNAME",
    "AZURE_APP_SERVICE_NAME",
    "AZURE_APP_SERVICE_URL",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT",
    "AZURE_CONTAINER_REGISTRY_NAME",
    "AZURE_ENV_NAME",
    "AZURE_FOUNDRY_ACCOUNT_NAME",
    "AZURE_FOUNDRY_PROJECT_NAME",
    "AZURE_LOCATION",
    "AZURE_RESOURCE_GROUP",
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_LOCATION",
    "AZURE_SEARCH_SERVICE_NAME",
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_TENANT_ID",
    "AZURE_VOICE_AGENT_MODEL",
    "AZURE_VOICE_AGENT_NAME",
    "AZURE_VOICE_AGENT_PREFER_LOCALES",
    "AZURE_VOICE_AGENT_VERSION",
    "AZURE_VOICE_AGENT_VOICE",
    "AZURE_VOICE_AGENT_VOICE_TYPE",
    "AZURE_VOICE_LIVE_ACCOUNT_ID",
    "AZURE_VOICE_LIVE_ACCOUNT_NAME",
    "AZURE_VOICE_LIVE_ENDPOINT",
    "EVENT_HUB_FQDN",
    "EVENT_HUB_NAME",
    "FIBREOPS_AGENT_BACKEND",
    "FIBREOPS_HOSTED_AGENT_NAME",
    "FOUNDRY_IQ_KNOWLEDGE_BASE",
    "FOUNDRY_IQ_MCP_CONNECTION",
    "FOUNDRY_IQ_SEARCH_ENDPOINT",
    "KEY_VAULT_NAME",
    "M365_ACTION_BASE_URL",
)
_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_azd_values(output: str) -> dict[str, str]:
    """Parse ``azd env get-values`` output without evaluating shell code."""
    values: dict[str, str] = {}
    for line in output.splitlines():
        match = _ASSIGNMENT.match(line.strip())
        if not match:
            continue
        key, raw = match.groups()
        parsed = shlex.split(raw, posix=True)
        values[key] = parsed[0] if parsed else ""
    return values


def get_azd_values(environment: str | None = None) -> dict[str, str]:
    command = ["azd", "env", "get-values"]
    if environment:
        command.extend(["--environment", environment])
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    return parse_azd_values(result.stdout)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def sync_env_file(values: Mapping[str, str], destination: Path) -> list[str]:
    """Update deployment-owned non-secret values while preserving local overrides."""
    selected = {key: values[key] for key in DEPLOYMENT_OWNED_KEYS if key in values}
    original = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
    updated: list[str] = []
    written: set[str] = set()
    for line in original:
        match = _ASSIGNMENT.match(line.strip())
        if match and match.group(1) in selected:
            key = match.group(1)
            updated.append(f"{key}={_quote(selected[key])}")
            written.add(key)
        else:
            updated.append(line)
    missing = [key for key in DEPLOYMENT_OWNED_KEYS if key in selected and key not in written]
    if missing:
        if updated and updated[-1]:
            updated.append("")
        updated.append("# --- Synced from the selected AZD environment ---")
        updated.extend(f"{key}={_quote(selected[key])}" for key in missing)
    destination.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    return [key for key in DEPLOYMENT_OWNED_KEYS if key in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync-env",))
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME"))
    parser.add_argument("--destination", type=Path, default=Path(".env"))
    args = parser.parse_args()
    values = get_azd_values(args.environment)
    updated = sync_env_file(values, args.destination)
    print(f"Updated {args.destination} with {len(updated)} non-secret values from AZD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
