> FibreOps — Autonomous Fibre Outage Response System (BRK241)

> Reference implementation of the **Autonomous Fibre Outage Response** demo
> built on **Microsoft Foundry Agent Service**, the **Microsoft Agent
> Framework**, **Azure Event Hubs**, **Microsoft Teams**, and a **mocked
> Dynamics 365 Field Service** (because most demo tenants don't have D365
> Field Service provisioned).

## Architecture (textual)

```
 OLT telemetry ──▶ Event Hub ──▶ Orchestrator ──┬─▶ IncidentAnalysisAgent
                                                ├─▶ NetOpsCoordinatorAgent ──▶ D365 (mock) + Teams (real)
                                                └─▶ FieldDispatchAgent     ──▶ D365 booking + Teams update
                                                          │
                                                          ▼
                                              OpenTelemetry → Application Insights
                                                          │
                                                          ▼
                                              Optimiser (rubric → suggestions)
```

Agents are **hosted Prompt Agents** in Microsoft Foundry Agent Service
(`agent_framework_foundry.FoundryAgent`). Tools are typed Python functions
the runtime supplies to the hosted definition.

### Architecture diagram

![FibreOps architecture diagram](./docs/images/architecture-diagram.png)

> Regenerate with `python scripts/gen_architecture_diagram.py`
> (writes `docs/images/architecture-diagram.png`).

### Services architecture (swim-lane)

A layered, service-oriented view of FibreOps. Read it left-to-right across four
lanes — **Client → Application Layer → Agent Framework Orchestration →
External Services** — with an Azure services band along the bottom and
managed-identity security/governance applied across every component.

![FibreOps services architecture diagram](./docs/images/services-architecture.png)

**How to read it.** Solid arrows are the **orchestration flow** (control passing
between FibreOps components); dashed arrows are **external data flow** (calls
that leave the process for an Azure or Microsoft 365 service).

| # | Node | Lane | Role |
| --- | --- | --- | --- |
| ① | NOC Operator / Foundry Playground | Client | Human-in-the-loop driving the demo from the browser, CLI, or Foundry Playground |
| ② | NOC Console (FastAPI + HTMX · Demo CLI) | Application | Entry point — serves the dashboard and exposes the run/optimiser JSON API |
| ③ | Telemetry ingest (Event Hub · generator) | Application | Real Azure Event Hubs consumer **or** the deterministic synthetic OLT signal generator |
| ④ | `IncidentAnalysisAgent` | Orchestration | Classifies severity, finds root cause, pulls the right SOP |
| ⑤ | `NetOpsCoordinatorAgent` | Orchestration | Files the D365 incident and posts the Teams outage notice |
| ⑥ | `FieldDispatchAgent` | Orchestration | Selects the best engineer, books the resource, updates Teams |
| ⑦ | Integration tools (`FunctionTool`) | Orchestration | Typed Python tools the runtime supplies to the hosted agents |
| ⑧ | Knowledge — SOPs + topology | Orchestration | Retrieval over standard operating procedures and the fibre node graph |
| ⑨ | Web IQ / Work IQ search | Orchestration | Grounding against Microsoft 365 / web sources |
| ⑩ | Microsoft Teams | External | Adaptive Card outage notices + status updates via Incoming Webhook |
| ⑪ | Dynamics 365 Field Service (mock) | External | Dataverse-shaped REST for incidents and bookable-resource bookings |
| ⑫ | Azure AI Voice Live | External | SSML status announcements, voice/prosody chosen per severity |
| ⑬ | Microsoft Foundry Agent Service | External | Hosts the published Prompt Agents that back ④–⑥ |

**End-to-end flow.** A telemetry signal arrives at ③ (Event Hub or generator)
and the **Orchestrator** (`handle_signal`, lane 3) drives it through the agent
pipeline ④ → ⑤ → ⑥. Each agent calls the typed tools ⑦–⑨ — SOP/topology
lookups and Web/Work IQ grounding — then fans out to the external services:
ticket and booking to **D365** ⑪, Adaptive Cards to **Microsoft Teams** ⑩, and
spoken updates through **Azure AI Voice Live** ⑫. The agents themselves are
hosted Prompt Agents in **Microsoft Foundry Agent Service** ⑬. The operator ①
sees everything live in the NOC Console ②.

