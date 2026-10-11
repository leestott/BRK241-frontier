"""List agents in the selected AZD environment's Foundry project."""
from __future__ import annotations

import argparse
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from scripts.deployment_config import get_azd_values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", help="AZD environment name; defaults to AZURE_ENV_NAME/current")
    parser.add_argument("--endpoint", help="Explicit Foundry project endpoint")
    args = parser.parse_args()
    values = get_azd_values(args.environment) if not args.endpoint else {}
    endpoint = (
        args.endpoint
        or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        or os.getenv("FOUNDRY_PROJECT_ENDPOINT")
        or values.get("AZURE_AI_PROJECT_ENDPOINT")
    )
    if not endpoint:
        parser.error("No project endpoint found. Select an AZD environment or pass --endpoint.")

    print(f"Endpoint: {endpoint}")
    with AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    ) as client:
        agents = client.agents.list()
        agent_list = agents.data if hasattr(agents, "data") else agents
        for agent in agent_list:
            print(f"Agent Name: {agent.name}, ID: {agent.id}")
            versions = client.agents.list_versions(agent_name=agent.name)
            version_items = versions.data if hasattr(versions, "data") else versions
            for version in version_items:
                print(
                    f"  - Version: {getattr(version, 'version', 'N/A')}, "
                    f"ID: {getattr(version, 'id', 'N/A')}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
