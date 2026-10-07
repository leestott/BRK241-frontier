# Reproducing and validating the FibreOps demo

## Which version is being demonstrated?

Verified on 7 October 2026; this is a release record, not an automatic deployment
of every subsequent source change.

| Surface | Existing deployed demo | Current source / experiment |
|---|---|---|
| Web image tag | `release-20261007-171059` | New source changes are not deployed |
| Image digest | `sha256:579f1591ac9a3c57c56c25b6695391c4a5533b480830ec1e58bdf6fa6f6dc74f` | Build a new image for an approved app release |
| Voice agent | `fibreops-noc-voice`, version **4** | `fibreops-noc-voice-incident-candidate`, version **3** |
| Effective speech | `gpt-realtime-2.1-datazone-standard`, native `marin` | Same model/voice, different instructions |
| Browser voice script | `voice-live.js?v=3` | `voice-live.js?v=4`, with structured incident facts |
| Language policy | English default; explicit Polish/Arabic override for one turn | Same policy; more focused incident wording |
| Native-speaker review | Not performed | Unavailable; automated evaluation only |

The deployed voice instruction SHA-256 is
`bdff4275af4038b239fd9c07bc472891a1aecd29692a20d23d7970c4e975006a`.
It matches the voice instructions at source checkpoint
`17441e16ccb17110b1e643ffb5930bba95379f87`. The active image digest is the
authority for the existing binary; rebuilding a source checkpoint with floating
dependencies does not guarantee the same image bytes.

To inspect that historical source without overwriting current work:

```powershell
git worktree add --detach ..\BRK241-deployed-baseline 17441e16ccb17110b1e643ffb5930bba95379f87
```

Use the current branch for development and corrected installation/evaluation
tooling. Its OpenTelemetry pins stay on the 1.44/0.65 line required by Azure
Monitor 1.8.10; main's earlier 1.45/0.66 dependency update could not install cleanly.
The offline evaluation CI job also explicitly installs `pytest-asyncio`.

Version numbers belong to individual Foundry agents and projects. Publishing in
a new project returns new version identifiers: use those returned identifiers,
not literal `4` or `3` from this environment. Never point an existing production
agent name at experimental source merely to reproduce a comparison.

## Local rehearsal

Use Python 3.11 or 3.12 (the CI matrix) and PowerShell 7. The release audit also
verified a fresh Python 3.13 installation. Run from the repository root. For a
rehearsal that cannot notify real operators, use a fresh checkout and terminal
without a configured `.env` or inherited service settings:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m pip check
$env:FIBREOPS_AGENT_BACKEND = "local"
.\.venv\Scripts\python.exe -m fibreops.demo backend
.\.venv\Scripts\python.exe -m fibreops.demo run --backend local --signals 3
.\.venv\Scripts\python.exe -m fibreops.demo ui
```

Open <http://127.0.0.1:8800/>. Do not copy the placeholder `.env.example` into a
local-only rehearsal: a nonempty placeholder endpoint can enable an integration.
If using an existing configured checkout, explicitly isolate or clear its
service settings before starting. `FIBREOPS_AGENT_BACKEND=local` changes agent
inference, **not** all external tool delivery.

Without service configuration, signals are generated locally, D365 is a local
Dataverse-shaped mock, Teams writes to an outbox, knowledge uses fixtures, and
voice can fall back to browser speech. This is not native Foundry speech.
Inspect the backend/integration badges before narrating a service as live.
Browser speech requires a browser voice to be installed and available.

The local CLI and local UI share the working directory's `state/` files.
The deployed App Service has separate in-container state and can restart empty.
Do not inject signals, run simulation or send notifications against the live
site merely to test health.

To view the static architecture walkthrough, serve `docs` on a different port
from the mock D365 service:

```powershell
.\.venv\Scripts\python.exe -m http.server 8766 --bind 127.0.0.1 --directory docs
```

Open <http://127.0.0.1:8766/services-architecture.html>. This is a static
walkthrough, not live telemetry.

## Validate the existing deployed app

Sign in to Azure CLI and the web browser separately. CLI login does not renew an
expired App Service browser cookie. Keep actual identifiers in your ignored
local configuration:

```powershell
az login --tenant <your-tenant-id>
az account set --subscription <your-subscription-id>
az account show --query "{user:user.name,tenant:tenantId,subscription:id}" -o json
az webapp config show -g <resource-group> -n <web-app> --query linuxFxVersion -o tsv
az webapp config appsettings list -g <resource-group> -n <web-app> `
  --query "[?name=='AZURE_VOICE_AGENT_NAME' || name=='AZURE_VOICE_AGENT_VERSION' || name=='AZURE_VOICE_AGENT_VOICE'].{name:name,value:value}" -o json
```

