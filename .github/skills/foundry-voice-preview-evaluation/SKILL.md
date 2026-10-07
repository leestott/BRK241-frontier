---
name: foundry-voice-preview-evaluation
description: 'Use when publishing or validating native Foundry Voice Agents Preview, English-default explicit Polish/Arabic per-turn replies, Stop response, real-audio acceptance, or Azure AI Evaluation SDK results in the latest Microsoft Foundry portal.'
---

# FibreOps voice publication and evaluation

Run commands from the repository root. Keep deployment-specific values in the
ignored local environment, never in this skill or tracked files.

## Sources and environment

- [Voice definition](../../../src/fibreops/voice_live/definition.json)
- [Publisher](../../../scripts/publish_voice_agent.py)
- [Audio validation](../../../scripts/validate_voice_audio.py)
- [SDK and latest-portal evaluation](../../../scripts/evaluate_voice_responses.py)
- [Voice deployment and acceptance documentation](../../../README.md)

The runtime uses `azure-ai-projects<2.7`. Publish voice versions in an isolated
environment installed from `requirements-voice.txt`; evaluate in a separate
environment installed from `requirements-evaluation.txt`. Do not upgrade the
runtime to the preview publisher SDK.

## Procedure

1. Confirm the target project, candidate agent and currently pinned production
   version. Record the existing container image and version for rollback.
2. Run local regressions:

   ```powershell
   $env:PYTHONPATH = (Join-Path $PWD 'src')
   python -m pytest tests\test_voice.py tests\test_voice_live_session.py tests\test_dashboard_markup.py -q
   .\.venv-voice\Scripts\python.exe -m unittest tests.test_voice_agent_publisher
   node --test tests\test_voice_client.cjs
   python -m pytest --noconftest tests\test_voice_sdk_evaluation.py tests\test_voice_audio_validation.py -q
   ```

3. Publish a separate candidate with `AZURE_AI_PROJECT_ENDPOINT`,
   `AZURE_VOICE_AGENT_NAME`, `AZURE_VOICE_AGENT_MODEL=gpt-realtime-2.1` and
   `AZURE_VOICE_AGENT_VOICE=marin` set locally:

   ```powershell
   .\.venv-voice\Scripts\python.exe scripts\publish_voice_agent.py
   ```

4. Use the returned immutable version in the README's
   `scripts\validate_voice_audio.py` command. Collect all cases and repeats with
   `--collect-only`; use a fresh ignored output directory. These are real PCM
   inputs, not text-only response probes. Speech TTS creates test inputs only.
5. Run `python -m scripts.evaluate_voice_responses` with the complete report,
   project endpoint, judge endpoint and fresh output directory. Set
   `$env:PF_WORKER_COUNT = "2"` for bounded local evaluation concurrency.
   Keep custom evaluators asynchronous with per-row timeouts; synchronous
   wrappers can deadlock the SDK's shared worker pool at this concurrency.
   Reuse the shared SDK bearer-token provider for custom judge calls. Do not
   create per-row credentials or persist access tokens; preserve authentication
   errors and verify the signed-in account before retrying a failed run.
6. Verify both the original SDK record and the latest portal's **Evaluations >
   Runs** record. Legacy `azure_ai_project` upload alone does not populate the
   latest portal. Retain original scores and failed gates; check every downloaded
   row and exact agent/version tags. `--sdk-results` republishes matching saved
   results without rescoring. These are on-demand runs, not recurring schedules.
   If an existing portal run outlasts local polling, use `--resume` with the same
   output directory and report. It verifies the saved scores and existing run
   rather than creating another run; preserve timeout logs.
7. Obtain native-speaker listening review for Polish and Arabic. Text scores and
   non-silent waveforms do not certify pronunciation.
   Use the generated `listening-review.csv`; all rows start pending.
   When no listener is available, report automated-only demo results with
   `human_listening_approved=false`; do not claim a listening pass or acoustic
   certification. Explicit demo rollout approval is still required.
   For new incident work, use `--suite incidents-v2 --repeats 2` (96 turns with
   controlled read-only tools). Its inspected former holdout is now regression
   data; freeze the new manifest before collecting either agent.
   Keep the original language suite. Do not tune against the new held-out split
   or weaken its nine acceptance gates. Preserve all spoken preambles and
   response transcript segments; do not compare different transcript/rubric
   protocols. `incidents` remains the historical 72-turn benchmark.
   Compare identical datasets with `scripts.compare_voice_evaluations` and retain
   per-language and per-split failures, not just aggregate averages.
8. Activate a production version only after its acceptance gate passes and rollout
   is approved. For an app-only release, retain the existing version/settings and
   avoid global postdeploy hooks that republish agents.
9. Reconnect the browser and inspect **session.updated**, not session.created, for
   effective instructions, voice and model. Test Stop response during generation,
   queued playback and tool work; the microphone must remain usable.

## Invariants

- English is the default even for Polish/Arabic input. Only explicit requests in
  the current turn select Polish or Arabic; overrides expire at the next turn.
- Preserve negation, uncertainty and exact quantities. Never replace native
  speech with a British Neural TTS voice while claiming native output.
- Never lower thresholds merely to pass a release, ignore incomplete datasets,
  hide evaluator errors or claim a queued run is complete.
- Stop response cannot undo an external action already executed. It must stop
  playback and avoid restarting speech after a cancelled tool response.
- Do not print credentials, publish recordings of real customers, run notification
  tools as smoke tests, or commit ignored state/evaluation artifacts.
