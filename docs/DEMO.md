# Live Demo Script — BRK241 Autonomous Fibre Outage Response

> **Stage time:** 8 minutes ± 30s
> **Audience eye-line:** one terminal (left), one Teams channel (right), one Foundry portal tab (background, only opened during Act 5)
> **Failure budget:** 1 act may go wrong; you have a deterministic fallback for every dependency

---

## 0. Pre-flight (T-15 minutes, off-stage)

Run these once before walking on stage. Each line is copy-paste-safe.

```powershell
# Confirm the installed SDKs are present
cd <path-to-this-repo>
.\.venv\Scripts\python.exe -c "import agent_framework, agent_framework_foundry, azure.ai.projects; print('OK')"

# 2) Make sure the .env is filled in
#    AZURE_AI_PROJECT_ENDPOINT=https://<acct>.services.ai.azure.com/api/projects/<proj>
#    AZURE_AI_MODEL_DEPLOYMENT=gpt-5.4-mini   (or whatever deployment you have)
#    TEAMS_WEBHOOK_URL=https://...           (Power Automate / channel webhook)
#    EVENT_HUB_FQDN=<ns>.servicebus.windows.net
#    EVENT_HUB_NAME=fibre-signals
#    APPLICATIONINSIGHTS_CONNECTION_STRING=...
Copy-Item .env.example .env  # if not yet present, then edit
az login

# 3) Publish the three hosted Prompt Agents to Foundry (one-time, ~30s)
.\.venv\Scripts\python.exe -m fibreops.demo publish

# 4) Sanity-check the resolved backend
.\.venv\Scripts\python.exe -m fibreops.demo backend
#  -> Resolved backend: hosted

# 5) Warm-up run (NOT shown to audience — primes connections, caches)
.\.venv\Scripts\python.exe -m fibreops.demo --signals 1 --skip-optimiser
#  -> if a webhook is configured, verify the card arrives in Teams

# 6) Reset the state for a clean stage run
Remove-Item state\runs.jsonl, state\traces.jsonl, state\teams_outbox.jsonl, `
            state\optimiser_suggestions.jsonl, state\d365_store.json -ErrorAction Ignore