In the authenticated browser, verify `/healthz`, `/api/voice/session` and the
dashboard. Anonymous action/API requests may return 401 or redirect to Entra;
do not disable authentication to make a probe pass. If polling starts failing
after a long session, reload the top-level page and complete sign-in.

Open a new voice connection and inspect **`session.updated`**, not the generic
`session.created`. Check model, native voice and effective instructions. The
REST session descriptor names the agent but does not expose its version, so
verify the App Service version pin too. Test **Stop response** and a subsequent
reply without invoking external tools. This does not certify a physical
microphone or pronunciation.

The audit verified authenticated health/session endpoints and dashboard panels.
Earlier intermittent panel HTTP 500 responses were observed; successful later
probes do not establish their root cause or guarantee they cannot recur.
An expired browser session also caused authentication/CORS failures before
top-level sign-in was refreshed. Azure CLI authentication and upstream 502
failures have occurred during evaluation and must not be described as eliminated.

See the [deployment procedure](../README.md#deploy-to-azure) for provisioning a
new environment. `azd up` can run publication hooks and create new versions; it
is not a read-only health check. An app-only release must preserve the voice pin
unless promotion is explicitly approved.

## Reproduce the automated voice comparison

The publisher and evaluator require separate environments:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\python.exe -m pip install -r requirements-voice.txt
python -m venv .venv-evaluation
.\.venv-evaluation\Scripts\python.exe -m pip install -r requirements-evaluation.txt
```

Set `AZURE_AI_PROJECT_ENDPOINT` to your Foundry project, and use a **separate
candidate name** in `AZURE_VOICE_AGENT_NAME`. Set `AZURE_VOICE_AGENT_MODEL` to
`gpt-realtime-2.1` and `AZURE_VOICE_AGENT_VOICE` to `marin`, then publish:

```powershell
.\.venv-voice\Scripts\python.exe scripts\publish_voice_agent.py
```

Record the returned candidate version and the current production name/version.
The following commands use caller-supplied `$project`, `$speech`, `$judge`,
`$agent`, `$version` and `$label` variables. `$speech` is an authorized Azure
Speech endpoint for synthetic **input** audio; output remains native Foundry
speech. Use fresh output directories, never an existing successful run.

Run this block once for each agent, using `production` and `candidate` labels:

```powershell
$env:PF_WORKER_COUNT = "2"
foreach ($suite in @("incidents-v2", "language")) {
  $audio = "state\$label-$suite-audio"
  $evaluation = "state\$label-$suite-evaluation"
  .\.venv-evaluation\Scripts\python.exe -m scripts.validate_voice_audio `
    --project-endpoint $project --speech-endpoint $speech --judge-endpoint $judge `
    --agent $agent --version $version --suite $suite --repeats 2 `
    --collect-only --output-dir $audio
  if ($LASTEXITCODE -ne 0) { throw "Audio collection failed; preserve its report." }
  .\.venv-evaluation\Scripts\python.exe -m scripts.evaluate_voice_responses `
    --audio-report "$audio\report.json" --project-endpoint $project `
    --judge-endpoint $judge --output-dir $evaluation
  # Exit 1 can mean quality failures, not successful completion. Require evidence.
  if (-not (Test-Path "$evaluation\summary.json")) {
    throw "Scoring/publication incomplete; inspect the log before continuing."
  }
  $summary = Get-Content "$evaluation\summary.json" -Raw | ConvertFrom-Json
  if (-not $summary.cloud_verified -or $summary.portal_run.status -ne "completed") {
    throw "Foundry evidence is not complete."
  }
}
foreach ($suite in @("incidents-v2", "language")) {
  .\.venv-evaluation\Scripts\python.exe -m scripts.compare_voice_evaluations `
    --runs "state\production-$suite-evaluation" "state\candidate-$suite-evaluation" `
    --output "state\$suite-comparison.json"
  if ($LASTEXITCODE -ne 0) { throw "Comparison contracts differ or evidence is incomplete." }
}
```

That is **96 incident turns and 32 language turns per agent**, 256 turns total.
The incident suite contains 48 development, 24 inspected-regression and 24 new
held-out turns per agent. Save and freeze `case-manifest.json` before tuning;
once holdout results guide a prompt change, a new holdout is required.
Use the same source revision, judge deployment and evaluation protocols on both
sides. Fresh generated responses and model-judge scores can vary: repeatability
of the procedure is not a guarantee of identical scores.

If latest-portal polling times out after scoring, rerun the evaluator with the
same report/output directory and `--resume`. It checks the saved SDK scores,
dataset, portal IDs, metadata and every downloaded row, without rescoring or
creating another run. An interrupted incident audio collection can instead use
`--resume-from` with a **new audio directory**; this is not supported for shared
language-reset conversations. Keep all original errors visible.

If scoring saved `sdk-results.json` but publication failed **before**
`portal-run.json` was created, use `--sdk-results <saved-sdk-results.json>` with
a fresh output directory instead. This verifies the original cloud SDK run and
publishes its existing scores without new judge calls. Use the new directory
in the comparison. Do not use `--resume` unless the portal identity exists.

No human listener is available for this demo. Keep
`human_listening_approved=false`; report **automated evaluation only,
pronunciation not verified**. Do not replace missing listening approval with a
text score, hide failed gates or change thresholds to make the candidate pass.
See the [latest matched comparisons](../README.md#optimized-incident-comparison-7-october-2026)
and latest Foundry runs for actual measured results, rather than treating the
latest version as inherently better.

## Before committing or releasing

Run the tests in [CONTRIBUTING](../CONTRIBUTING.md), the isolated publisher tests,
the offline evaluation tests and `node --test tests\test_voice_client.cjs`.
The live judge calibration is opt-in and billed. Build with
`python -m pip wheel --no-deps .` in an isolated environment.
Keep `.env`, `.azure/`, credentials, evaluation downloads, audio and runtime state
out of Git. Follow the [public-sharing checklist](../SECURITY.md#public-sharing-checklist).

```powershell
.\.venv-voice\Scripts\python.exe -m unittest tests.test_voice_agent_publisher -v
.\.venv-evaluation\Scripts\python.exe -m pip install pytest pytest-asyncio
.\.venv-evaluation\Scripts\python.exe -m pytest --noconftest `
  tests\test_voice_sdk_evaluation.py tests\test_voice_audio_validation.py `
  tests\test_voice_evaluation_comparison.py tests\test_voice_judge_live.py -q
node --test tests\test_voice_client.cjs
```

### Audit evidence (7 October 2026)

- Fresh isolated runtime requirements installed successfully; `pip check`
  reported no broken requirements.
- Full runtime suite: **192 passed, 13 skipped**. The runtime environment
  intentionally does not install the evaluation SDK or preview publisher SDK.
- Separate offline evaluation suite: **38 passed, 9 skipped** (opt-in live
  calibration); isolated publisher: **3 passed**; browser voice tests:
  **6 passed**.
- Final documentation/UI check: **24 passed**. A deployable wheel built
  successfully, and the public-source secret scan reported no leaks.
- An isolated local CLI rehearsal completed three signals, recording **three
  mock tickets and two mock bookings**. This is not evidence of real Dataverse,
  Teams delivery or hosted inference.
- Authenticated live health/session probes succeeded. Three rounds of six
  dashboard-panel requests all returned HTTP 200; earlier intermittent errors
  remain a disclosed limitation, not a resolved root-cause claim.
- Both matched voice comparisons completed with verified SDK and latest-portal
  evidence. Candidate incident scores improved, but language regression and
  factual failures prevented a clean promotion recommendation.

These are local and live audit results, **not a claim that a new GitHub CI run
passed**. Committing source does not deploy it, promote a voice candidate or
approve pronunciation.
