"""Microsoft 365 Copilot declarative-agent package builder.

Builds a sideloadable Microsoft 365 Copilot **declarative agent** package for
FibreOps. A declarative agent is a Copilot extension defined by:

* ``declarativeAgent.json`` — the agent definition (name, instructions,
  conversation starters, and the actions it can call).
* ``fibreops-action.json``  — an API plugin manifest pointing at the FibreOps
  FastAPI app's OpenAPI document so Copilot can call back into ``/sdk/chat``.
* ``manifest.json``         — the Teams app-manifest wrapper that registers the
  declarative agent with Microsoft 365.
* ``color.png`` / ``outline.png`` — the required app icons (192×192 / 32×32).

``build_m365_package`` writes all of the above plus a ``fibreops-copilot.zip``
ready to sideload via the Teams Admin / Microsoft 365 Admin Center.

The public callback URL is read from ``M365_ACTION_BASE_URL`` (and publisher
metadata from ``M365_PUBLISHER_NAME`` / ``M365_PUBLISHER_WEBSITE``) so the same
package can be re-pointed at any FibreOps deployment with zero code changes.
"""
from __future__ import annotations

import binascii
import json
import struct
import zlib
from pathlib import Path
from typing import Any

from ..config import get_settings

# Stable filenames inside the package (referenced cross-file + in the zip root).
DECLARATIVE_AGENT_FILE = "declarativeAgent.json"
ACTION_PLUGIN_FILE = "fibreops-action.json"
MANIFEST_FILE = "manifest.json"
COLOR_ICON_FILE = "color.png"
OUTLINE_ICON_FILE = "outline.png"
ZIP_FILE = "fibreops-copilot.zip"

# Placeholder used when no public callback URL is configured. The CLI prints a
# warning so the operator remembers to set M365_ACTION_BASE_URL before sideload.
_PLACEHOLDER_BASE_URL = "https://your-fibreops-host.example.com"

_BRAND_COLOR = (0x0F, 0x6C, 0xBD)  # FibreOps blue


def _base_url() -> str:
    """Resolve the public FibreOps API base URL (no trailing slash)."""
    settings = get_settings()
    base = settings.m365_action_base_url or _PLACEHOLDER_BASE_URL
    return base.rstrip("/")


def build_declarative_agent() -> dict[str, Any]:
    """Return the declarative-agent definition (``declarativeAgent.json``)."""
    settings = get_settings()
    base = _base_url()
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/copilot/declarative-agent/v1.2/schema.json",
        "version": "v1.2",
        "name": "FibreOps NOC Copilot",
        "description": (
            "Network Operations Center copilot for autonomous fibre outage "
            "response — query live incidents, dispatch status, and optimiser "
            "performance from the FibreOps agent system."
        ),
        "instructions": (
            "You are the FibreOps NOC Copilot. Help network operators understand "
            "live fibre outages, the actions the FibreOps agents have taken "
            "(incident analysis, NetOps coordination, and field dispatch), and "
            "how the optimiser is improving the response. Always call the "
            "'fibreops' action to fetch real run state; never invent incident "
            "data. Be concise and operations-focused."
        ),
        "conversation_starters": [
            {
                "title": "Latest incident",
                "text": "Show me the latest fibre outage incident and what was done.",
            },
            {
                "title": "Dispatch status",
                "text": "Which engineers are currently dispatched and their ETAs?",
            },
            {
                "title": "Optimiser performance",
                "text": "How is the optimiser performing across recent runs?",
            },
        ],
        "actions": [
            {
                "id": "fibreops",
                "file": ACTION_PLUGIN_FILE,
            }
        ],
        "metadata": {
            "actionBaseUrl": base,
            "publisher": settings.m365_publisher_name,
            "publisherWebsite": settings.m365_publisher_website,
        },
    }