> Regenerate with `python scripts/gen_services_architecture.py`
> (writes `docs/images/services-architecture.png`).

## Capabilities

- 📡 **Telemetry** — synthetic IoT signal generator + real Azure Event Hubs
  producer/consumer with `DefaultAzureCredential`.
- 🧠 **Agent system** — three role-specialised agents (Incident Analysis,
  NetOps Coordinator, Field Dispatch). Three pluggable backends share one
  `.run()` contract:
  - **`hosted`** — `agent_framework_foundry.FoundryAgent` connected to a
    Prompt Agent published to **Microsoft Foundry Agent Service** (the
    architecture-diagram path).
  - **`foundry`** — `agent_framework.Agent` + `FoundryChatClient` with the
    definition resolved locally (useful while iterating on prompts).
  - **`local`** — deterministic `LocalAgent` so the demo runs without any
    Azure credentials.
- 🛠️ **Tools** — knowledge (SOPs + topology), D365 ticketing (Dataverse-shaped
  REST), Microsoft Teams Adaptive Cards, engineer dispatch, procedural memory.
- 📨 **Teams** — Adaptive Card outage notices + status updates via Incoming
  Webhook (any unconfigured channel is logged to `state/teams_outbox.jsonl`).
- 🎟️ **D365 mock** — FastAPI service mimicking `/api/data/v9.2/incidents` and
  `/api/data/v9.2/bookableresourcebookings`. Swap `D365_MOCK_BASE_URL` to a
  real Dataverse environment with no code changes.
- 🔭 **Observability** — JSON structured logs + OpenTelemetry spans persisted
  to `state/traces.jsonl`. Set `APPLICATIONINSIGHTS_CONNECTION_STRING` to
  ship to Application Insights.
- 🔁 **Optimiser** — rubric-based evaluation of every run + actionable
  improvement suggestions. Drop-in target for `FoundryEvals` in production.

![Architecture](./docs/images/architecture.png)  

## NOC console preview

The deployed application is a tactical Network Operations Center wallboard
with a live KPI strip, node topology grid, severity LEDs, alarm pulses,
panel codes (`[01·RUNS]`, `[02·DETAIL]`, …), live UTC clock, and a
light/dark theme toggle. All numbers are aggregated from real run state —
nothing is fabricated.

| Dark mode (default NOC look)                              | Light mode                                                   |
| --------------------------------------------------------- | ------------------------------------------------------------ |
| ![NOC dark](./docs/screenshots/noc-dark-prod.png)         | ![NOC light](./docs/screenshots/noc-light-prod.png)          |

Run detail with a real Foundry agent decision (Incident Analysis →
NetOps Coordinator handing off to dispatch):

![NOC run detail](./docs/screenshots/noc-run-detail-prod.png)

## Quick start

```powershell
# 1. Create venv & install
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .

# 2. (Optional) configure real services
Copy-Item .env.example .env
# edit .env -> set AZURE_AI_PROJECT_ENDPOINT, TEAMS_WEBHOOK_URL, EVENT_HUB_FQDN
az login        # for Foundry + Event Hub via DefaultAzureCredential

# 3. (Optional) publish hosted Prompt Agents to Foundry — one-time
.\.venv\Scripts\python.exe -m fibreops.demo publish

# 4. Run the demo
.\.venv\Scripts\python.exe -m fibreops.demo --signals 3
```

### Demo CLI

| Command                                          | Purpose                                                            |
| ------------------------------------------------ | ------------------------------------------------------------------ |
| `python -m fibreops.demo` _(or)_ `... run`       | Run the full end-to-end demo (default 3 signals)                   |
| `python -m fibreops.demo --signals N`            | Inject N telemetry signals                                         |
| `python -m fibreops.demo run --publish-eh`       | Publish signals to a real Event Hub before consuming               |
| `python -m fibreops.demo run --backend local`    | Force the deterministic local backend (offline-safe)               |
| `python -m fibreops.demo publish`                | Create the three hosted Prompt Agents in Foundry                   |
| `python -m fibreops.demo cleanup`                | Delete the hosted Prompt Agents                                    |
| `python -m fibreops.demo backend`                | Show which backend the current config will resolve to              |
| `python -m fibreops.demo card`                   | Render the latest Teams Adaptive Card payload from the outbox      |
| `python -m fibreops.demo chat "status"`          | Talk to FibreOps via the **GitHub Copilot SDK adapter** (slide 4)  |
| `python -m fibreops.demo publish-m365`           | Build the **Microsoft 365 Copilot declarative agent + action package** (slide 13) |

