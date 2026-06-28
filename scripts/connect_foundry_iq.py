"""Create the Foundry project connection to the Foundry IQ knowledge base MCP endpoint.

A Foundry **RemoteTool** project connection (authenticated with the project's
managed identity) targets the knowledge base's MCP endpoint. The published
incident-analysis Prompt Agent then references this connection via an MCP tool
(``knowledge_base_retrieve``) so the *hosted* backend grounds on Foundry IQ.

Run this once after the knowledge base exists, then (re)publish the agents with
``python -m fibreops.demo publish`` (set ``FOUNDRY_IQ_MCP_CONNECTION`` to the
connection name first).

Permissions:
  - The caller needs **Foundry Project Manager** on the Foundry account to
    create the connection.
  - The Foundry **project** managed identity needs **Search Index Data Reader**
    on the search service (granted by scripts/grant-mi-roles.ps1).

Usage:
  python scripts/connect_foundry_iq.py \
    --foundry-account <account> --foundry-resource-group <rg> \
    --project-name <project> \
    --search-endpoint https://<search>.search.windows.net \
    --knowledge-base fibreops-knowledge-base \
    --connection-name fibreops-kb-mcp
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
from azure.identity import DefaultAzureCredential

KB_MCP_API_VERSION = "2026-05-01-preview"
CONNECTIONS_API_VERSION = "2025-10-01-preview"


def main() -> int:
    ap = argparse.ArgumentParser(description="Create the Foundry IQ MCP project connection.")
    ap.add_argument("--subscription-id", default=os.environ.get("AZURE_SUBSCRIPTION_ID"))
    ap.add_argument("--foundry-account", required=True)
    ap.add_argument("--foundry-resource-group", required=True)
    ap.add_argument("--project-name", required=True)
    ap.add_argument("--search-endpoint", default=os.environ.get("FOUNDRY_IQ_SEARCH_ENDPOINT"), required=False)
    ap.add_argument("--knowledge-base", default=os.environ.get("FOUNDRY_IQ_KNOWLEDGE_BASE", "fibreops-knowledge-base"))
    ap.add_argument("--connection-name", default=os.environ.get("FOUNDRY_IQ_MCP_CONNECTION", "fibreops-kb-mcp"))
    args = ap.parse_args()

    if not args.subscription_id:
        print("ERROR: --subscription-id (or AZURE_SUBSCRIPTION_ID) is required.", file=sys.stderr)
        return 2
    if not args.search_endpoint:
        print("ERROR: --search-endpoint (or FOUNDRY_IQ_SEARCH_ENDPOINT) is required.", file=sys.stderr)
        return 2

    # New Foundry projects are CognitiveServices account sub-resources.
    project_resource_id = (
        f"/subscriptions/{args.subscription_id}"
        f"/resourceGroups/{args.foundry_resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{args.foundry_account}"
        f"/projects/{args.project_name}"
    )
    mcp_endpoint = (
        f"{args.search_endpoint.rstrip('/')}"
        f"/knowledgebases/{args.knowledge_base}/mcp?api-version={KB_MCP_API_VERSION}"
    )

    credential = DefaultAzureCredential()
    token = credential.get_token("https://management.azure.com/.default").token
    url = (
        f"https://management.azure.com{project_resource_id}"
        f"/connections/{args.connection_name}?api-version={CONNECTIONS_API_VERSION}"
    )
    body = {
        "name": args.connection_name,
        "type": "Microsoft.MachineLearningServices/workspaces/connections",
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "RemoteTool",
            "target": mcp_endpoint,
            "isSharedToAll": True,
            "audience": "https://search.azure.com/",
            "metadata": {"ApiType": "Azure"},
        },
    }
    print(f"Creating project connection '{args.connection_name}' -> {mcp_endpoint}")
    resp = httpx.put(url, headers={"Authorization": f"Bearer {token}"}, json=body, timeout=60)
    if resp.status_code >= 400:
        print(f"ERROR {resp.status_code}: {resp.text}", file=sys.stderr)
        return 1
    print("Connection created/updated successfully.")
    print(f"  Set FOUNDRY_IQ_MCP_CONNECTION={args.connection_name} and re-run `fibreops.demo publish`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
