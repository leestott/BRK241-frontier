# Test suite

The test suite covers the local incident workflow, Azure deployment
configuration, Foundry agents, the web console, integrations, and voice-agent
behavior. Python tests use `pytest`; the browser voice client tests use Node's
built-in test runner.

| Test file | High-level coverage |
|---|---|
| `test_copilot_sdk.py` | Copilot SDK session lifecycle, chat routing, history limits, signal-shaped prompts, API validation, and CLI behavior. |
| `test_d365_mock.py` | Mock Dynamics 365 health, incident CRUD, status updates, field-service bookings, and missing-record behavior. |
| `test_dashboard_markup.py` | Dashboard structure, action/operations separation, incident-card metadata, and delivery of the dashboard JavaScript. |
| `test_deployment_config.py` | Safe AZD value parsing, non-secret `.env` synchronization, configurable infrastructure, portable naming, clean voice deployment defaults, resumable post-deploy phases, and managed-identity RBAC. |
| `test_documentation.py` | Public Markdown link integrity and discoverability of generated walkthrough and skill documentation. |
| `test_e2e_local.py` | Complete offline incident flow using local agents and the mock Dynamics 365 service. |
| `test_factory.py` | Agent-backend selection, published-agent registry handling, local role creation, severity escalation, coordinator dispatch policy, and engineer availability. |
| `test_incident_evaluation_dataset.py` | Foundry JSONL generation from recorded incidents, context preservation, runtime-data sanitization, and end-to-end technical consistency rows. |
| `test_knowledge_iq.py` | Foundry IQ configuration, local fallback fixtures, remote endpoint behavior, grounded local analysis, UI rendering, and reset behavior. |
| `test_m365_publish.py` | Microsoft 365 declarative-agent, action-plugin, and app-manifest shapes plus package creation and CLI publication behavior. |
| `test_models.py` | Domain-model enums, defaults, validation, generated identifiers/timestamps, ticket states, and dispatch serialization. |
| `test_observability.py` | JSONL span export, common orchestration attributes, event recording, and filtering of empty telemetry values. |
| `test_optimiser.py` | Incident scoring for analysis completeness, severity, customer impact, dispatch policy, SOP grounding, recommendations, and persisted optimization results. |
| `test_orchestrator.py` | Model-response extraction/parsing, malformed coordinator-output cleanup, signal-fact preservation, severity handling, and deterministic high/critical dispatch. |
| `test_routines.py` | Routine definitions, placeholder resolution, safe decision expressions, critical and low-severity execution paths, tool requirements, factory selection, and orchestrator compatibility. |
| `test_teams.py` | Teams outage/status message generation, engineer and ETA facts, optional fields, and outbox fallback. |
| `test_telemetry.py` | Synthetic telemetry generation, guaranteed critical loss-of-light coverage, and seeded determinism. |
| `test_tools.py` | SOP and topology lookup, spoken node-ID normalization, Dubai/Poland fixtures, engineer selection, and local memory persistence. |
| `test_ui.py` | Health and partial-render endpoints, incident/run APIs, simulation controls, signal injection, optimizer views, reset behavior, and error handling. |
| `test_voice.py` | Voice-update outbox behavior, incident phrases, optional automatic speech, UI rendering, reset behavior, and incomplete/corrupt record handling. |
| `test_voice_agent_publisher.py` | Voice Agents Preview definition construction, managed versus self-deployed models, locale/voice settings, and publisher configuration. |
| `test_voice_audio_validation.py` | PCM audio validation, WAV output, Speech authorization/token exchange, and fail-closed text-judge assessments. |
| `test_voice_client.cjs` | Browser stop-response and barge-in behavior, queued-audio cancellation, microphone continuity, and suppression of late audio. |
| `test_voice_live_session.py` | English-default multilingual policy, UAE Arabic voice selection, dedicated voice-project routing, websocket/session configuration, stop-during-tool behavior, and voice API/UI surfaces. |
| `test_voice_sdk_evaluation.py` | Voice evaluation dataset completeness, metric gates, latest-portal result publication, per-language summaries, and cloud result verification. |

## Shared test support

| File | Purpose |
|---|---|
| `conftest.py` | Shared fixtures for temporary state, environment cleanup, mock services, and application clients. |
| `__init__.py` | Marks the directory as a Python package for consistent imports. |

## Run the tests

```powershell
$env:PYTHONPATH = Join-Path $PWD "src"
python -m pytest tests -q
node --test tests\test_voice_client.cjs
```

Some cloud-dependent tests are skipped unless their opt-in environment
configuration is present. Generated `__pycache__` files are not part of the
source test inventory.
