"""Provision a Foundry IQ knowledge base for FibreOps.

Creates (idempotently) in an Azure AI Search service:

1. A text + semantic search **index** (``fibreops-knowledge``) seeded with the
   FibreOps Standard Operating Procedures (``src/fibreops/data/sop_*.md``) and
   the fibre-node topology (``src/fibreops/data/fibre_nodes.json``).
2. A **knowledge source** (``fibreops-knowledge-source``) over that index.
3. A **knowledge base** (``fibreops-knowledge-base``) that orchestrates
   agentic retrieval with *minimal* reasoning effort and *extractive* output —
   so no Azure OpenAI deployment is required on the search side.

The knowledge base exposes an MCP endpoint that a Foundry agent reaches through
a project connection / toolbox (see ``scripts/connect-foundry-iq.ps1``) and
calls as the ``knowledge_base_retrieve`` tool.

Auth: pass ``--admin-key`` (or set ``SEARCH_ADMIN_KEY``) to use the search admin
key, otherwise ``DefaultAzureCredential`` is used (the caller then needs
*Search Service Contributor* + *Search Index Data Contributor* on the service).

Usage:
    python scripts/provision_foundry_iq.py \
        --endpoint https://<search>.search.windows.net [--admin-key <key>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    KnowledgeBase,
    KnowledgeRetrievalMinimalReasoningEffort,
    KnowledgeRetrievalOutputMode,
    KnowledgeSourceReference,
    SearchableField,
    SearchIndex,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SearchFieldDataType,
)

INDEX_NAME = "fibreops-knowledge"
SEMANTIC_CONFIG = "fibreops-semantic"
KNOWLEDGE_SOURCE = "fibreops-knowledge-source"
KNOWLEDGE_BASE = "fibreops-knowledge-base"

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "fibreops" / "data"


def _credential(admin_key: str | None):
    if admin_key:
        return AzureKeyCredential(admin_key)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _build_index() -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="title", type=SearchFieldDataType.String),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(name="category", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
    ]
    semantic = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                ),
            )
        ]
    )
    return SearchIndex(name=INDEX_NAME, fields=fields, semantic_search=semantic)


def _documents() -> list[dict]:
    docs: list[dict] = []
    # SOP markdown files
    for md in sorted(_DATA_DIR.glob("sop_*.md")):
        text = md.read_text(encoding="utf-8")
        first_line = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), md.stem)
        docs.append(
            {
                "id": md.stem,
                "title": first_line,
                "content": text,
                "category": "sop",
                "source": md.name,
            }
        )
    # Fibre node topology
    nodes_path = _DATA_DIR / "fibre_nodes.json"
    if nodes_path.exists():
        for node in json.loads(nodes_path.read_text(encoding="utf-8")):
            node_id = node.get("node_id", "")
            readable = ", ".join(f"{k}: {v}" for k, v in node.items())
            docs.append(
                {
                    "id": f"node-{node_id}",
                    "title": f"Fibre node {node_id} — {node.get('site', '')}".strip(),
                    "content": readable,
                    "category": "node",
                    "source": "fibre_nodes.json",
                }
            )
    return docs


def main() -> int:
    ap = argparse.ArgumentParser(description="Provision the FibreOps Foundry IQ knowledge base.")
    ap.add_argument("--endpoint", default=os.environ.get("SEARCH_ENDPOINT"), required=False)
    ap.add_argument("--admin-key", default=os.environ.get("SEARCH_ADMIN_KEY"))
    args = ap.parse_args()
    if not args.endpoint:
        print("ERROR: --endpoint (or SEARCH_ENDPOINT) is required.", file=sys.stderr)
        return 2

    cred = _credential(args.admin_key)
    index_client = SearchIndexClient(endpoint=args.endpoint, credential=cred)

    print(f"1/4 Creating index '{INDEX_NAME}' ...")
    index_client.create_or_update_index(_build_index())

    print("2/4 Uploading FibreOps documents (SOPs + node topology) ...")
    docs = _documents()
    search_client = SearchClient(endpoint=args.endpoint, index_name=INDEX_NAME, credential=cred)
    result = search_client.upload_documents(documents=docs)
    print(f"     uploaded {sum(1 for r in result if r.succeeded)}/{len(docs)} documents")

    print(f"3/4 Creating knowledge source '{KNOWLEDGE_SOURCE}' ...")
    knowledge_source = SearchIndexKnowledgeSource(
        name=KNOWLEDGE_SOURCE,
        description="FibreOps SOPs and fibre-node topology.",
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=INDEX_NAME,
            semantic_configuration_name=SEMANTIC_CONFIG,
        ),
    )
    index_client.create_or_update_knowledge_source(knowledge_source)

    print(f"4/4 Creating knowledge base '{KNOWLEDGE_BASE}' ...")
    knowledge_base = KnowledgeBase(
        name=KNOWLEDGE_BASE,
        description="FibreOps NOC knowledge base for agentic retrieval.",
        knowledge_sources=[KnowledgeSourceReference(name=KNOWLEDGE_SOURCE)],
        retrieval_reasoning_effort=KnowledgeRetrievalMinimalReasoningEffort(),
        output_mode=KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA,
    )
    index_client.create_or_update_knowledge_base(knowledge_base)

    print("\nDone. Foundry IQ knowledge base ready:")
    print(f"  search endpoint : {args.endpoint}")
    print(f"  knowledge base  : {KNOWLEDGE_BASE}")
    print(f"  MCP endpoint    : {args.endpoint.rstrip('/')}/knowledgeBases/{KNOWLEDGE_BASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
