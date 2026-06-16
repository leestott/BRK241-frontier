"""One-command demo runner.

Usage:
  python -m fibreops.demo                     # full end-to-end demo (3 signals)
  python -m fibreops.demo --signals 5
  python -m fibreops.demo run --publish-eh    # publish to real Event Hub first
  python -m fibreops.demo publish             # create hosted agents in Foundry
  python -m fibreops.demo cleanup             # delete hosted agents in Foundry
  python -m fibreops.demo card                # show the latest Teams card payload
  python -m fibreops.demo backend             # show the resolved agent backend
  python -m fibreops.demo ui                  # launch the NOC web console (port 8800)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Force UTF-8 stdout on Windows so rich panels with unicode render correctly.
if sys.platform == "win32":
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from .config import get_settings
from .observability import init_observability
from .optimiser import run_optimisation
from .orchestrator import handle_signal
from .telemetry import generate_signals, publish_demo_signals

app = typer.Typer(add_completion=False, no_args_is_help=False)
console = Console(force_terminal=True, legacy_windows=False)


def _wait_for_mock(url: str, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health", timeout=1.5)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.4)
    return False


def _start_mock_d365() -> Optional[subprocess.Popen]:
    settings = get_settings()
    console.print(f"[cyan]Starting mock D365 Field Service on {settings.d365_mock_base_url}…[/cyan]")
    proc = subprocess.Popen(
        [sys.executable, "-m", "fibreops.mocks.d365_service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    if not _wait_for_mock(settings.d365_mock_base_url):
        console.print("[red]Mock D365 failed to start[/red]")
        proc.terminate()
        return None
    console.print("[green]Mock D365 is up.[/green]")
    return proc


def _stop_mock_d365(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.terminate()
        proc.wait(timeout=4)
    except Exception:
        proc.kill()


@app.command("run")
def run_demo(
    signals: int = typer.Option(3, help="Number of telemetry signals to inject"),
    publish_eh: bool = typer.Option(False, "--publish-eh", help="Publish to real Event Hub first"),
    serve_mock_d365: bool = typer.Option(True, "--serve-mock-d365/--no-serve-mock-d365"),
    skip_optimiser: bool = typer.Option(False, "--skip-optimiser"),
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Force agent backend: hosted | foundry | local (default: auto from FIBREOPS_AGENT_BACKEND)",
    ),
) -> None:
    """Run the end-to-end fibre-outage demo."""
    init_observability()
    settings = get_settings()
    if backend:
        os.environ["FIBREOPS_AGENT_BACKEND"] = backend
        get_settings.cache_clear()  # type: ignore[attr-defined]
        settings = get_settings()
    Path("state").mkdir(exist_ok=True)
    # Wipe per-demo trace files so the optimiser table is for THIS run only.
    for f in ("traces.jsonl", "runs.jsonl"):
        p = Path("state") / f
        if p.exists():
            p.unlink()

    console.rule("[bold blue]FibreOps — Autonomous Fibre Outage Response System")
    _print_config_panel(settings)

    mock_proc: Optional[subprocess.Popen] = None
    try:
        if serve_mock_d365:
            mock_proc = _start_mock_d365()
            if mock_proc is None:
                raise typer.Exit(code=1)

        sigs = generate_signals(count=signals)
        console.print(Panel(
            "\n".join(
                f"{s.signal_id}  {s.severity.value:8s}  {s.signal_type.value:18s}  node={s.node_id}"
                for s in sigs
            ),
            title=f"Generated {len(sigs)} telemetry signals",
            border_style="cyan",
        ))

        if publish_eh:
            if not settings.event_hub_enabled:
                console.print("[red]--publish-eh requested but EVENT_HUB_FQDN is not set[/red]")
                raise typer.Exit(code=1)
            sent = asyncio.run(publish_demo_signals(sigs))
            console.print(f"[green]Published {sent} signals to Event Hub[/green]")

        async def _drive() -> list[dict]:
            results = []
            for s in sigs:
                console.rule(f"[bold magenta]Signal {s.signal_id} → orchestrator")
                result = await handle_signal(s)
                _render_run(result)
                results.append(result)
            return results

        runs = asyncio.run(_drive())

        if not skip_optimiser and settings.optimiser_enabled:
            console.rule("[bold green]Agent Optimiser")
            opt = run_optimisation()
            _render_optimiser(opt)

        console.rule("[bold blue]Demo complete")
        console.print(f"Runs persisted: [cyan]state/runs.jsonl[/cyan]  ({len(runs)} record(s))")
        console.print("Traces:         [cyan]state/traces.jsonl[/cyan]")
        console.print("D365 store:     [cyan]state/d365_store.json[/cyan]")
        console.print("Teams outbox:   [cyan]state/teams_outbox.jsonl[/cyan] (if webhook not configured)")
    finally:
        _stop_mock_d365(mock_proc)


@app.command("publish")
def publish_hosted_agents() -> None:
    """Publish the three role agents as hosted Prompt Agents in Microsoft Foundry."""
    init_observability()
    settings = get_settings()
    if not settings.foundry_enabled:
        console.print("[red]AZURE_AI_PROJECT_ENDPOINT is not set — cannot publish.[/red]")
        raise typer.Exit(code=1)
    from .agents.publisher import AGENT_NAMES, publish_all

    console.rule("[bold blue]Publishing hosted Foundry agents")
    console.print(f"Project endpoint: [cyan]{settings.azure_ai_project_endpoint}[/cyan]")
    console.print(f"Model deployment: [cyan]{settings.azure_ai_model_deployment}[/cyan]")
    console.print(f"Agents to create: [cyan]{', '.join(AGENT_NAMES.values())}[/cyan]\n")
    try:
        registry = publish_all()
    except Exception as exc:
        console.print(f"[red]Publish failed: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    t = Table(title="Hosted agents", show_lines=False)
    t.add_column("Role", style="cyan")
    t.add_column("Foundry agent name")
    t.add_column("Version", justify="right")
    for role, entry in registry.items():
        t.add_row(role, entry["agent_name"], entry["version"])
    console.print(t)
    console.print(
        "\n[green]Done.[/green] Subsequent `python -m fibreops.demo run` "
        "invocations will auto-detect these and use the hosted path."
    )


@app.command("cleanup")
def cleanup_hosted_agents(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete the hosted Prompt Agents and wipe the local registry."""
    init_observability()
    settings = get_settings()
    if not settings.foundry_enabled:
        console.print("[red]AZURE_AI_PROJECT_ENDPOINT is not set — cannot cleanup.[/red]")
        raise typer.Exit(code=1)
    from .agents.publisher import AGENT_NAMES, cleanup_all

    if not yes:
        confirmed = typer.confirm(
            f"This will delete {len(AGENT_NAMES)} hosted agents from Foundry. Continue?"
        )
        if not confirmed:
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)

    removed = cleanup_all()
    if removed:
        console.print(f"[green]Deleted:[/green] {', '.join(removed)}")
    else:
        console.print("[yellow]Nothing to delete.[/yellow]")


