from __future__ import annotations

import base64
import binascii
import math
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

import httpx


ELEVENLABS_API_ROOT = "https://api.elevenlabs.io/v1"
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"
MAX_AUDIO_BYTES = 32 * 1024 * 1024


class ProviderFailureKind(StrEnum):
    CONFIGURATION = "configuration"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"
    AMBIGUOUS = "ambiguous"


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        kind: ProviderFailureKind,
        public_message: str,
        *,
        request_dispatched: bool = False,
    ) -> None:
        super().__init__(public_message)
        self.kind = kind
        self.public_message = public_message
        self.request_dispatched = request_dispatched


@dataclass(frozen=True)
class SpeechGeneration:
    audio: bytes
    aligned_text: str | None
    alignment: dict[str, Any] | None
    request_id: str | None
    output_format: str


class ElevenLabsAdapter:
    """Narrow official ElevenLabs REST adapter with sanitized failures."""

    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key.strip():
            raise ProviderFailure(
                ProviderFailureKind.CONFIGURATION,
                "ElevenLabs API configuration is unavailable.",
            )
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    @classmethod
    def from_environment(cls, *, client: httpx.Client | None = None) -> ElevenLabsAdapter:
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key.strip():
            raise ProviderFailure(
                ProviderFailureKind.CONFIGURATION,
                "ElevenLabs API configuration is unavailable.",
            )
        return cls(api_key=api_key, client=client)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate_speech(
        self,
        *,
        text: str,
        voice_id: str,
        model_id: str,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        voice_settings: dict[str, float | bool] | None = None,
    ) -> SpeechGeneration:
        if not text.strip() or not voice_id.strip() or not model_id.strip():
            raise ProviderFailure(
                ProviderFailureKind.TERMINAL,
                "The ElevenLabs speech request is invalid.",
            )
        payload: dict[str, object] = {"text": text, "model_id": model_id}
        if voice_settings:
            payload["voice_settings"] = voice_settings
        url = f"{ELEVENLABS_API_ROOT}/text-to-speech/{voice_id}/with-timestamps"
        try:
            response = self._client.post(
                url,
                params={"output_format": output_format},
                headers={
                    "xi-api-key": self._api_key,
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json=payload,
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise ProviderFailure(
                ProviderFailureKind.AMBIGUOUS,
                "The paid provider outcome requires reconciliation.",
                request_dispatched=True,
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderFailure(
                ProviderFailureKind.RETRYABLE,
                "The provider connection failed before a response was received.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                ProviderFailureKind.AMBIGUOUS,
                "The paid provider outcome requires reconciliation.",
                request_dispatched=True,
            ) from exc
        if response.status_code in {408, 429} or 500 <= response.status_code < 600:
            raise ProviderFailure(
                ProviderFailureKind.AMBIGUOUS,
                "The paid provider outcome requires reconciliation.",
                request_dispatched=True,
            )
        if response.status_code >= 400:
            raise ProviderFailure(
                ProviderFailureKind.TERMINAL,
                "The provider rejected the speech request permanently.",
            )
        try:
            data = response.json()
            encoded = data["audio_base64"]
            if not isinstance(encoded, str):
                raise TypeError
            audio = base64.b64decode(encoded, validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise ProviderFailure(
                ProviderFailureKind.TERMINAL,
                "The provider returned an invalid speech artifact.",
            ) from exc
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            raise ProviderFailure(
                ProviderFailureKind.TERMINAL,
                "The provider returned an invalid speech artifact.",
            )
        alignment = data.get("alignment")
        aligned_text = None
        if isinstance(alignment, dict):
            characters = alignment.get("characters")
            starts = alignment.get("character_start_times_seconds")
            ends = alignment.get("character_end_times_seconds")
            valid_characters = isinstance(characters, list) and all(
                isinstance(item, str) and len(item) == 1 for item in characters
            )
            if valid_characters:
                valid_characters_list = cast("list[str]", characters)
            else:
                valid_characters_list = []
            if isinstance(starts, list) and isinstance(ends, list):
                starts_list = cast("list[Any]", starts)
                ends_list = cast("list[Any]", ends)
            else:
                starts_list = []
                ends_list = []
            valid_times = (
                valid_characters
                and len(starts_list) == len(valid_characters_list)
                and len(ends_list) == len(valid_characters_list)
                and all(
                    isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    and math.isfinite(float(item))
                    and item >= 0
                    for item in starts_list + ends_list
                )
                and all(
                    float(start) <= float(end)
                    for start, end in zip(starts_list, ends_list, strict=True)
                )
                and all(
                    float(starts_list[index]) <= float(starts_list[index + 1])
                    and float(ends_list[index]) <= float(ends_list[index + 1])
                    for index in range(len(starts_list) - 1)
                )
            )
            if valid_times:
                aligned_text = "".join(valid_characters_list)
            else:
                alignment = None
        else:
            alignment = None
        request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        if request_id is not None and len(request_id) > 200:
            request_id = None
        return SpeechGeneration(
            audio=audio,
            aligned_text=aligned_text,
            alignment=alignment,
            request_id=request_id,
            output_format=output_format,
        )
