from __future__ import annotations

import httpx

import pytest

from auraly_pipeline.voices.handler import VoiceGenerateHandler
from auraly_pipeline.voices.provider import (
    ElevenLabsAdapter,
    ProviderFailure,
    ProviderFailureKind,
)


def test_429_after_dispatch_is_not_reported_as_safe_automatic_retry() -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, request=request, text="rate limited")

    adapter = ElevenLabsAdapter(
        api_key="placeholder",
        client=httpx.Client(transport=httpx.MockTransport(responder)),
    )
    with pytest.raises(ProviderFailure) as captured:
        adapter.generate_speech(
            text="Narration",
            voice_id="voice",
            model_id="model",
        )
    assert captured.value.kind is ProviderFailureKind.AMBIGUOUS
    assert captured.value.request_dispatched is True


def test_voice_handler_declares_reconcile_before_retry() -> None:
    assert VoiceGenerateHandler.retry_safety.value == "reconcile_before_retry"