### Agent backend resolution

Set `FIBREOPS_AGENT_BACKEND` to override; otherwise auto-detect:

| Configured value | Behaviour                                                                |
| ---------------- | ------------------------------------------------------------------------ |
| `auto` _(default)_ | `hosted` if `state/foundry_agents.json` exists; else `foundry` if endpoint set; else `local` |
| `hosted`           | Always bind to published `FoundryAgent`s (errors if not yet published)   |
| `foundry`          | Always build local `Agent + FoundryChatClient`                           |
| `local`            | Always use the deterministic `LocalAgent` shim                           |

The `demo` command:
1. Boots the mock D365 service (`http://127.0.0.1:8765`).
2. Generates a deterministic burst of telemetry signals (always includes one
   `CRITICAL loss_of_light` against a high-customer node).
3. Drives each signal through the agent pipeline.
4. Renders the analysis, ticket, Teams notice, and dispatch decision in a
   rich console layout.
5. Runs the optimiser and prints per-run scores + suggestions.

## NOC Operations Console (web UI)

Prefer to drive the demo from a browser? Launch the operations console:

```bash
python -m fibreops.demo ui                  # listens on http://127.0.0.1:8800
python -m fibreops.demo ui --port 9000      # pick another port
```

The UI is a single-page **FastAPI + HTMX + Tailwind** dashboard backed by the
same `state/*.jsonl` files the CLI writes to. It boots the mock D365 service
**in-process** during startup, so a single command gives you a self-contained
demo with zero external dependencies.

Layout:

| Pane                | What it shows                                               |
|---------------------|-------------------------------------------------------------|
| **Header**          | `OPS · SECURE` badge, `MODE · {backend}` (`local`/`foundry`/`hosted`), live UTC clock, theme toggle, command bar |
| **`[KPI]` wallboard** | 8 tactical tiles (incidents 24h, critical with alarm pulse, customers impacted, engineers dispatched, Foundry IQ lookups, Teams cards posted, optimiser avg score, system health) |
| **`[01·RUNS]`**     | Live list of agent runs (polled every 3 s) with severity LED, node id, engineer, ETA, AWAITING-TELEMETRY empty state |
| **`[02·DETAIL]`**   | Click a row → full agent decision timeline (Incident Analysis → NetOps → Field Dispatch) |
| **`[03·TOPO]`**     | Node grid coloured by severity, dispatched outline, click-to-jump-to-detail, severity legend |
| **`[04·OPTIMISER]`** | Average rubric score, per-criterion bars, top suggestions   |
| **`[05·TEAMS]`**    | Flattened Adaptive Card preview (polled every 5 s)          |
| **`[06·VOICE]`**    | Voice Live outbox (utterance, voice, severity)              |
| **`[07·IQ]`**       | Foundry IQ + Web IQ + Work IQ grounding lookups (real Bing/Fabric calls in `foundry`/`hosted` mode, fixtures in `local` mode) |

Action buttons:

- **Inject signal** — push one synthetic outage signal through the orchestrator
- **Inject CRITICAL ×3** — push three signals including a forced `CRITICAL` event
- **Start / Stop simulation** — continuous injection on a 10 s loop
- **Run optimiser** — score the latest runs and refresh suggestions
- **Reset state** — truncate `runs.jsonl`, `traces.jsonl`, `teams_outbox.jsonl`, and the D365 store

There is also a small JSON API for scripting (`/api/runs`, `/api/optimiser`)
and a `/healthz` endpoint for liveness probes.