# 7) Open the four windows you'll use on stage
#    a) Terminal (this one)
#    b) Teams channel with the webhook target
#    c) VS Code with src\fibreops\agents\instructions.py open (Act 2 prop)
#    d) Foundry portal → Agents page (Act 5 prop, kept minimised until needed)
```

> 🛟 **If Foundry publish fails on stage day**: set
> `$env:FIBREOPS_AGENT_BACKEND="local"` in PowerShell and rerun the demo.
> The deterministic `LocalAgent` keeps the orchestrator and local tools
> available, but does not demonstrate hosted inference.

---

## Act 1 — "The pipes can talk" *(60s)*

**Goal:** establish the problem and the input layer in one breath.

**On screen:** terminal showing the source tree.

**Say:**
> "A national fibre operator has tens of thousands of optical line terminals. Each one continuously emits health telemetry — light levels, BER, reachability. Today, when something breaks, a human in a NOC reads a dashboard, opens an ITSM ticket, calls a dispatcher, and types into Teams. This demo explores how agents can help with that loop; review the decisions before using them operationally."

**Type:**
```powershell
type src\fibreops\telemetry\generator.py | Select-Object -First 25
```

**Point out:**
- One file, two modes: in-process generator (deterministic for this demo) and `EventHubConsumerClient` against a real namespace (`EVENT_HUB_FQDN`).
- `DefaultAzureCredential` — no connection strings, no key rotation, Microsoft Entra ID-only Event Hub.

---

## Act 2 — "Three agents, one orchestrator" *(90s)*

**Goal:** show that this is *not* one mega-prompt; it's three agents with hard contracts.

**On screen:** VS Code → `src/fibreops/agents/instructions.py`.

**Say:**
> "Three role-specialised Prompt Agents, all hosted in **Microsoft Foundry Agent Service**. Each has its own tool surface, its own instructions, and a strict output contract. The Coordinator hands off to Dispatch with a literal `HANDOFF:DISPATCH` token — no fuzzy 'I think we should…'. This is how you stop agents from inventing work."

**Highlight:**
- The JSON schema in `INCIDENT_ANALYSIS_INSTRUCTIONS_V1`.
- The handoff tokens in `NETOPS_COORDINATOR_INSTRUCTIONS_V1`.
- Quick scroll of `src/fibreops/tools/` — each tool is a typed Python function. Foundry sees the JSON schema, the runtime executes the Python.

**Type (don't run yet):**
```powershell
.\.venv\Scripts\python.exe -m fibreops.demo backend
```
Show that **Resolved backend: hosted** — agents are in Foundry, not in this Python process.

---

## Act 3 — "Watch it think" *(2 minutes)*

**Goal:** the headline moment. One command, real Azure, end-to-end.

**On screen:** terminal full-screen.

**Say:**
> "I'm going to inject three deterministic telemetry signals. Watch the orchestrator route each signal through the agent pipeline; the exact analysis and timing depend on the active backend."

**Type:**
```powershell
.\.venv\Scripts\python.exe -m fibreops.demo --signals 3
```

**Narrate as panels appear:**

| Panel                               | What to say                                                                                                                                       |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| Configuration table                 | "Check the backend and each integration status before describing it as live; an unset webhook uses the local Teams outbox, and the signal generator is not Event Hubs." |
| `Generated 3 telemetry signals`     | "Deterministic seeds so I can rehearse — in production this is the consumer group on the hub."                                                    |
| 🧠 **IncidentAnalysisAgent**         | "Notice the structured analysis — summary, probable cause, customer impact and severity. Compare the output against the input rather than assuming a specific escalation." |
| 🛰️ **NetOpsCoordinatorAgent**       | "Ticket `INC-XXXX` just appeared in D365 — let me show you" (pivot to Teams, see the **Adaptive Card** arrive in the channel)                     |
| 🚐 **FieldDispatchAgent**           | "Engineer chosen by skill, region, and shift — no human in the loop. The card in Teams just updated with the ETA."                                |

**Pivot to Teams** during the first dispatch panel — the **Adaptive Card** should already be in the channel. Audience sees the loop close in their familiar tool.

---

## Act 4 — "Show me the receipts" *(60s)*

**Goal:** prove this isn't theatre — everything is replayable.

**Type:**
```powershell
Get-Content state\runs.jsonl | Select-Object -First 1 | ConvertFrom-Json | ConvertTo-Json -Depth 10 | more
```

**Say:**
> "Every run is a JSON document: the input signal, every agent step, every tool call, every output, every ticket. This is what we ship to Log Analytics and to the optimiser. It is also what Foundry Evals ingests — same shape."

**Then:**
```powershell
.\.venv\Scripts\python.exe -m fibreops.demo card
```

**Point at:**
- The Adaptive Card JSON the system would have posted.
- The hint at the bottom: paste into `adaptivecards.io/designer` and you get the exact rendering — useful for governance reviews.

---

## Act 5 — "It watches itself" *(90s)*

**Goal:** end on the moat — the optimisation loop.

**On screen:** the optimiser table that's still on the terminal from Act 3.

**Say:**
> "The five-criterion rubric checks analysis completeness, severity consistency, ticket creation, dispatch policy and SOP references. The score depends on this run; inspect its actual reasons and suggestions. Prompt changes need another evaluation before claiming they improved the result."

**Pivot to Application Insights** (one Foundry-portal-or-AppInsights tab) and run a query from `docs/KQL.md`:

```kusto
// Agent decision timeline for the last hour
dependencies
| where timestamp > ago(1h)
| where name startswith "agent."
| project timestamp, name, duration, success, operation_Id
| order by timestamp asc
```

**Say:**
> "These spans can be queried in Application Insights when Azure Monitor
> export is configured and ingestion has been verified. The optimiser
> suggests changes; we still have to review, test and publish a prompt
> revision ourselves."

---

## Act 6 — Failure stories *(60s, only if time)*

Pick **one** of these and run it deliberately. Audiences love deliberate failure.

### 6a. "What if there's no engineer?"

```powershell
# Mark every London engineer as off-shift (JSON-aware, order-independent)
$f = 'src\fibreops\data\engineers.json'
$j = Get-Content $f -Raw | ConvertFrom-Json
foreach ($e in $j) { if ($e.region -eq 'London') { $e.on_shift = $false } }
$j | ConvertTo-Json -Depth 5 | Set-Content $f

