"""Offline checks for the opt-in, real-audio acceptance runner."""
import json
import struct
import wave

import pytest

from scripts.validate_voice_audio import CASES, audio_metrics, judge, write_wav


@pytest.mark.parametrize("pcm", [b"", b"\x00"])
def test_audio_metrics_reject_missing_or_malformed_pcm(pcm):
    with pytest.raises(ValueError, match="PCM16"):
        audio_metrics(pcm)


def test_audio_metrics_and_wav_format(tmp_path):
    pcm = struct.pack("<hh", 1000, -1000) * 12000
    assert audio_metrics(pcm) == {"seconds": 1.0, "rms": 1000.0}
    path = tmp_path / "test.wav"
    write_wav(path, pcm)
    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        assert audio.readframes(audio.getnframes()) == pcm


@pytest.mark.asyncio
@pytest.mark.parametrize("assessment,raises,passed", [
    ({"language_ok": True, "meaning_ok": True, "language_natural": True, "input_understood": True}, False, True),
    ({"language_ok": False, "meaning_ok": True, "language_natural": True, "input_understood": True}, False, False),
    ({"language_ok": "true"}, True, None),
    ({}, True, None),
])
async def test_judge_fails_closed_on_bad_or_incomplete_assessments(assessment, raises, passed):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(assessment)}}]}

    class Client:
        async def post(self, *args, **kwargs):
            return Response()

    if raises:
        with pytest.raises(ValueError, match="invalid evaluation shape"):
            await judge(Client(), "https://example.test", "fake", "test-model", CASES[0], "input", "output")
    else:
        result = await judge(Client(), "https://example.test", "fake", "test-model", CASES[0], "input", "output")
        assert result["passed"] is passed