> ℹ️ The UI uses Tailwind + HTMX from CDN scripts so there is no Node tooling
> involved. For a fully offline / air-gapped demo, vendor those two scripts
> into `src/fibreops/ui/static/` and update `templates/index.html` to point at
> the local copies.

## Voice Live integration (BRK241 slide 8)

The system can speak status updates through **Azure AI Voice Live integration
with Foundry Agent Service** — the "Interact with Voice" arrow on Slide 4 and
the announcement on Slide 8 of the deck.

Behaviour:

- `tools/voice.speak_status_update(...)` builds an SSML utterance (voice +
  prosody picked per severity) and either POSTs it to
  `AZURE_VOICE_LIVE_ENDPOINT` or appends to `state/voice_outbox.jsonl`.
- The **Speak status** button in the NOC console (`🔊 Speak status`) speaks
  the latest incident — uses the `engineer_dispatched` phrase when dispatch
  is complete, otherwise `outage_detected`.
- Setting `FIBREOPS_VOICE_UPDATES=1` causes the NetOps and Field Dispatch
  agents to emit voice updates automatically at each milestone (off by
  default so the CLI demo stays quiet).
- The new "Voice Live updates" pane in the UI shows the rolling outbox
  (voice, transcript, incident id, timestamp) so the audience sees what the
  operator would hear.

| Env var                       | Purpose                                                  |
|-------------------------------|----------------------------------------------------------|
| `AZURE_VOICE_LIVE_ENDPOINT`   | HTTPS endpoint accepting `{voice, ssml, text, ...}`      |
| `AZURE_VOICE_LIVE_API_KEY`    | Optional `Ocp-Apim-Subscription-Key` header              |
| `AZURE_VOICE_LIVE_VOICE`      | Override the default voice (e.g. `en-GB-SoniaNeural`)    |
| `FIBREOPS_VOICE_UPDATES`      | `1` = agents speak automatically; default `0` (UI only)  |

## Foundry Routines (BRK241 slide 11)

The NetOps coordinator role has two interchangeable implementations:

1. **Chat agent** (default) — prompt-driven `Agent` / hosted Prompt Agent / `LocalAgent`.
2. **Foundry Routine** — a deterministic, declarative plan defined in
   `fibreops/agents/routines.py` (`NETOPS_ROUTINE_DEFINITION`) and executed by
   `NetOpsRoutineAgent`. Steps: `file_ticket → post_teams_notice →
   remember_ticket`, then a decision expression picks `HANDOFF:DISPATCH` vs
   `MONITOR`. Honours the same `await agent.run(prompt)` contract, so the
   orchestrator never needs to know which is in use.

Flip on the Routine path with:

```bash
$env:FIBREOPS_NETOPS_ROUTINE = "1"     # PowerShell
python -m fibreops.demo                # or python -m fibreops.demo ui
```

The `netops · routine` pill in the UI header confirms which mode is active.
The Routine emits per-step trace metadata under
`run.steps[NetOps].metadata.routine` for the optimiser/UI to read.

When the Foundry SDK ships a public `Routines` primitive, the publisher will
lift `NETOPS_ROUTINE_DEFINITION` into a hosted Routine via
`AIProjectClient.routines.create_version(...)` — until then the local runner
gives the same observable behaviour and the same trace shape.

## Foundry IQ — Web IQ + Work IQ (BRK241 slide 9)

The Incident Analysis agent grounds its reasoning with **Microsoft Foundry
IQ** — the "Web IQ" and "Work IQ" tiles on slide 9. Two new tools sit in front
of those endpoints:

- `tools/knowledge.web_iq_search(query, *, limit)` — public-web context
  (roadworks, weather, power, splice guidance).
- `tools/knowledge.work_iq_search(query, *, limit)` — enterprise context
  (site surveys, SLA tiers, competency matrix, MTTR trend).

Every lookup is persisted to `state/iq_lookups.jsonl` and surfaced live in the
NOC console **Knowledge sources** panel (polls `/partials/iq` every 6 s). When
the endpoints are unset the tools fall back to deterministic fixtures so the
demo always grounds — the `iq · fixtures` pill in the header confirms which
mode is active (`foundry-iq` when wired live, `fixtures` offline, `off` when
disabled).

