# Canvas debug demo — fixing a live FibreOps prod bug

This is a ~5-minute live demo showing how the **GitHub Copilot app + canvases**
turn a production defect on the deployed NOC console into a verified fix —
without leaving the Copilot session. It uses two canvases: **Browser** (the live
Azure site) and **Terminal** (probes + tests).

- **Live site:** https://fbreops-noc-gkrykk.azurewebsites.net/
- **Persona:** NOC platform engineer debugging a garbled agent decision in prod.
- **Skills shown:** Browser canvas, Terminal canvas, repo edit, test validation.

---

## Setup (once)

```powershell
python -m pip install -e .
```

## The bug

On the live site, `/api/runs` shows the NetOpsCoordinator decision as corrupted
model output — tool-call routing tokens and hallucinated CJK spam, e.g.:

```
" to=create_ticket ＿老司机 to=create_ticket  彩神争霸? "
```

It also renders verbatim in the NOC run-detail timeline. Embarrassing in prod.

# Demo Script: Copilot Canvas Debug Loop

## Demo arc

### 1. Browser canvas — see it in production
Open the **Browser** canvas on the live site, then open a run detail.

Point to the garbled **Decision** line and say:

> This is live and customer-facing. The decision field should be readable, but it is clearly leaking routing noise into the UI.

**Demo intent:** establish the visible production defect before touching the code.

---

### 2. Terminal canvas — confirm the defect
Run the canvas probe from the **Terminal** canvas:

```powershell
./scripts/canvas_demo.ps1
```

The probe prints each run's decision string. The output should make it obvious that the decision value is garbled.

**Narration:**

> The browser shows the symptom, but the terminal confirms this is coming from the API response, not just a rendering issue.

---

### 3. Copilot debugs
Ask Copilot:

> The decision field on /api/runs is garbled — find and fix it.

Expected Copilot investigation path:

- Trace the `/api/runs` response back to the run record.
- Identify that `orchestrator.py` stores raw `coord_text` directly into the run record.
- Confirm that the small hosted model can leak routing tokens such as `to=create_ticket`, spam text, or non-printable characters.
- Confirm that the UI simply displays whatever decision string is stored.

**Root cause:**

`orchestrator.py` stores raw `coord_text` from the coordinator model directly into the run record and UI. This means model artefacts, routing tokens, spam-like fragments, and non-printable characters can appear in the customer-facing decision field.

**Fix:**

Add `_sanitise_decision()` so the app:

- strips routing tokens such as `to=create_ticket`;
- removes non-printable characters;
- preserves any valid JSON payload;
- leaves already-clean decision text untouched;
- falls back to the derived `DISPATCH` or `MONITOR` label when the model output is unusable.

**Narration:**

> Copilot has moved from symptom to source. The important part is that we are not masking the UI; we are fixing the data boundary where untrusted model output becomes application state.

---

### 4. Terminal canvas — validate
Run the test suite from the **Terminal** canvas:

```powershell
python -m pytest tests/test_orchestrator.py tests/test_ui.py -q
```

Expected validation coverage:

- garbled decision text becomes clean;
- JSON payloads are preserved;
- already-clean text remains untouched;
- UI rendering uses the cleaned decision value.

**Narration:**

> The tests now protect the exact failure mode we saw in production: dirty model output entering the run record and leaking into the UI.

---

### 5. Ship
Deploy the fix:

```powershell
azd deploy
```

Refresh the **Browser** canvas and reopen the run detail.

The **Decision** line should now read cleanly.

**Closing line:**

> We found the live defect, reproduced it from the terminal, used Copilot to trace and fix the root cause, validated it with tests, and shipped the fix — all inside one Copilot session. Loop closed.

---

## Presenter checklist

- [ ] Live site open in Browser canvas.
- [ ] Run detail page ready with visible garbled **Decision** value.
- [ ] Terminal canvas available.
- [ ] `./scripts/canvas_demo.ps1` runs successfully.
- [ ] Copilot prompt ready: `the decision field on /api/runs is garbled — find and fix it.`
- [ ] Tests available: `tests/test_orchestrator.py` and `tests/test_ui.py`.
- [ ] Deployment command ready: `azd deploy`.
- [ ] Final browser refresh confirms clean decision output.

## Short talk track

This demo shows a complete production-style debugging loop. We start with a customer-facing issue in the Browser canvas, confirm the defect through the Terminal canvas, ask Copilot to investigate the API and orchestration path, fix the unsafe handling of model output, validate the behaviour with targeted tests, and deploy the update. The value is not just that Copilot writes code; it helps close the loop from production symptom to tested fix and deployment.
