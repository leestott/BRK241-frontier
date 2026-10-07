# Reproducing the deployed FibreOps demo

## Production-aligned source

The code, tests, dependency manifests and workflows on this branch are restored
to deployed source checkpoint **`17441e16ccb17110b1e643ffb5930bba95379f87`**.
Only Markdown documentation differs from that checkpoint. This is intentional:
do not mix unpromoted experimental code into the production source.

Experimental tooling is separate from the production-aligned source; see
[Evaluation tooling and experimental results](#evaluation-tooling-and-experimental-results).
Do not use an experimental revision as the production build source.

Verified deployed release, 7 October 2026:

| Surface | Deployed value |
|---|---|
| Web image tag | `release-20261007-171059` |
| Image digest | `sha256:579f1591ac9a3c57c56c25b6695391c4a5533b480830ec1e58bdf6fa6f6dc74f` |
| Voice agent | `fibreops-noc-voice`, version **4** |
| Effective model and speech | `gpt-realtime-2.1-datazone-standard`, native `marin` |
| Browser voice script | `voice-live.js?v=3` |
| Language policy | English default; explicit Polish/Arabic override for one turn |
| Integrations observed | Hosted agent backend, Teams outbox, IQ fixtures |
| Native-speaker review | Not performed; pronunciation not verified |

The deployed voice instruction SHA-256 is
`bdff4275af4038b239fd9c07bc472891a1aecd29692a20d23d7970c4e975006a`.
It matches the voice instructions at the source checkpoint above.
The active image digest is the authority for the existing binary: rebuilding
source with floating dependencies does not guarantee identical image bytes.
Publishing an unchanged definition creates a new Foundry version; it does not
automatically reproduce the existing version identifier or change the app pin.

Verify production alignment from the repository root:

```powershell
git diff --exit-code 17441e16ccb17110b1e643ffb5930bba95379f87 HEAD -- `
  . ':(exclude)*.md' ':(exclude)**/*.md'
```

No output and exit code zero mean the committed non-Markdown tree matches.
Check `git status --short` too: that comparison does not include uncommitted work.
Do not replace the pinned image or agent merely to check the website.

## Local rehearsal

Use Python 3.11 or 3.12 (the CI matrix) and PowerShell 7. Run from the repository
root in a fresh checkout/terminal without a configured `.env` or inherited
service settings. The audit also used Python 3.13.

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

Open <http://127.0.0.1:8800/>. A nonempty placeholder endpoint can enable an
integration: do not copy `.env.example` into a local-only rehearsal.
Selecting `local` changes agent inference, **not** all external tool delivery.
In an existing configured checkout, isolate its settings first; never overwrite
an existing `.env` to run a sample.

Without service configuration, signals are generated locally, D365 uses a local
Dataverse-shaped mock, Teams writes to an outbox, and knowledge uses fixtures.
Browser speech is a fallback, not native Foundry speech, and requires an
available browser voice. Inspect the integration badges before narrating a
service as live. Real Dataverse integration requires an authenticated connector,
permissions and schema mapping; changing the mock URL is not sufficient.

The local CLI/UI share the working directory's `state/` files. The deployed
App Service has separate in-container state and may restart empty. Do not
inject signals or send notifications against production just to check health.

The static architecture walkthrough uses port 8766 to avoid the mock D365
service on port 8765:

```powershell
.\.venv\Scripts\python.exe -m http.server 8766 --bind 127.0.0.1 --directory docs
```

Open <http://127.0.0.1:8766/services-architecture.html>. It is not live telemetry.

## Validate the existing deployment

Azure CLI and browser sign-in are separate. CLI login does not refresh an
expired App Service browser cookie. Keep actual deployment identifiers in
ignored local configuration. The tenant, subscription, resource-group and
web-app values below are placeholders for your own environment:

```powershell
az login --tenant <your-tenant-id>
az account set --subscription <your-subscription-id>
az webapp config show -g <resource-group> -n <web-app> --query linuxFxVersion -o tsv
az webapp config appsettings list -g <resource-group> -n <web-app> `
  --query "[?name=='AZURE_VOICE_AGENT_NAME' || name=='AZURE_VOICE_AGENT_VERSION' || name=='AZURE_VOICE_AGENT_VOICE'].{name:name,value:value}" -o json
```

In the authenticated browser, check `/healthz`, `/api/voice/session` and the
dashboard. Anonymous APIs may return 401 or redirect to Entra; do not disable
authentication to make a probe pass.

On a new voice connection, inspect **`session.updated`**, not the generic
`session.created`, for effective model, voice and instructions. Verify the
App Service version pin separately: the REST session descriptor does not
expose it. Test English, an explicit Polish request, an explicit Arabic request,
and a subsequent default-English turn. Test **Stop response**, then another
reply, without invoking dispatch or notification tools.

Authenticated health/session probes and three rounds of six dashboard-panel
requests returned HTTP 200 during the audit. Earlier intermittent panel HTTP 500
errors were observed; later successes do not establish their root cause.
Expired browser authentication also caused CORS/sign-in failures. Evaluation
encountered Azure CLI authentication and upstream 502 failures. These are
disclosed limitations, not errors proven permanently eliminated.

See the [deployment procedure](../README.md#deploy-to-azure) for a new environment.
`azd up` can run publishing hooks and create versions; it is not a read-only
health check. Deployment and voice promotion require an explicit rollout decision.

## Evaluation tooling and experimental results

This branch retains the original production-checkpoint language audio validator,
SDK evaluator and isolated voice publisher. See the
[voice evaluation documentation](../README.md#azure-ai-evaluation-sdk-and-foundry-results)
for their original workflow. It does **not** contain incident suites,
`--resume`, `--resume-from`, segmented-transcript handling, the comparison
script or the later async/token-cache safeguards.

The optimized comparison used different tooling preserved at `c80c2e9`.
To inspect/reproduce that experiment, use a separate worktree and its own
documentation; do not publish or deploy from it as though it were production:

```powershell
git worktree add --detach ..\BRK241-voice-experiment c80c2e9
```

Keep evaluation SDKs isolated from the application and publisher environments.
Historical scoring can stall or fail; preserve errors and complete datasets.
A successful upload does not mean acceptance gates passed. No human listener is
available: retain `human_listening_approved=false` and report pronunciation as
**not verified**. Never tune a previously inspected holdout and still call it
untouched, mix evaluation protocols, or weaken thresholds to obtain a pass.

Recorded matched results using the experimental tooling (not production code):

| Evaluation | Production voice v4 | Candidate voice v3 |
|---|---:|---:|
| Incident all nine gates | 69/96 | 72/96 |
| Fresh held-out incident gates | 16/24 | 20/24 |
| Language all six gates | 24/32 | 21/32 |
| Language selection, language suite | 32/32 | 31/32 |

The candidate improved incident totals but regressed in English incident
fluency, answered an explicit Arabic request in English, and converted
unconfirmed dispatch into definite non-dispatch. It was **not promoted**.
The controlled incident fixtures do not verify live dispatch or notification.
These scores are not directly comparable to the older evaluation protocol.

To reproduce the experiment, publish a separately named candidate in your own
Foundry project and record the returned version. Keep project-specific
evaluation IDs, portal links and definition backups with your private run
artifacts, not in the public guide.

All four runs completed with zero evaluator errors. The experimental tooling
verified downloaded portal rows against saved SDK scores. Incident publication
needed resumption; production language publication reused saved scores after
an authentication failure, without rescoring. Failed attempts remain evidence.

## Before committing or releasing

Run the [development tests](../CONTRIBUTING.md#development-workflow), plus:

```powershell
python -m venv .venv-voice
.\.venv-voice\Scripts\python.exe -m pip install -r requirements-voice.txt
.\.venv-voice\Scripts\python.exe -m unittest tests.test_voice_agent_publisher -v
python -m venv .venv-evaluation
.\.venv-evaluation\Scripts\python.exe -m pip install -r requirements-evaluation.txt pytest
.\.venv-evaluation\Scripts\python.exe -m pytest --noconftest `
  tests\test_voice_sdk_evaluation.py tests\test_voice_audio_validation.py -q
node --test tests\test_voice_client.cjs
.\.venv\Scripts\python.exe -m pip wheel --no-deps .
```

The test counts and wheel recorded for `c80c2e9` apply to the experimental tree,
not this restored tree. Revalidate this tree separately; never present local
checks as a new GitHub CI run. Keep credentials, `.env`, `.azure/`, recordings,
evaluation downloads and runtime state out of Git. Follow the
[public-sharing checklist](../SECURITY.md#public-sharing-checklist).

Restored-tree validation on 7 October 2026: dependency installation and
`pip check` passed; runtime tests **171 passed, 4 skipped**; isolated offline
evaluation tests **19 passed**; isolated publisher **3 passed**; browser voice
tests **5 passed**. The non-Markdown tree comparison against `17441e1` was empty.
These checks validate the restored source, not the unpromoted experiment.