| Env var                          | Purpose                                                        |
|----------------------------------|----------------------------------------------------------------|
| `FOUNDRY_WEB_IQ_ENDPOINT`        | HTTPS endpoint accepting `{query, top}` and returning `{results: [...]}` (or a bare list) |
| `FOUNDRY_WEB_IQ_API_KEY`         | Optional `Ocp-Apim-Subscription-Key` header                    |
| `FOUNDRY_WORK_IQ_ENDPOINT`       | HTTPS endpoint for enterprise IQ (same shape as Web IQ)        |
| `FOUNDRY_WORK_IQ_API_KEY`        | Optional API key for Work IQ                                   |
| `FIBREOPS_FOUNDRY_IQ`            | `0`/`false` disables IQ grounding entirely (default: `1`)      |

## GitHub Copilot SDK adapter (BRK241 slide 4)

The **GitHub Copilot SDK** in the deck is mirrored locally by
`fibreops.sdk.FibreOpsCopilotClient` — same `create_session()` /
`send_and_wait()` shape as `@github/copilot-sdk`, so the same calling code
works against either implementation.

```python
from fibreops.sdk import FibreOpsCopilotClient

client = FibreOpsCopilotClient()
session = client.create_session()
response = session.send_and_wait("status")
print(response.text)        # human-readable summary
print(response.data)        # structured JSON (runs, incidents, optimiser, ...)
```

The adapter routes prompts by shape:

- **JSON signal-shaped dicts** → forwarded to `orchestrator.handle_signal`
  so the SDK can ingest live telemetry.
- **Free-form text** → answered by a deterministic responder against the
  current `state/*.jsonl` files (`help`, `status`, `nodes`, `engineers`,
  `optimiser`, `dispatch`, ...).

Drive it from the terminal:

```powershell
python -m fibreops.demo chat "help"
python -m fibreops.demo chat "status"
python -m fibreops.demo chat '{"signal_id":"sig-demo","node_id":"FN-LDN-001","signal_type":"loss_of_light","severity":"critical"}'
```