.\.venv\Scripts\python.exe -m fibreops.demo --signals 1
```
Dispatch returns `NO_ENGINEER_AVAILABLE`, the optimiser flags `dispatch_policy`.
Back up `src\fibreops\data\engineers.json` before this optional failure
demo, and restore that backup afterward; do not discard unrelated edits.

### 6b. "What if D365 is down?"

```powershell
.\.venv\Scripts\python.exe -m fibreops.demo --signals 1 --no-serve-mock-d365
```
Ticketing tool retries, then the run records the failure cleanly with a structured error rather than crashing.

### 6c. "What if Foundry is down?"

```powershell
$env:FIBREOPS_AGENT_BACKEND="local"
.\.venv\Scripts\python.exe -m fibreops.demo --signals 1
Remove-Item Env:FIBREOPS_AGENT_BACKEND
```
The demo runs identically against the deterministic `LocalAgent`. Use this as your **on-stage safety net** if anything Azure-side wobbles.

---

## Closing line *(15s)*

> "Three agent roles, one orchestrator, and a local fallback. Foundry hosting, Teams delivery and Event Hubs are available when configured; the default signal source and ticket service are demos. Inspect traces and scores, then iterate with tests before adopting this pattern."

---

## Stage-day quick reference (print this!)

```
TERMINAL CHEAT SHEET
====================
publish hosted agents   :  python -m fibreops.demo publish
show resolved backend   :  python -m fibreops.demo backend
RUN THE DEMO            :  python -m fibreops.demo --signals 3
show last Teams card    :  python -m fibreops.demo card
LAUNCH WEB CONSOLE      :  python -m fibreops.demo ui      # http://127.0.0.1:8800
serve hosted agent      :  python -m fibreops.demo serve-hosted   # /responses on 8088
deploy hosted agent     :  pwsh scripts/deploy-hosted-agent.ps1 -RegistryName <acr>
chat via Copilot SDK    :  python -m fibreops.demo chat "status"
build M365 package      :  python -m fibreops.demo publish-m365
turn on Routines        :  $env:FIBREOPS_NETOPS_ROUTINE = "1"
turn on Voice (auto)    :  $env:FIBREOPS_VOICE_UPDATES   = "1"
disable Foundry IQ      :  $env:FIBREOPS_FOUNDRY_IQ      = "0"
cleanup hosted agents   :  python -m fibreops.demo cleanup -y
force local fallback    :  $env:FIBREOPS_AGENT_BACKEND="local"

WINDOW LAYOUT
=============
[ Terminal — full screen during Act 3 ]
[ Teams channel — pop-out, lower-right ]
[ VS Code: instructions.py — Act 2 only ]
[ Application Insights / Foundry portal — Act 5 only ]

KILL-SWITCH
===========
If anything Azure-side fails on stage:
   $env:FIBREOPS_AGENT_BACKEND="local"
   .\.venv\Scripts\python.exe -m fibreops.demo --signals 3
