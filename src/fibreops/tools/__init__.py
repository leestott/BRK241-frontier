"""Agent tools.

Each tool is a plain Python function with strict argument schemas. The agents
register these via FunctionTool when running in Foundry, and the local runner
calls them directly when Foundry credentials are absent.
"""
from .knowledge import lookup_sop, list_sops_tool, web_iq_search, work_iq_search
from .teams import post_outage_notice, post_status_update
from .ticketing import create_ticket, update_ticket
from .dispatch import find_best_engineer, dispatch_engineer
from .memory import remember, recall
from .voice import speak_status_update

__all__ = [
    "lookup_sop",
    "list_sops_tool",
    "web_iq_search",
    "work_iq_search",
    "post_outage_notice",
    "post_status_update",
    "create_ticket",
    "update_ticket",
    "find_best_engineer",
    "dispatch_engineer",
    "remember",
    "recall",
    "speak_status_update",
]