Or hit the embedded HTTP endpoint when the NOC console is running:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8800/sdk/chat -Body '{"prompt":"status"}' -ContentType application/json
```

## Publishing to Microsoft 365 Copilot (BRK241 slide 13)

The Field Service Coordinator featured on slide 13 ships as a **declarative
agent + action plugin** ready for sideload via Teams Admin Center / Microsoft 365
Admin Center. Build the package with:

```powershell
python -m fibreops.demo publish-m365 --out dist/m365
#  ✓ wrote dist/m365/declarativeAgent.json
#  ✓ wrote dist/m365/fibreops-action.json
#  ✓ wrote dist/m365/manifest.json
#  ✓ wrote dist/m365/color.png  (192x192)
#  ✓ wrote dist/m365/outline.png ( 32x32)
#  ✓ wrote dist/m365/fibreops-copilot.zip
```

Set `M365_ACTION_BASE_URL` to the public HTTPS hostname of the FastAPI app
*before* publishing — the action plugin uses
`{base_url}/openapi.json` as its OpenAPI runtime. The CLI warns when the
placeholder is in effect.

| Env var                       | Purpose                                                                  |
|-------------------------------|--------------------------------------------------------------------------|
| `M365_ACTION_BASE_URL`        | Public HTTPS root for the FastAPI `/openapi.json` (e.g. Container Apps FQDN) |
| `M365_APP_ID`                 | Override the generated Teams app GUID (default: deterministic per repo)  |
| `M365_PUBLISHER_NAME`         | Publisher name shown in M365 Admin Center                                |
| `M365_PUBLISHER_WEBSITE`      | Publisher website link                                                   |

Upload the `fibreops-copilot.zip` to **Teams Admin Center → Manage apps →
Upload new app** (or **M365 Admin Center → Integrated apps → Upload custom
apps**). The declarative agent inherits the publisher metadata, advertises
the conversation starters from the deck, and proxies tool calls to the
deployed FastAPI app.

## Real-vs-Mock

| Component                | Real path                                                  | Mock path (default)                        |
|--------------------------|------------------------------------------------------------|--------------------------------------------|
| Foundry Agent Service    | **Hosted Prompt Agents** via `FoundryAgent` (after `demo publish`) or `Agent + FoundryChatClient` | `LocalAgent` deterministic executor        |
| Foundry Routines         | Hosted Routine on Foundry Agent Service                    | `NetOpsRoutineAgent` local runner          |
| Event Hub                | `EventHubConsumerClient` with `DefaultAzureCredential`     | In-process async generator                 |
| Microsoft Teams          | Incoming Webhook (Adaptive Card)                           | Append to `state/teams_outbox.jsonl`       |
| Voice Live               | `AZURE_VOICE_LIVE_ENDPOINT` (Voice Live / Azure AI Speech) | Append to `state/voice_outbox.jsonl`       |
| D365 Field Service       | Set `D365_MOCK_BASE_URL` to a real Dataverse v9.2 endpoint | FastAPI service (`fibreops.mocks.d365_service`) |
| Knowledge (SOPs, topology) | Foundry Work IQ / Web IQ / Fabric IQ connections          | Local JSON + markdown                       |
| Foundry IQ grounding     | `FOUNDRY_WEB_IQ_ENDPOINT` + `FOUNDRY_WORK_IQ_ENDPOINT`     | Deterministic fixtures in `tools/knowledge.py` |
| GitHub Copilot SDK       | `@github/copilot-sdk` against a hosted endpoint            | `fibreops.sdk.FibreOpsCopilotClient` (in-process) |
| Microsoft 365 Copilot publishing | Sideloaded `fibreops-copilot.zip` (Teams / Microsoft 365 admin) | `python -m fibreops.demo publish-m365` produces a placeholder package |
| Memory                   | Foundry Agent Memory store                                 | SQLite `state/memory.db`                    |
| Optimiser                | `FoundryEvals` + `evaluate_traces`                         | Local rubric (`fibreops.optimiser`)         |
| Application Insights     | `APPLICATIONINSIGHTS_CONNECTION_STRING`                    | JSON spans in `state/traces.jsonl`          |

## Layout

```
src/fibreops/
  agents/          # IncidentAnalysis, NetOpsCoordinator, FieldDispatch
    factory.py        # builds hosted / foundry / local backends
    publisher.py      # create/cleanup hosted Prompt Agents in Foundry
    instructions.py   # versioned system prompts
  tools/           # knowledge (+ Foundry Web IQ / Work IQ), teams, ticketing, dispatch, memory, voice
  telemetry/       # mock generator + Event Hub producer/consumer
  mocks/           # FastAPI Dataverse-shaped D365 service
  data/            # SOPs (markdown), fibre nodes + engineers (JSON)
  config.py        # pydantic settings
  observability.py # logging + OTel tracing (JSONL + Application Insights)
  orchestrator.py  # event loop wiring signals -> agents -> integrations
  optimiser.py     # rubric scoring + improvement suggestions
  demo.py          # rich one-command CLI (run / publish / cleanup / backend / card / ui / chat / publish-m365)
  ui/              # FastAPI + HTMX NOC operations console (templates + static)
  sdk/             # GitHub Copilot SDK adapter (FibreOpsCopilotClient, slide 4)
  dist/            # Microsoft 365 Copilot declarative agent + action package builder (slide 13)
  agents/routines.py  # declarative Foundry Routine for NetOps coordinator
infra/             # Bicep for Event Hubs / Key Vault / Log Analytics / Application Insights
tests/             # unit + e2e smoke tests
docs/
  DEMO.md          # rehearsal-grade 8-minute stage script
  KQL.md           # paste-ready Application Insights queries
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Deploy to Azure

The repository ships a complete `azd` (Azure Developer CLI) template that
provisions everything the demo needs on **Azure App Service for Linux
Containers** plus a private **Azure Container Registry**, **Event Hub**,
**Key Vault**, **Log Analytics**, and **Application Insights**.

