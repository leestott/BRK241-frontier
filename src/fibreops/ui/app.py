"""FibreOps Operations Console — FastAPI + HTMX + Tailwind.

* Reads agent runs from ``state/runs.jsonl`` and optimiser summary from
  ``state/optimiser_suggestions.jsonl``.
* Spins up the mock D365 service in-process on startup (uvicorn ``Server``)
  so the **Inject Signal** button works without any extra processes.
* Exposes HTMX-polled partials so the dashboard refreshes itself without
  any client-side JavaScript framework.

Layout::

  GET  /                              page shell
  GET  /partials/runs                 recent runs list
  GET  /partials/optimiser            optimiser summary
  GET  /partials/teams                Teams outbox cards
  GET  /partials/voice                Voice Live outbox utterances
  GET  /partials/run/{run_id}         single-run detail timeline
  GET  /partials/sim                  simulate-toggle button state
  POST /actions/inject                inject n synthetic signals
  POST /actions/optimise              run the optimiser
  POST /actions/voice                 speak status for the latest incident
  POST /actions/reset                 truncate state/* files
  POST /actions/simulate/{on|off}     toggle continuous injection
  GET  /api/runs                      JSON list (programmatic clients)
  GET  /api/optimiser                 JSON summary
  GET  /healthz                       liveness probe
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import get_settings
from ..observability import get_logger, init_observability

logger = get_logger(__name__)

PKG_DIR = Path(__file__).parent
TEMPLATES_DIR = PKG_DIR / "templates"
STATIC_DIR = PKG_DIR / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

STATE_DIR = Path("state")
RUNS_FILE = STATE_DIR / "runs.jsonl"
SUGGESTIONS_FILE = STATE_DIR / "optimiser_suggestions.jsonl"
TRACES_FILE = STATE_DIR / "traces.jsonl"
TEAMS_OUTBOX = STATE_DIR / "teams_outbox.jsonl"
VOICE_OUTBOX = STATE_DIR / "voice_outbox.jsonl"
IQ_LOOKUPS = STATE_DIR / "iq_lookups.jsonl"


# --- state-file readers -------------------------------------------------------

def _load_runs(limit: int = 50) -> list[dict[str, Any]]:
    if not RUNS_FILE.exists():
        return []
    runs = [
        json.loads(line)
        for line in RUNS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(runs))[:limit]


def _load_optimiser() -> dict[str, Any] | None:
    if not SUGGESTIONS_FILE.exists():
        return None
    try:
        return json.loads(SUGGESTIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_teams_outbox(limit: int = 10) -> list[dict[str, Any]]:
    if not TEAMS_OUTBOX.exists():
        return []
    cards = [
        json.loads(line)
        for line in TEAMS_OUTBOX.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(cards))[:limit]


def _load_voice_outbox(limit: int = 10) -> list[dict[str, Any]]:
    if not VOICE_OUTBOX.exists():
        return []
    utterances = [
        json.loads(line)
        for line in VOICE_OUTBOX.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(utterances))[:limit]


def _load_iq_lookups(limit: int = 10) -> list[dict[str, Any]]:
    if not IQ_LOOKUPS.exists():
        return []
    lookups = [
        json.loads(line)
        for line in IQ_LOOKUPS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return list(reversed(lookups))[:limit]


def _flatten_teams_card(card: dict[str, Any]) -> dict[str, Any]:
    """Pull the human-readable fields out of an Adaptive Card payload."""
    try:
        body = card["attachments"][0]["content"]["body"]
        title = body[0]["text"]
        summary = body[1]["text"] if len(body) > 1 else ""
        facts = body[2].get("facts", []) if len(body) > 2 else []
    except (KeyError, IndexError, TypeError):
        return {"title": "(unparseable card)", "summary": "", "facts": []}
    return {"title": title, "summary": summary, "facts": facts}


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """Decorate a raw run with display-friendly fields."""
    sig = run.get("signal", {})
    ctx = run.get("node_context", {})
    steps = {s.get("agent"): s for s in run.get("steps", [])}
    analysis = steps.get("IncidentAnalysisAgent", {}).get("output", {})
    coord = steps.get("NetOpsCoordinatorAgent", {})
    dispatch = steps.get("FieldDispatchAgent", {})
    severity = analysis.get("severity") or sig.get("severity", "low")
    dispatched = bool(dispatch and "DISPATCHED" in str(dispatch.get("result", "")))
    engineer = None
    eta = None
    if dispatched:
        meta = dispatch.get("metadata", {}).get("dispatch", {})
        engineer = meta.get("engineer_name")
        eta = meta.get("eta_minutes")
    return {
        "run_id": run.get("run_id"),
        "incident_id": run.get("incident_id"),
        "started_at": run.get("started_at", ""),
        "node_id": sig.get("node_id"),
        "region": ctx.get("region"),
        "site": ctx.get("site"),
        "customers_served": ctx.get("customers_served", 0),
        "signal_type": sig.get("signal_type"),
        "severity_input": sig.get("severity"),
        "severity": severity,
        "summary": analysis.get("summary", ""),
        "ticket": coord.get("ticket"),
        "dispatched": dispatched,
        "engineer_name": engineer,
        "eta_minutes": eta,
        "raw": run,
    }


# --- simulation loop ----------------------------------------------------------


class _SimState:
    running: bool = False
    task: asyncio.Task | None = None
    interval: float = 4.0


_sim = _SimState()


async def _sim_loop() -> None:
    from ..orchestrator import handle_signal
    from ..telemetry import generate_signals

    while _sim.running:
        try:
            for sig in generate_signals(count=1, include_critical=False):
                await handle_signal(sig)
        except Exception as exc:  # pragma: no cover - logged for demo visibility
            logger.warning("sim loop error: %s", exc)
        try:
            await asyncio.sleep(_sim.interval)
        except asyncio.CancelledError:  # pragma: no cover
            break


# --- mock D365 lifecycle ------------------------------------------------------


async def _wait_for_d365(base_url: str, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=0.5) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                r = await client.get(f"{base_url}/health")
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.1)
    return False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    init_observability()
    settings = get_settings()
    d365_server: uvicorn.Server | None = None
    d365_task: asyncio.Task | None = None
    spawn_d365 = os.getenv("FIBREOPS_UI_SKIP_MOCK_D365", "").lower() not in ("1", "true", "yes")
    if spawn_d365:
        from ..mocks.d365_service import app as d365_app

        cfg = uvicorn.Config(
            d365_app,
            host="127.0.0.1",
            port=settings.d365_mock_port,
            log_level="warning",
            access_log=False,
        )
        d365_server = uvicorn.Server(cfg)
        d365_task = asyncio.create_task(d365_server.serve())
        if not await _wait_for_d365(settings.d365_mock_base_url):
            logger.warning("mock D365 did not become ready in time")
    try:
        yield
    finally:
        _sim.running = False
        if _sim.task:
            _sim.task.cancel()
            with contextlib.suppress(BaseException):
                await _sim.task
            _sim.task = None
        if d365_server:
            d365_server.should_exit = True
        if d365_task:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(d365_task, timeout=3.0)


# --- app factory --------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="FibreOps Operations Console", lifespan=_lifespan)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        settings = get_settings()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "backend": settings.agent_backend,
                "auto_dispatch": settings.auto_dispatch,
                "foundry_endpoint": settings.azure_ai_project_endpoint or "(not configured)",
                "teams_enabled": settings.teams_enabled,
                "voice_live_enabled": settings.voice_live_enabled,
                "netops_routine": settings.netops_routine_enabled,
                "foundry_iq_enabled": settings.foundry_iq_enabled,
                "web_iq_enabled": settings.web_iq_enabled,
                "work_iq_enabled": settings.work_iq_enabled,
                "sim_running": _sim.running,
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/partials/runs", response_class=HTMLResponse)
    async def partial_runs(request: Request) -> HTMLResponse:
        runs = [_run_summary(r) for r in _load_runs()]
        return templates.TemplateResponse(
            request, "partials/runs.html", {"runs": runs}
        )

    @app.get("/partials/optimiser", response_class=HTMLResponse)
    async def partial_optimiser(request: Request) -> HTMLResponse:
        summary = _load_optimiser()
        criterion_stats: list[dict[str, Any]] = []
        if summary and summary.get("scores"):
            agg: dict[str, list[float]] = {}
            for s in summary["scores"]:
                for k, v in s.get("criteria", {}).items():
                    agg.setdefault(k, []).append(v)
            for name, values in sorted(agg.items()):
                avg = sum(values) / len(values)
                criterion_stats.append(
                    {"name": name, "avg": avg, "pct": int(round(avg * 100))}
                )
        return templates.TemplateResponse(
            request,
            "partials/optimiser.html",
            {"summary": summary, "criteria": criterion_stats},
        )

    @app.get("/partials/teams", response_class=HTMLResponse)
    async def partial_teams(request: Request) -> HTMLResponse:
        cards = [_flatten_teams_card(c) for c in _load_teams_outbox()]
        return templates.TemplateResponse(
            request, "partials/teams.html", {"cards": cards}
        )

    @app.get("/partials/voice", response_class=HTMLResponse)
    async def partial_voice(request: Request) -> HTMLResponse:
        utterances = _load_voice_outbox()
        return templates.TemplateResponse(
            request, "partials/voice.html", {"utterances": utterances}
        )

    @app.get("/partials/iq", response_class=HTMLResponse)
    async def partial_iq(request: Request) -> HTMLResponse:
        lookups = _load_iq_lookups()
        return templates.TemplateResponse(
            request, "partials/iq.html", {"lookups": lookups}
        )

    @app.post("/actions/voice", response_class=HTMLResponse)
    async def action_voice(request: Request) -> HTMLResponse:
        """Speak a status update for the most recent incident."""
        from ..tools import speak_status_update

        runs = _load_runs(limit=1)
        if runs:
            r = _run_summary(runs[0])
            if r.get("dispatched"):
                speak_status_update(
                    incident_id=r["incident_id"],
                    phrase="engineer_dispatched",
                    severity=r.get("severity", "medium"),
                    engineer=r.get("engineer_name"),
                    eta=r.get("eta_minutes"),
                )
            else:
                analysis = (
                    r["raw"].get("steps", [{}])[0].get("output", {}) if r.get("raw") else {}
                )
                speak_status_update(
                    incident_id=r["incident_id"],
                    phrase="outage_detected",
                    severity=r.get("severity", "medium"),
                    node_id=r.get("node_id"),
                    region=r.get("region"),
                    customers=r.get("customers_served", 0),
                    probable_cause=analysis.get("probable_cause", "investigating"),
                )
        utterances = _load_voice_outbox()
        return templates.TemplateResponse(
            request, "partials/voice.html", {"utterances": utterances}
        )

    @app.get("/partials/sim", response_class=HTMLResponse)
    async def partial_sim(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "partials/sim_toggle.html",
            {"sim_running": _sim.running},
        )

    @app.get("/partials/run/{run_id}", response_class=HTMLResponse)
    async def partial_run(request: Request, run_id: str) -> HTMLResponse:
        match = next(
            (r for r in _load_runs(limit=500) if r.get("run_id") == run_id), None
        )
        decorated = _run_summary(match) if match else None
        return templates.TemplateResponse(
            request,
            "partials/run_detail.html",
            {"run": decorated},
        )

    @app.post("/actions/inject", response_class=HTMLResponse)
    async def action_inject(
        request: Request, count: int = 1, critical: bool = False
    ) -> HTMLResponse:
        # Import lazily so we pick up monkeypatched handle_signal during tests.
        from .. import orchestrator
        from ..telemetry import generate_signals

        count = max(1, min(10, int(count)))
        for sig in generate_signals(count=count, include_critical=critical):
            await orchestrator.handle_signal(sig)
        runs = [_run_summary(r) for r in _load_runs()]
        return templates.TemplateResponse(
            request, "partials/runs.html", {"runs": runs}
        )

    @app.post("/actions/optimise", response_class=HTMLResponse)
    async def action_optimise(request: Request) -> HTMLResponse:
        from ..optimiser import run_optimisation

        run_optimisation()
        return await partial_optimiser(request)

    @app.post("/actions/reset", response_class=HTMLResponse)
    async def action_reset(request: Request) -> HTMLResponse:
        for path in (RUNS_FILE, SUGGESTIONS_FILE, TEAMS_OUTBOX, TRACES_FILE, VOICE_OUTBOX, IQ_LOOKUPS):
            if path.exists():
                path.unlink()
        d365_store = STATE_DIR / "d365_store.json"
        if d365_store.exists():
            d365_store.unlink()
        return templates.TemplateResponse(
            request, "partials/runs.html", {"runs": []}
        )

    @app.post("/actions/simulate/{state}", response_class=HTMLResponse)
    async def action_simulate(request: Request, state: str) -> HTMLResponse:
        if state not in ("on", "off"):
            raise HTTPException(400, "state must be 'on' or 'off'")
        if state == "on" and not _sim.running:
            _sim.running = True
            _sim.task = asyncio.create_task(_sim_loop())
        elif state == "off":
            _sim.running = False
            if _sim.task:
                _sim.task.cancel()
                with contextlib.suppress(BaseException):
                    await _sim.task
                _sim.task = None
        return templates.TemplateResponse(
            request,
            "partials/sim_toggle.html",
            {"sim_running": _sim.running},
        )

    @app.get("/api/runs")
    async def api_runs(limit: int = 50) -> JSONResponse:
        return JSONResponse({"runs": _load_runs(limit=limit)})

    @app.get("/api/optimiser")
    async def api_optimiser() -> JSONResponse:
        return JSONResponse({"summary": _load_optimiser()})

    @app.post("/sdk/chat")
    async def sdk_chat(request: Request) -> JSONResponse:
        """Copilot SDK-shaped endpoint: ``{prompt: str}`` -> response.

        Lets external Copilot apps (CLI, VS Code extensions, M365 declarative
        agent action plugins) drive the FibreOps system without speaking
        Foundry directly. Sessions are per-request unless ``session_id`` is
        passed; the client maintains in-memory turn history.
        """
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(400, "prompt is required")
        from ..sdk import FibreOpsCopilotClient

        client = FibreOpsCopilotClient()
        session = await client.create_session()
        resp = await session.send_and_wait(prompt)
        await session.close()
        return JSONResponse(resp.to_dict())

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    port = int(os.getenv("FIBREOPS_UI_PORT", "8800"))
    host = os.getenv("FIBREOPS_UI_HOST", "127.0.0.1")
    logger.info(
        "starting FibreOps console on http://%s:%s (backend=%s, mock_d365=%s)",
        host,
        port,
        settings.agent_backend,
        settings.d365_mock_base_url,
    )
    uvicorn.run(
        "fibreops.ui.app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
