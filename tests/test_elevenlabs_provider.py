from __future__ import annotations

import base64

import httpx
import pytest

from auraly_pipeline.voices.provider import (
    ElevenLabsAdapter,
    ProviderFailure,
    ProviderFailureKind,
)


VOICE_ID = "voice_explicit"
MODEL_ID = "eleven_multilingual_v2"


def _response(
    status: int = 200, *, request: httpx.Request, payload: dict | None = None
) -> httpx.Response:
    return httpx.Response(status, request=request, json=payload or {})


def test_adapter_uses_official_timestamp_endpoint_and_explicit_voice_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["json"] = __import__("json").loads(request.content)
        return _response(
            request=request,
            payload={
                "audio_base64": base64.b64encode(b"ID3raw-audio").decode(),
                "alignment": {
                    "characters": list("Hello world"),
                    "character_start_times_seconds": [index * 0.1 for index in range(11)],
                    "character_end_times_seconds": [(index + 1) * 0.1 for index in range(11)],
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ElevenLabsAdapter(api_key="secret-value", client=client)
    result = adapter.generate_speech(text="Hello world", voice_id=VOICE_ID, model_id=MODEL_ID)

    assert seen["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice_explicit/with-timestamps"
        "?output_format=mp3_44100_128"
    )
    assert seen["json"] == {"text": "Hello world", "model_id": MODEL_ID}
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["xi-api-key"] == "secret-value"
    assert result.audio == b"ID3raw-audio"
    assert result.aligned_text == "Hello world"


def test_missing_api_key_is_stable_and_does_not_disclose_environment(monkeypatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(ProviderFailure) as caught:
        ElevenLabsAdapter.from_environment()
    assert caught.value.kind is ProviderFailureKind.CONFIGURATION
    assert str(caught.value) == "ElevenLabs API configuration is unavailable."


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ProviderFailureKind.TERMINAL),
        (401, ProviderFailureKind.TERMINAL),
        (429, ProviderFailureKind.AMBIGUOUS),
        (500, ProviderFailureKind.AMBIGUOUS),
        (503, ProviderFailureKind.AMBIGUOUS),
    ],
)
def test_http_failures_are_classified_without_raw_body(
    status: int, kind: ProviderFailureKind
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, request=request, text="SENSITIVE provider body")

    adapter = ElevenLabsAdapter(
        api_key="secret-value",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderFailure) as caught:
        adapter.generate_speech(text="Text", voice_id=VOICE_ID, model_id=MODEL_ID)
    assert caught.value.kind is kind
    assert "SENSITIVE" not in str(caught.value)
    assert "secret-value" not in str(caught.value)


def test_timeout_after_dispatch_is_ambiguous_not_blindly_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("SENSITIVE timeout", request=request)

    adapter = ElevenLabsAdapter(
        api_key="secret-value",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderFailure) as caught:
        adapter.generate_speech(text="Text", voice_id=VOICE_ID, model_id=MODEL_ID)
    assert caught.value.kind is ProviderFailureKind.AMBIGUOUS
    assert str(caught.value) == "The paid provider outcome requires reconciliation."


def test_invalid_alignment_falls_back_instead_of_being_trusted() -> None:
    cases = [
        ([0.0, float("inf")], [0.1, float("inf")]),
        ([0.0, 0.05], [0.2, 0.1]),
        ([0.1, 0.0], [0.2, 0.3]),
    ]
    for starts, ends in cases:

        def handler(request: httpx.Request, starts=starts, ends=ends) -> httpx.Response:
            payload = {
                "audio_base64": base64.b64encode(b"ID3raw-audio").decode(),
                "alignment": {
                    "characters": ["O", "K"],
                    "character_start_times_seconds": starts,
                    "character_end_times_seconds": ends,
                },
            }
            if any(value == float("inf") for value in starts + ends):
                import json

                body = json.dumps(payload, allow_nan=True).encode()
                return httpx.Response(
                    200, request=request, content=body, headers={"content-type": "application/json"}
                )
            return _response(request=request, payload=payload)

        adapter = ElevenLabsAdapter(
            api_key="secret-value",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = adapter.generate_speech(text="OK", voice_id=VOICE_ID, model_id=MODEL_ID)
        assert result.aligned_text is None
        assert result.alignment is None


def test_invalid_or_oversized_provider_payload_is_terminal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request=request, payload={"audio_base64": "not-base64"})

    adapter = ElevenLabsAdapter(
        api_key="secret-value",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderFailure) as caught:
        adapter.generate_speech(text="Text", voice_id=VOICE_ID, model_id=MODEL_ID)
    assert caught.value.kind is ProviderFailureKind.TERMINAL