```powershell
# 0. Prereqs: Azure CLI, azd >= 1.10, Docker (only needed for local image build),
#    a Foundry project with a deployed model. The default in this repo is
#    `gpt-4.1-mini` because that is what the demo Foundry account ships with;
#    any chat-completions deployment (gpt-4o-mini, gpt-4o, gpt-4.1) works —
#    just match AZURE_AI_MODEL_DEPLOYMENT to the deployment name in your account.

azd auth login
azd env new fibreops-demo

# Supply the Foundry endpoint + model deployment name (azd will prompt or
# read them from .env):
azd env set AZURE_AI_PROJECT_ENDPOINT  "https://<account>.services.ai.azure.com/api/projects/<project>"
azd env set AZURE_AI_MODEL_DEPLOYMENT  "gpt-4.1-mini"
azd env set AZURE_LOCATION             "swedencentral"   # any AppService + ACR region

azd up
```

`azd up` provisions the resource group, builds the image via `az acr build`,
pushes it to the private registry, and brings up the FastAPI app on App
Service. The NOC console is live at the URL printed at the end.

### Post-deploy: grant the managed identity its workload roles

The Bicep template **does not** create role assignments because most
deployers only have `Contributor` (not `Owner` / `User Access Administrator`)
on the subscription. A subscription `Owner` runs this script **once**
after `azd up`:

```powershell
pwsh scripts/grant-mi-roles.ps1 `
  -ResourceGroup        rg-fibreops-demo `
  -FoundryAccountName   <your-foundry-account> `
  -FoundryResourceGroup <rg-that-holds-foundry>
```

The script grants the App Service's system-assigned managed identity:

| Role                          | Scope                | Why                                          |
|-------------------------------|----------------------|----------------------------------------------|
| `Azure Event Hubs Data Owner` | Event Hubs namespace | Publish + consume `fibre-signals`            |
| `Key Vault Secrets User`      | Key Vault            | Read optional secrets (e.g. Teams webhook)   |
| `AcrPull`                     | Container Registry   | Pull image with MI instead of admin creds    |
| `Azure AI Developer`          | Foundry account      | Invoke hosted Prompt Agents + manage threads |
| `Cognitive Services OpenAI User` | Foundry account   | Call the chat-completions deployment from the agent runtime |

Wait 2–5 minutes for RBAC to propagate, then harden the App Service to
pull via managed identity and disable ACR admin:

```powershell
az webapp config set -g rg-fibreops-demo -n <webapp-name> `
  --generic-configurations '{"acrUseManagedIdentityCreds": true}'
az acr update -n <acr-name> --admin-enabled false
az webapp restart -g rg-fibreops-demo -n <webapp-name>
```

> **ABAC tip** — if your Foundry tenant scopes `Owner` with an ABAC condition
> (e.g. only on resources you create), the script will fail to grant the two
> Foundry roles. Have a tenant admin run the Foundry-scope half of the script
> against the Foundry account instead. The webapp's `MODE · foundry` badge
> turns green only after both Foundry roles have propagated.

### Infra-only deploy (no AZD)

```powershell
az group create -n rg-fibreops-demo -l swedencentral
az deployment group create `
  --resource-group rg-fibreops-demo `
  --template-file infra/main.bicep `
  --parameters namePrefix=fbreops `
               azureAiProjectEndpoint="https://<account>.services.ai.azure.com/api/projects/<project>" `
               azureAiModelDeployment="gpt-4.1-mini"
```

This provisions infra only — you still need to build + push the container
image to the created ACR and configure the App Service to point at it.
Most users should prefer `azd up`.

## See also
- `docs/DEMO.md` — rehearsal-grade 8-minute stage script with timings, speaker notes, fail-safes, and a kill-switch.
- `docs/KQL.md` — paste-ready Application Insights / Log Analytics queries for the agent decision timeline, per-agent latency, optimiser score trend, dispatch SLA, and full per-incident trace replay.

## Contributing, security, and code of conduct

- [CONTRIBUTING.md](CONTRIBUTING.md) — dev workflow + CLA notice.
- [SECURITY.md](SECURITY.md) — please report vulnerabilities to MSRC, not via GitHub issues.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Microsoft Open Source Code of Conduct.
- [LICENSE](LICENSE) — MIT.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorised use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos are subject to those third-party's policies.