Every act still works.
```

---

## Optional: drive the demo from the NOC web console

If the room has a projector and you'd rather narrate from a browser than a
terminal, swap Act 3 for the **FibreOps NOC Operations Console**:

```bash
python -m fibreops.demo ui            # http://127.0.0.1:8800
```

Suggested narration overlay:

- **Act 1 (signals)** — start on the dashboard with an empty *Active incidents*
  pane. Click **Inject CRITICAL ×3**. Three rows fade in with severity dots.
- **Act 2 (agents)** — click the critical row. The right pane fans out the
  Incident Analysis → NetOps Coordinator → Field Dispatch decision timeline
  with the SOP reference, ticket id, and engineer name.
- **Act 2a (Foundry IQ, slide 9)** — point at the *Knowledge sources* panel.
  The Incident Analysis agent grounded its reasoning with Web IQ (roadworks,
  weather) and the **Foundry IQ knowledge base** (SOPs + node topology via
  Azure AI Search agentic retrieval), cached in `state/iq_lookups.jsonl`. The
  `iq · fixtures` pill flips to `foundry-iq` the moment the knowledge base is
  configured (`FOUNDRY_IQ_SEARCH_ENDPOINT` + `FOUNDRY_IQ_KNOWLEDGE_BASE`), or a
  legacy `FOUNDRY_WEB_IQ_ENDPOINT` is set. Provision the knowledge base with
  `scripts/provision_foundry_iq.py` (see the README "Foundry IQ knowledge base"
  section).
- **Act 2b (Routines, optional)** — flip `$env:FIBREOPS_NETOPS_ROUTINE = "1"`
  before launch and the `netops · routine` pill turns violet. Same UI, same
  trace shape — narration line: *"the NetOps coordinator now runs the local
  routine implementation, the same three deterministic steps every time."*
- **Act 3 (Teams)** — point at the *Teams card preview* pane (auto-polled
  every 5 s) — the same Adaptive Card payload that landed in the channel.
- **Act 3b (Foundry Voice Agent Preview)** — click **🔊 Speak status**. The *Voice updates*
  pane shows the recorded announcement text the on-call operator would hear, with the
  voice and severity styling that matches the incident. When
  `AZURE_VOICE_AGENT_NAME` is configured after publishing a distinct
  `kind: voice` agent, the browser opens a Foundry realtime voice session
  and **plays the spoken update**. Otherwise browser speech synthesis
  supplies an offline fallback.
- **Act 3c (Talk to agent)** — publish the separate Foundry Voice Agent
  Preview and click **🎙️ Talk to agent**. The browser captures your microphone,
  streams PCM16/24 kHz audio through `/ws/voice` to the Foundry project
  voice-agent protocol, and plays the reply. Entra authentication stays
  server-side. **Stop response** cancels the current reply and queued playback
  while keeping the microphone available. Click **🛑 Stop talking** to end the session. This preview
  does not have a production SLA.
- **Voice acceptance (off-stage)** — run the real-audio and SDK evaluation commands
  in the README. The latest portal's **Evaluations > Runs** uses the new evaluation
  API; a legacy SDK upload alone is not visible there. The native candidate
  defaults to English, with Polish/Arabic only on an explicit current-turn request.
  Inspect `session.updated` before claiming the deployed agent uses that policy.
  Web-app deployment does not promote a candidate or waive fluency/listening gates.
- **Recorded evaluation demo (2026-10-07)** — production `fibreops-noc-voice`
  version **4** contains the unchanged definition from candidate version **7**,
  using native `marin` speech. The temporary candidate agent was removed after
  rollout; its v6 and v7 evaluation runs remain available for comparison.
  Both runs passed language selection, semantic accuracy, natural wording,
  transcription and coherence on all 32 replies. Fluency at 4/5 or above fell
  from **22/32 to 14/32** after the wording change: this is a measured regression,
  not an improvement. The latest fluency pass counts are English **11/22**,
  Polish **1/6**, and Arabic **2/4**. Show the failed rows and their explanations
  in Foundry rather than describing a completed run as a passed quality gate.
  This rollout has an explicit demo exception; native-speaker audio approval
  remains outstanding. Version numbers are per agent, so production v4 and
  candidate v7 identify the same tested definition, not different model ages.
- **Architecture (optional)** — open [the documentation-only walkthrough](services-architecture.html).
  Play or step through the incident and voice/evaluation workflows. It sends no
  service requests and is not a live trace viewer.
- **Act 4 (optimiser)** — click **Run optimiser**. The middle column shows
  the average score, per-criterion bars, and improvement suggestions.
- **Act 5 (autonomy)** — toggle **Start simulation**. New incidents stream
  in every 10 s with no further input. Toggle off to stop.
- **Act 5a (Copilot SDK, slide 4)** — drop to a terminal and type
  `python -m fibreops.demo chat "status"`. Same orchestrator, addressed via
  the in-process `FibreOpsCopilotClient` adapter (`create_session` /
  `send_and_wait` — inspired by `@github/copilot-sdk`, not the SDK itself)
  returning a deterministic JSON envelope.
- **Act 5b (M365 publishing, slide 13)** — run
  `python -m fibreops.demo publish-m365`. The CLI emits a sideload-ready
  `fibreops-copilot.zip` with a declarative agent + action plugin + Teams
  manifest. Point at the warning when `M365_ACTION_BASE_URL` is unset;
  publishing a package is not the same as configuring and validating a
  production integration.

The UI reads the same `state/*.jsonl` files the CLI demo writes to, so you
can flip between terminal and browser freely and they always agree.

---

## What is real vs. what is mocked

| Layer                 | This demo            | Production swap                                  |
| --------------------- | -------------------- | ------------------------------------------------ |
| Telemetry             | Generator or Event Hub | Real OLT → Event Hub (same code)               |
| Agents                | **Foundry-hosted Prompt Agents** | Identical                                |
| Hosted agent (container) | `serve-hosted` locally, `deploy-hosted` to Foundry | Containerised hosted agent in Foundry Agent Service |
| NetOps coordinator    | Optional Foundry **Routine** (`FIBREOPS_NETOPS_ROUTINE=1`) | Hosted Routine (when SDK exposes it) |
| Function tools        | Real Python in-process | Identical                                      |
| Ticketing             | FastAPI mock D365      | Requires Dataverse authentication, permissions and schema mapping |
| Teams                 | Configured webhook or local outbox | Verify delivery and operational permissions |
| Voice                 | Text outbox; native voices have no SSML | Published **Foundry Voice Agent Preview**, pinned version |
| Evaluation            | Local run rubric; separate real-audio voice suite | Evaluation SDK scores and latest Foundry portal acceptance gates |
| Tracing               | OpenTelemetry → Application Insights | Identical                                |
