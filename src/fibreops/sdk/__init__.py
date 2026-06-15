"""FibreOps SDK surface — programmatic clients for external apps.

This package exposes :class:`FibreOpsCopilotClient`, a thin wrapper around the
orchestrator that mirrors the **GitHub Copilot SDK** session API
(``create_session`` + ``send_and_wait``). It lets a Copilot CLI script, a VS
Code extension, or any other Copilot-shaped client invoke the FibreOps agent
system over the same surface used by hosted copilot apps.

BRK241 slide 4 lists the GitHub Copilot SDK as one of the building blocks of
the system; this module is the concrete binding.
"""
from .copilot_client import (
    FibreOpsCopilotClient,
    FibreOpsCopilotResponse,
    FibreOpsCopilotSession,
)

__all__ = [
    "FibreOpsCopilotClient",
    "FibreOpsCopilotResponse",
    "FibreOpsCopilotSession",
]
