# Test suite

The test suite covers the local incident workflow, Azure deployment
configuration, Foundry agents, the web console, integrations, and voice-agent
behavior. Python tests use `pytest`; the browser voice client tests use Node's
built-in test runner. The inventory currently contains 24 Python test files
and one Node test file.

| Test file | High-level coverage |
|---|---|
| `test_copilot_sdk.py` | Copilot SDK session lifecycle, chat routing, history limits, signal-shaped prompts, API validation, and CLI behavior. |
| `test_d365_mock.py` | Mock Dynamics 365 health, incident CRUD, status updates, field-service bookings, and missing-record behavior. |
| `test_dashboard_markup.py` | Dashboard structure, action/operations separation, incident-card metadata, privacy-safe Foundry footer, and delivery of the dashboard JavaScript. |
| `test_deployment_config.py` | Safe AZD value parsing, non-secret `.env` synchronization, configurable infrastructure, portable naming, clean voice deployment defaults, resumable post-deploy phases, managed-identity RBAC, private build-context exclusions, and the CI async test plugin. |
| `test_documentation.py` | Public Markdown link integrity, test-inventory completeness, offline contributor commands, and discoverability of generated walkthrough and skill documentation. |
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
| `test_voice_audio_validation.py` | PCM audio validation, WAV output, Speech authorization/token exchange, transient Speech synthesis retries, and fail-closed text-judge assessments. |
| `test_voice_client.cjs` | Browser stop-response and barge-in behavior, queued-audio cancellation, microphone continuity, and suppression of late audio. |
| `test_voice_live_session.py` | English-default multilingual policy, UAE Arabic voice selection, dedicated voice-project routing, websocket/session configuration, stop-during-tool behavior, and voice API/UI surfaces. |
| `test_voice_sdk_evaluation.py` | Voice evaluation dataset completeness, metric gates, latest-portal result publication, per-language summaries, and cloud result verification. |

## Shared test support

| File | Purpose |
|---|---|
| `conftest.py` | Shared fixtures for temporary state, environment cleanup, mock services, and application clients. |
| `__init__.py` | Marks the directory as a Python package for consistent imports. |

## Run the tests

Run from the repository root in a fresh terminal without real integration
settings. Follow the [contributor setup](../CONTRIBUTING.md) to install
`requirements.txt` and the editable package in `.venv`. Do not copy a configured
`.env`, `.azure/`, or runtime `state/` into an offline test checkout.

```powershell
$env:FIBREOPS_AGENT_BACKEND = "local"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests -q
node --test tests\test_voice_client.cjs
```

### Isolated preview publisher tests

The four publisher tests require the Voice Agents Preview SDK. They are skipped
when the runtime SDK does not expose `VoiceAgentDefinition`. Keep this SDK out
of the application `.venv`; the following tests construct definitions without
publishing agents or requiring Azure credentials:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\python.exe -m pip install -r requirements-voice.txt
.\.venv-voice\Scripts\python.exe -m unittest tests.test_voice_agent_publisher -v
```

### Isolated evaluation tooling tests

The SDK evaluation test module is skipped when `azure-ai-evaluation` is absent.
Its tests and the audio-validation tests use mocked service calls and do not
run a paid cloud evaluation:

```powershell
python -m venv .venv-evaluation
.\.venv-evaluation\Scripts\python.exe -m pip install -r requirements-evaluation.txt pytest pytest-asyncio
.\.venv-evaluation\Scripts\python.exe -m pytest --noconftest tests\test_voice_sdk_evaluation.py tests\test_voice_audio_validation.py -q
```

CI runs the runtime tests on Windows/Linux with Python 3.11 and 3.12, the Node
voice-client tests, isolated preview publisher checks, wheel-content validation,
and a separate Python 3.12 evaluation-tooling job. Real-audio acceptance and
Foundry portal evaluations are separate from these offline tests; see the
[voice evaluation workflow](../README.md#azure-ai-evaluation-sdk-and-foundry-results).

This inventory describes `main`. The separate `voice-candidate-v2` branch adds
credential-cache and native-async evaluation regressions; do not assume those
repairs are in the production branch.

Generated `__pycache__` files are not part of the source test inventory.