def build_action_plugin() -> dict[str, Any]:
    """Return the API plugin manifest (``fibreops-action.json``)."""
    base = _base_url()
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/copilot/plugin/v2.1/schema.json",
        "schema_version": "v2.1",
        "name_for_human": "FibreOps",
        "namespace": "fibreops",
        "description_for_human": "Query the FibreOps NOC agent system.",
        "description_for_model": (
            "Calls the FibreOps API to answer questions about live fibre "
            "outages, agent actions, dispatch status, and optimiser results."
        ),
        "functions": [
            {
                "name": "ask",
                "description": (
                    "Ask the FibreOps NOC agent system a natural-language "
                    "question about incidents, dispatch, or the optimiser."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The operator's natural-language question.",
                        }
                    },
                    "required": ["prompt"],
                },
                "returns": {
                    "type": "string",
                    "description": "The FibreOps agent system's answer.",
                },
            }
        ],
        "runtimes": [
            {
                "type": "OpenApi",
                "auth": {"type": "None"},
                "spec": {"url": f"{base}/openapi.json"},
                "run_for_functions": ["ask"],
            }
        ],
    }


def build_app_manifest() -> dict[str, Any]:
    """Return the Teams app-manifest wrapper (``manifest.json``)."""
    settings = get_settings()
    base = _base_url()
    return {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.19/MicrosoftTeams.schema.json",
        "manifestVersion": "1.19",
        "version": "1.0.0",
        "id": settings.m365_app_id,
        "developer": {
            "name": settings.m365_publisher_name,
            "websiteUrl": settings.m365_publisher_website,
            "privacyUrl": f"{settings.m365_publisher_website}/privacy",
            "termsOfUseUrl": f"{settings.m365_publisher_website}/terms",
        },
        "name": {"short": "FibreOps NOC", "full": "FibreOps NOC Copilot"},
        "description": {
            "short": "Autonomous fibre outage response copilot.",
            "full": (
                "FibreOps NOC Copilot surfaces live fibre outage incidents, "
                "agent actions, dispatch status, and optimiser performance "
                "from the FibreOps agent system inside Microsoft 365 Copilot."
            ),
        },
        "icons": {"color": COLOR_ICON_FILE, "outline": OUTLINE_ICON_FILE},
        "accentColor": "#0F6CBD",
        "copilotAgents": {
            "declarativeAgents": [
                {"id": "fibreops", "file": DECLARATIVE_AGENT_FILE}
            ]
        },
        "permissions": ["identity"],
        "validDomains": [base.split("//", 1)[-1]],
    }


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png_square(size: int, rgb: tuple[int, int, int]) -> bytes:
    """Build a minimal, valid solid-colour square PNG of ``size``×``size``."""
    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR: width, height, bit depth 8, colour type 2 (truecolour RGB).
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    pixel = bytes(rgb)
    row = b"\x00" + pixel * size  # filter byte (0 = none) + RGB pixels
    raw = row * size
    idat = zlib.compress(raw, 9)
    return (
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def build_m365_package(out_dir: Path) -> dict[str, Path]:
    """Write all package artefacts to ``out_dir`` and return their paths.

    Returns a mapping with keys ``declarativeAgent``, ``actionPlugin``,
    ``manifest``, ``color``, ``outline`` and ``zip``.
    """
    import zipfile

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    declarative_path = out_dir / DECLARATIVE_AGENT_FILE
    action_path = out_dir / ACTION_PLUGIN_FILE
    manifest_path = out_dir / MANIFEST_FILE
    color_path = out_dir / COLOR_ICON_FILE
    outline_path = out_dir / OUTLINE_ICON_FILE
    zip_path = out_dir / ZIP_FILE

    declarative_path.write_text(
        json.dumps(build_declarative_agent(), indent=2), encoding="utf-8"
    )
    action_path.write_text(
        json.dumps(build_action_plugin(), indent=2), encoding="utf-8"
    )
    manifest_path.write_text(
        json.dumps(build_app_manifest(), indent=2), encoding="utf-8"
    )
    color_path.write_bytes(_png_square(192, _BRAND_COLOR))
    outline_path.write_bytes(_png_square(32, (0xFF, 0xFF, 0xFF)))

    # Package the five artefacts at the zip root (no parent directories).
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(declarative_path, DECLARATIVE_AGENT_FILE)
        zf.write(action_path, ACTION_PLUGIN_FILE)
        zf.write(manifest_path, MANIFEST_FILE)
        zf.write(color_path, COLOR_ICON_FILE)
        zf.write(outline_path, OUTLINE_ICON_FILE)

    return {
        "declarativeAgent": declarative_path,
        "actionPlugin": action_path,
        "manifest": manifest_path,
        "color": color_path,
        "outline": outline_path,
        "zip": zip_path,
    }
