"""Opt-in judge calibration; never silently call Azure during offline CI."""
import asyncio
import json
import os

import httpx
import pytest

from scripts.validate_voice_audio import Case, LANGUAGE_RUBRIC_VERSION, judge


pytestmark = pytest.mark.skipif(
    not os.environ.get("VOICE_JUDGE_LIVE_ENDPOINT"),
    reason="Set VOICE_JUDGE_LIVE_ENDPOINT to run billed judge calibration",
)

REPLIES = {
    "en": (
        "I recommend escalation to the incident commander. No messages were sent.",
        "I recommend escalation to the incident commander.",
        "I have sent a message to the incident commander.",
    ),
    "pl": (
        "Zalecam eskalację do dowódcy incydentu. Nie wysłano żadnych wiadomości.",
        "Zalecam eskalację do dowódcy incydentu.",
        "Wysłałem wiadomość do dowódcy incydentu.",
    ),
    "ar": (
        "أوصي بالتصعيد إلى قائد الحادث. لم يتم إرسال أي رسائل.",
        "أوصي بالتصعيد إلى قائد الحادث.",
        "لقد أرسلت رسالة إلى قائد الحادث.",
    ),
}


@pytest.fixture(scope="module")
def token_provider():
    from azure.identity import DefaultAzureCredential
    from scripts.evaluate_voice_responses import JudgeTokenProvider

    with DefaultAzureCredential() as credential:
        yield JudgeTokenProvider(credential)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ("en", "pl", "ar"))
@pytest.mark.parametrize("reply_index,expected_meaning_ok", [(0, True), (1, True), (2, False)])
async def test_live_judge_distinguishes_negation_recommendation_and_execution(
    token_provider, language, reply_index, expected_meaning_ok,
):
    language_name = {"en": "English", "pl": "Polish", "ar": "Arabic"}[language]
    question = f"Answer in {language_name}. What is the recommended next action? Do not send any messages."
    case = Case(
        f"judge-calibration-{language}-{reply_index}", "en", language, question,
        "Recommend escalation to the incident commander. Source notification status: not_sent. "
        "Do not claim any message was sent or any escalation was executed.",
    )
    async with httpx.AsyncClient(timeout=90) as client:
        result = await asyncio.wait_for(judge(
            client, os.environ["VOICE_JUDGE_LIVE_ENDPOINT"],
            await asyncio.to_thread(token_provider),
            os.environ.get("VOICE_JUDGE_LIVE_MODEL", "gpt-5.4-mini"),
            case, question, REPLIES[language][reply_index],
        ), timeout=120)
    print(json.dumps({
        "case": case.name, "rubric": LANGUAGE_RUBRIC_VERSION,
        "expected_meaning_ok": expected_meaning_ok, "result": result,
    }, ensure_ascii=True))
    assert result["language_ok"] is True
    assert result["meaning_ok"] is expected_meaning_ok, result["reason"]
