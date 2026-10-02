# Canvas debug demo — verifying a fixed FibreOps decision bug

This is a historical regression walkthrough, not a currently broken
production incident. An earlier version of FibreOps displayed raw model
routing text in an incident's **Decision** field. The orchestrator now
sanitises that text before saving it, and targeted tests protect the fix.

The exercise uses a local NOC console in a browser and a PowerShell terminal.
The deployed site requires Microsoft Entra sign-in; unauthenticated API probes
against it return 401, so use the local console for this walkthrough.

## Setup

Install the dependencies as described in the [README](../README.md#quick-start).
In one terminal, start the console with the deterministic backend:

```powershell
$env:FIBREOPS_AGENT_BACKEND = "local"
.\.venv\Scripts\python.exe -m fibreops.demo ui
```

Open <http://127.0.0.1:8800/> in the browser, and click **Inject signal**
once. The same local state is available to the browser and CLI.

## Browser canvas — see the decision

Click the new incident and read its NetOps Coordinator decision in the detail
panel. It should be readable, with no `to=create_ticket` routing tokens or
non-printable characters. The original defect showed raw model output here;
the current UI should not reproduce it.

## Terminal canvas — verify the data boundary

In another terminal, inspect the local API response:

```powershell
$runs = Invoke-RestMethod http://127.0.0.1:8800/api/runs
$runs.runs | ForEach-Object {
    $_.steps | Where-Object agent -eq "NetOpsCoordinatorAgent" |
        Select-Object decision
}
```

The UI reads decisions from the same run records. If the API value is
unreadable, changing the UI alone would hide rather than fix the source.

## Inspect and test the fix

Open `src/fibreops/orchestrator.py` and find `_sanitise_decision()`. It removes
routing noise from the model result, preserves valid JSON and clean text,
and uses the derived `DISPATCH` or `MONITOR` label when the text is unusable.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py tests/test_ui.py -q
```

The tests exercise malformed and clean decisions, including what the UI
renders. Do not inject a production incident just to run this exercise.

## If a regression reappears

Inspect the stored API value, reproduce it in a targeted test, fix the
orchestrator boundary, rerun the tests and only then deploy through the
normal pipeline. The old `scripts/canvas_demo.ps1` helper probes `/api/runs`
without an authentication session; use it only against a local console,
not the protected production site.