@app.command("serve-hosted")
def serve_hosted(
    port: int = typer.Option(None, "--port", "-p", help="Override the listen port"),
) -> None:
    """Run the hosted-agent container entrypoint locally (serves /responses on 8088)."""
    init_observability()
    from .agents.hosted_app import build_host_server

    settings = get_settings()
    listen = port or settings.hosted_agent_port
    console.rule("[bold blue]FibreOps hosted agent (local)")
    console.print(
        f"Serving OpenAI /responses + /readiness on [cyan]http://0.0.0.0:{listen}[/cyan]\n"
        "POST /responses with {\"input\": \"...\", \"stream\": false}."
    )
    server = build_host_server()
    server.run(host="0.0.0.0", port=listen)


@app.command("deploy-hosted")
def deploy_hosted(
    image: str = typer.Option(None, "--image", "-i", help="ACR image:tag (else FIBREOPS_HOSTED_IMAGE)"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Return immediately without polling status"),
) -> None:
    """Register the containerised hosted agent in Foundry Agent Service (V1Preview)."""
    init_observability()
    settings = get_settings()
    if not settings.foundry_enabled:
        console.print("[red]AZURE_AI_PROJECT_ENDPOINT is not set — cannot deploy.[/red]")
        raise typer.Exit(code=1)
    from .agents.deploy import deploy_hosted_agent

    console.rule("[bold blue]Deploying FibreOps hosted agent")
    console.print(f"Project endpoint: [cyan]{settings.azure_ai_project_endpoint}[/cyan]")
    console.print(f"Agent name:       [cyan]{settings.hosted_agent_name}[/cyan]")
    console.print(
        f"Sandbox size:     [cyan]{settings.hosted_agent_cpu} vCPU / {settings.hosted_agent_memory}[/cyan]\n"
    )
    try:
        result = deploy_hosted_agent(image=image, wait=not no_wait)
    except Exception as exc:
        console.print(f"[red]Deploy failed: {exc}[/red]")
        raise typer.Exit(code=2) from exc

    status = result.get("status", "unknown")
    colour = "green" if status == "active" else "yellow" if status != "failed" else "red"
    t = Table(show_header=False, box=None)
    t.add_row("Agent", result["agent_name"])
    t.add_row("Version", str(result.get("version")))
    t.add_row("Image", result["image"])
    t.add_row("Status", f"[{colour}]{status}[/{colour}]")
    if result.get("error"):
        t.add_row("Error", str(result["error"]))
    console.print(Panel(t, title="Hosted agent deployment", border_style=colour))
    if status == "active":
        console.print(
            "\n[green]Active.[/green] Invoke via "
            "`project.get_openai_client(agent_name=...).responses.create(input=...)`."
        )


@app.command("backend")
def show_backend() -> None:
    """Print the resolved agent backend for the current configuration."""
    settings = get_settings()
    from .agents.factory import _resolve_backend
    from .agents.publisher import load_registry

    backend = _resolve_backend(None)
    registry = load_registry()
    t = Table(show_header=False, box=None)
    t.add_row("Configured backend",
              settings.agent_backend if settings.agent_backend != "auto" else "[yellow]auto[/yellow]")
    t.add_row("Resolved backend", f"[bold green]{backend}[/bold green]")
    t.add_row("Foundry endpoint",
              settings.azure_ai_project_endpoint or "[yellow]not set[/yellow]")
    t.add_row("Model deployment", settings.azure_ai_model_deployment)
    t.add_row("Hosted registry",
              "✓ present" if registry else "[yellow]missing — run `publish` first[/yellow]")
    if registry:
        for role, entry in registry.items():
            t.add_row(f"  └ {role}", f"{entry['agent_name']} v{entry['version']}")
    console.print(Panel(t, title="Agent backend resolution", border_style="blue"))


@app.command("card")
def show_card(
    index: int = typer.Option(-1, "--index", help="Card index (negative = from end)"),
) -> None:
    """Show the most recent Teams Adaptive Card payload (from the local outbox).

    Useful when the live demo has no Teams webhook configured: the same JSON
    that would have been posted to a channel is appended to
    ``state/teams_outbox.jsonl`` — this command renders it.
    """
    outbox = Path("state/teams_outbox.jsonl")
    if not outbox.exists():
        console.print("[red]No outbox found — run the demo first.[/red]")
        raise typer.Exit(code=1)
    lines = [l for l in outbox.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        console.print("[red]Outbox is empty.[/red]")
        raise typer.Exit(code=1)
    try:
        record = json.loads(lines[index])
    except (IndexError, json.JSONDecodeError) as exc:
        console.print(f"[red]Could not read card at index {index}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    header = Table(show_header=False, box=None)
    header.add_row("Posted at", record.get("ts", "?"))
    header.add_row("Webhook target", record.get("target", "outbox"))
    header.add_row("Card kind", record.get("kind", "?"))
    console.print(Panel(header, title=f"Teams card #{index} (of {len(lines)})", border_style="cyan"))
    payload = record.get("payload", record)
    console.print(Panel(
        Syntax(json.dumps(payload, indent=2), "json", theme="ansi_dark", word_wrap=True),
        title="Adaptive Card JSON (paste into https://adaptivecards.io/designer to preview)",
        border_style="green",
    ))


@app.command("chat")
def chat_command(
    prompt: str = typer.Argument(..., help="Prompt to send. JSON signal → full agent flow; free text → chat."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Reuse an existing session id"),
) -> None:
    """Send a prompt through the FibreOps Copilot SDK adapter.

    Mirrors the GitHub Copilot SDK ``CopilotClient.create_session().send_and_wait()``
    contract, so external Copilot apps (CLI, VS Code extensions, hosted bots)
    can drive the same agent system from any language with an HTTP client.
    """
    init_observability()
    from .sdk import FibreOpsCopilotClient

    async def _run() -> dict:
        client = FibreOpsCopilotClient()
        session = await client.create_session()
        if session_id:
            # Demo convenience — surface that we created a fresh in-memory
            # session (real persistence is a future extension).
            console.print(f"[yellow]Note:[/yellow] in-memory sessions; new id {session.session_id}")
        resp = await session.send_and_wait(prompt)
        await session.close()
        return resp.to_dict()

    result = asyncio.run(_run())
    console.print(
        Panel(
            result["text"],
            title=f"Copilot SDK · session={result['session_id']} · kind={result['kind']}",
            border_style="cyan",
        )
    )


@app.command("publish-m365")
def publish_m365_command(
    out_dir: Path = typer.Option(
        Path("dist/m365"), "--out", help="Directory that will receive the .zip"
    ),
) -> None:
    """Build a Microsoft 365 Copilot declarative agent package.

    Writes:
      ``<out>/declarativeAgent.json``   - the declarative agent definition
      ``<out>/manifest.json``           - the Teams app manifest wrapper
      ``<out>/color.png``               - 192x192 colour icon
      ``<out>/outline.png``             - 32x32 outline icon
      ``<out>/fibreops-copilot.zip``    - ready to sideload via Teams Admin /
                                          M365 Admin Center

    Set ``M365_ACTION_BASE_URL`` to the public URL of your FibreOps API
    so the declarative agent's action plugin can call back into ``/sdk/chat``.
    """
    init_observability()
    from .dist.m365 import build_m365_package

    artefacts = build_m365_package(out_dir)
    t = Table(title="Microsoft 365 Copilot package", show_lines=False)
    t.add_column("Artefact", style="cyan")
    t.add_column("Path")
    for name, path in artefacts.items():
        t.add_row(name, str(path))
    console.print(t)
    settings = get_settings()
    if not settings.m365_action_base_url:
        console.print(
            "[yellow]Heads up:[/yellow] M365_ACTION_BASE_URL is not set, so "
            "the declarative agent action plugin will point at a placeholder. "
            "Set it to the public URL of your FibreOps API before sideloading."
        )
    else:
        console.print(f"[green]Action base URL[/green] · {settings.m365_action_base_url}")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """If no subcommand was given, default to ``run`` with its defaults."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(run_demo)


@app.command("ui")
def ui_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8800, "--port", help="Listen port"),
) -> None:
    """Launch the FibreOps NOC console (FastAPI + HTMX) on http://HOST:PORT/.

    The console spawns the mock D365 service in-process so **Inject signal**
    works without a separate process. Press Ctrl+C to stop.
    """
    os.environ.setdefault("FIBREOPS_UI_HOST", host)
    os.environ["FIBREOPS_UI_PORT"] = str(port)
    console.print(
        Panel(
            f"[bold]FibreOps NOC Console[/bold]\nhttp://{host}:{port}/\nbackend: [cyan]{get_settings().agent_backend}[/cyan]",
            border_style="cyan",
        )
    )
    from .ui.app import main as ui_main

    ui_main()


# Known subcommands — anything else on argv[1] means the user wants `run` with flags.
_SUBCOMMANDS = {"run", "publish", "cleanup", "card", "backend", "ui", "chat", "publish-m365"}


def _rewrite_default_argv(argv: list[str]) -> list[str]:
    """Insert ``run`` when the user passed flags without a subcommand.

    ``python -m fibreops.demo --signals 3`` -> ``python -m fibreops.demo run --signals 3``
    """
    if len(argv) <= 1:
        return argv
    first = argv[1]
    if first in _SUBCOMMANDS:
        return argv
    if first in {"--help", "-h"}:
        return argv
    return [argv[0], "run", *argv[1:]]


def _print_config_panel(settings) -> None:
    from .agents.factory import _resolve_backend
    from .agents.publisher import load_registry

    backend = _resolve_backend(None)
    summary = Table(show_header=False, box=None)
    summary.add_row("Agent backend", f"[bold]{backend}[/bold]")
    summary.add_row(
        "Foundry endpoint",
        settings.azure_ai_project_endpoint or "[yellow]not configured (local agents)[/yellow]",
    )
    summary.add_row(
        "Hosted agents",
        "✓ published" if load_registry() else "[yellow]not published[/yellow]",
    )
    summary.add_row(
        "Event Hub",
        f"{settings.event_hub_fqdn}/{settings.event_hub_name}"
        if settings.event_hub_enabled
        else "[yellow]not configured (in-process generator)[/yellow]",
    )
    summary.add_row(
        "Teams webhook",
        "configured" if settings.teams_enabled else "[yellow]not configured (logging to outbox)[/yellow]",
    )
    summary.add_row("Mock D365 base", settings.d365_mock_base_url)
    summary.add_row("Auto dispatch", str(settings.auto_dispatch))
    console.print(Panel(summary, title="Configuration", border_style="blue"))


def _render_run(run: dict) -> None:
    sig = run["signal"]
    console.print(
        f"[bold]Node:[/bold] {sig['node_id']}  "
        f"[bold]Type:[/bold] {sig['signal_type']}  "
        f"[bold]Severity:[/bold] {sig['severity']}"
    )
    for step in run["steps"]:
        agent = step["agent"]
        if agent == "IncidentAnalysisAgent":
            out = step["output"]
            console.print(Panel(json.dumps(out, indent=2), title=f"🧠 {agent}", border_style="cyan"))
        elif agent == "NetOpsCoordinatorAgent":
            ticket = step.get("ticket") or {}
            console.print(Panel(
                f"Decision: {step['decision']}\nTicket:   {ticket.get('ticket_id','-')}  status={ticket.get('status','-')}",
                title=f"🛰️ {agent}", border_style="green"))
        elif agent == "FieldDispatchAgent":
            console.print(Panel(step["result"], title=f"🚐 {agent}", border_style="magenta"))


def _render_optimiser(opt: dict) -> None:
    table = Table(title=f"Per-run scores (avg {opt['avg_score']})", show_lines=False)
    table.add_column("Run", style="cyan")
    table.add_column("Incident")
    table.add_column("Score", justify="right")
    table.add_column("Failed criteria")
    for s in opt["scores"]:
        failed = ", ".join(k for k, v in s["criteria"].items() if v < 1.0) or "[green]none[/green]"
        table.add_row(s["run_id"], s["incident_id"], f"{s['score']:.2f}", failed)
    console.print(table)
    if opt["suggestions"]:
        sg = Table(title="Improvement suggestions", show_lines=True)
        sg.add_column("Target", style="yellow")
        sg.add_column("Change")
        sg.add_column("Evidence", style="dim")
        for s in opt["suggestions"]:
            sg.add_row(s["target"], s["change"], s["evidence"])
        console.print(sg)
    else:
        console.print("[green]No suggestions — all runs passed every rubric criterion.[/green]")


if __name__ == "__main__":
    sys.argv = _rewrite_default_argv(sys.argv)
    app()
