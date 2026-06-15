"""GitHub Copilot SDK adapter — programmatic surface over the FibreOps agents.

The GitHub Copilot SDK (``@github/copilot-sdk`` in TypeScript, equivalent
shape in Python) exposes a ``CopilotClient`` with ``create_session()`` and
``session.send_and_wait(prompt)``. This module mirrors that contract so that:

* a developer can drive the autonomous fibre-outage system from a VS Code
  Copilot extension or a Copilot CLI script using the same SDK they already
  know;
* the same handler that powers the dashboard also serves Copilot-style
  programmatic clients without duplicate code paths.

The client routes prompts based on shape:

* A JSON object that looks like a telemetry signal (has ``signal_type`` and
  ``node_id``) -> :func:`fibreops.orchestrator.handle_signal` so the full
  agent flow runs end-to-end and returns the same record the dashboard sees.
* A free-form natural language prompt -> a chat helper that surfaces the last
  N runs, optimiser summary and node topology so the LLM (or, in local mode,
  a deterministic responder) can answer status questions.

Sessions are in-memory; persistence is a future extension.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..mocks import load_json
from ..models import Severity, SignalType, TelemetrySignal
from ..observability import get_logger

logger = get_logger(__name__)

_RUNS_FILE = Path("state") / "runs.jsonl"
_SUGGESTIONS_FILE = Path("state") / "optimiser_suggestions.jsonl"


@dataclass
class FibreOpsCopilotResponse:
    """Response returned by :meth:`FibreOpsCopilotSession.send_and_wait`."""

    session_id: str
    kind: str  # "signal" or "chat"
    text: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "text": self.text,
            "data": self.data,
        }


@dataclass
class _SessionState:
    session_id: str
    turns: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class FibreOpsCopilotSession:
    """A single Copilot-style session bound to the FibreOps orchestrator."""

    def __init__(self, client: "FibreOpsCopilotClient", session_id: str) -> None:
        self._client = client
        self._state = _SessionState(session_id=session_id)
        self._closed = False

    @property
    def session_id(self) -> str:
        return self._state.session_id

    @property
    def turns(self) -> list[dict[str, Any]]:
        return list(self._state.turns)

    @property
    def closed(self) -> bool:
        return self._closed

    async def send_and_wait(self, prompt: str | dict[str, Any]) -> FibreOpsCopilotResponse:
        """Send a prompt and wait for the agent flow / chat response.

        The prompt may be:
        * a dict (or JSON string) shaped like a telemetry signal -
          ``{signal_id, node_id, signal_type, severity, ...}`` - in which
          case the full orchestrator runs and the response contains the
          incident record.
        * a free-form string - in which case the chat helper answers from
          the current state files (runs, optimiser, topology).
        """
        if self._closed:
            raise RuntimeError(f"Session {self.session_id} is closed")
        return await self._client._dispatch(self._state, prompt)

    async def close(self) -> None:
        self._closed = True


class FibreOpsCopilotClient:
    """Entry point - create sessions, then send prompts.

    Usage::

        client = FibreOpsCopilotClient()
        session = await client.create_session()
        response = await session.send_and_wait("What was the last incident?")
        print(response.text)
        await session.close()
    """

    def __init__(self, *, max_session_turns: int = 50) -> None:
        self._max_session_turns = max_session_turns
        self._sessions: dict[str, FibreOpsCopilotSession] = {}

    async def create_session(self) -> FibreOpsCopilotSession:
        session_id = f"cs-{uuid.uuid4().hex[:10]}"
        session = FibreOpsCopilotSession(self, session_id)
        self._sessions[session_id] = session
        logger.info("copilot session created", extra={"session_id": session_id})
        return session

    def get_session(self, session_id: str) -> Optional[FibreOpsCopilotSession]:
        return self._sessions.get(session_id)

    async def _dispatch(
        self, state: _SessionState, prompt: str | dict[str, Any]
    ) -> FibreOpsCopilotResponse:
        payload: Any = prompt
        if isinstance(prompt, str):
            stripped = prompt.strip()
            if stripped.startswith("{"):
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    payload = prompt

        if _looks_like_signal(payload):
            return await self._handle_signal_prompt(state, payload)  # type: ignore[arg-type]
        text_prompt = prompt if isinstance(prompt, str) else json.dumps(prompt)
        return await self._handle_chat_prompt(state, text_prompt)

    async def _handle_signal_prompt(
        self, state: _SessionState, payload: dict[str, Any]
    ) -> FibreOpsCopilotResponse:
        signal = _build_signal_from_payload(payload)
        # Lazy import so unit tests can patch the orchestrator.
        from .. import orchestrator

        record = await orchestrator.handle_signal(signal)
        text = _summarise_record(record)
        response = FibreOpsCopilotResponse(
            session_id=state.session_id,
            kind="signal",
            text=text,
            data=record,
        )
        self._append_turn(state, prompt=payload, response=response)
        return response

    async def _handle_chat_prompt(
        self, state: _SessionState, prompt: str
    ) -> FibreOpsCopilotResponse:
        text = _answer_from_state(prompt)
        response = FibreOpsCopilotResponse(
            session_id=state.session_id,
            kind="chat",
            text=text,
            data={"runs_loaded": _runs_count()},
        )
        self._append_turn(state, prompt=prompt, response=response)
        return response

    def _append_turn(
        self,
        state: _SessionState,
        *,
        prompt: Any,
        response: FibreOpsCopilotResponse,
    ) -> None:
        state.turns.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "prompt": prompt,
                "response": response.to_dict(),
            }
        )
        if len(state.turns) > self._max_session_turns:
            state.turns[: -self._max_session_turns] = []


# --- helpers ------------------------------------------------------------------


_SIGNAL_REQUIRED = {"signal_id", "node_id", "signal_type", "severity"}


def _looks_like_signal(payload: Any) -> bool:
    return isinstance(payload, dict) and _SIGNAL_REQUIRED.issubset(payload.keys())


def _build_signal_from_payload(payload: dict[str, Any]) -> TelemetrySignal:
    return TelemetrySignal(
        signal_id=str(payload["signal_id"]),
        node_id=str(payload["node_id"]),
        signal_type=SignalType(payload["signal_type"]),
        severity=Severity(payload["severity"]),
        measured_value=float(payload.get("measured_value", 0.0)),
        unit=str(payload.get("unit", "dBm")),
        raw=dict(payload.get("raw", {})),
    )


def _summarise_record(record: dict[str, Any]) -> str:
    incident_id = record.get("incident_id", "?")
    steps = {s.get("agent"): s for s in record.get("steps", [])}
    analysis = steps.get("IncidentAnalysisAgent", {}).get("output", {})
    coord = steps.get("NetOpsCoordinatorAgent", {})
    dispatch = steps.get("FieldDispatchAgent", {})
    severity = analysis.get("severity", record.get("signal", {}).get("severity", "?"))
    summary = analysis.get("summary", "")
    ticket = (coord.get("ticket") or {}).get("ticket_id", "-")
    decision = coord.get("decision", "")
    parts = [
        f"Incident {incident_id} [{severity}] - {summary}",
        f"Ticket: {ticket}",
        f"Coordinator: {decision}",
    ]
    if dispatch:
        parts.append(f"Dispatch: {dispatch.get('result','-')}")
    return "\n".join(parts)


def _runs_count() -> int:
    if not _RUNS_FILE.exists():
        return 0
    return sum(1 for _ in _RUNS_FILE.read_text(encoding="utf-8").splitlines() if _.strip())


def _load_recent_runs(limit: int = 5) -> list[dict[str, Any]]:
    if not _RUNS_FILE.exists():
        return []
    lines = [
        json.loads(l)
        for l in _RUNS_FILE.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    return list(reversed(lines))[:limit]


def _load_optimiser() -> dict[str, Any] | None:
    if not _SUGGESTIONS_FILE.exists():
        return None
    try:
        return json.loads(_SUGGESTIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _answer_from_state(prompt: str) -> str:
    """Deterministic, no-LLM chat responder backed by state files."""
    q = prompt.lower().strip()
    recent = _load_recent_runs()
    opt = _load_optimiser()

    if any(kw in q for kw in ("status", "what's happening", "current", "latest", "last")):
        if not recent:
            return (
                "No incidents recorded yet. Inject a telemetry signal "
                "via `python -m fibreops.demo` or the NOC console."
            )
        latest = recent[0]
        return _summarise_record(latest)

    if any(kw in q for kw in ("how many", "count")):
        return f"There are {_runs_count()} incident run(s) recorded in state/runs.jsonl."

    if "optimi" in q or "score" in q or "evaluat" in q:
        if not opt:
            return "No optimiser summary yet. Run `python -m fibreops.demo` or click `Run optimiser` in the UI."
        return (
            f"Average optimiser score: {opt.get('avg_score', '?')}\n"
            f"Runs evaluated: {len(opt.get('scores', []))}\n"
            f"Top suggestions: {', '.join(opt.get('suggestions', [])[:3]) or '-'}"
        )

    if "node" in q or "topology" in q:
        nodes = load_json("fibre_nodes.json")
        return "Known fibre nodes:\n" + "\n".join(
            f"  {n['node_id']:12s} {n['region']:12s} {n['site']:30s} customers={n['customers_served']}"
            for n in nodes
        )

    if "help" in q or q == "":
        return (
            "FibreOps Copilot chat - try:\n"
            "  status            - latest incident summary\n"
            "  how many runs     - count of incident records\n"
            "  optimiser         - last evaluation summary\n"
            "  nodes             - fibre node topology\n"
            "Or send a telemetry signal as a JSON object to trigger the full agent flow."
        )

    return (
        "I can answer 'status', 'how many runs', 'optimiser', 'nodes', or run a "
        "full agent flow if you pass a JSON telemetry signal. Ask 'help' for examples."
    )
