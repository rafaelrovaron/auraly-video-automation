from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import types
import pytest

from auraly_pipeline.heygen.provider import HeyGenMcpAdapter, HeyGenProviderFailure
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from tests.test_heygen_provider import FakeSession
from tests.test_heygen_video_domain import video_item


class VideoSession(FakeSession):
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["image"],
        "additionalProperties": False,
        "properties": {
            "image": {
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "asset_id"},
                            "asset_id": {"type": "string"},
                        },
                        "required": ["type", "asset_id"],
                        "additionalProperties": False,
                    }
                ]
            },
            "audioAssetId": {"type": "string"},
            "aspectRatio": {"enum": ["9:16", "16:9"]},
            "resolution": {"enum": ["720p", "1080p"]},
            "outputFormat": {"enum": ["mp4"]},
            "fit": {"enum": ["cover", "contain"]},
            "expressiveness": {"enum": ["medium", "high", "low"]},
            "motionPrompt": {"type": "string"},
            "callbackId": {"type": "string"},
            "title": {"type": "string"},
        },
    }

    async def list_tools(self) -> types.ListToolsResult:
        existing = await super().list_tools()
        existing.tools.extend(
            [
                types.Tool(name="create_video_from_image", input_schema=self.schema),
                types.Tool(
                    name="get_video",
                    input_schema={
                        "type": "object",
                        "properties": {"videoId": {"type": "string"}},
                        "required": ["videoId"],
                    },
                ),
            ]
        )
        return existing


def adapter(session: VideoSession) -> HeyGenMcpAdapter:
    @asynccontextmanager
    async def factory(interactive: bool) -> AsyncIterator[VideoSession]:
        yield session

    return HeyGenMcpAdapter(session_factory=factory)


def test_video_payload_exact() -> None:
    session = VideoSession()
    session.responses["create_video_from_image"] = {"video_id": "video-one"}
    provider = adapter(session)
    preflight = provider.preflight_video(HeyGenVideoConfig())
    item = video_item(
        account_ref=preflight.account_ref, schema_fingerprint=preflight.schema_fingerprint
    )
    assert provider.create_video(item, callback_id="callback-one") == "video-one"
    payload = session.calls[-1][1]
    assert payload["image"] == {"type": "asset_id", "asset_id": "image-one"}
    assert payload["audioAssetId"] == "audio-one"
    assert payload["aspectRatio"] == "9:16"
    assert payload["callbackId"] == "callback-one"
    assert "idempotency_key" not in payload and "engine" not in payload
    session.responses["get_video"] = {
        "video_id": "video-one",
        "status": "completed",
        "video_url": "https://signed.example/video?secret=value",
    }
    result = provider.get_video("video-one")
    assert session.calls[-1] == ("get_video", {"videoId": "video-one"})
    assert result.download_url is not None
    assert "secret" not in result.model_dump_json()


def test_video_schema_requires_imported_audio() -> None:
    session = VideoSession()
    session.schema = {
        **session.schema,
        "properties": {
            k: v for k, v in session.schema["properties"].items() if k != "audioAssetId"
        },
    }
    with pytest.raises(HeyGenProviderFailure):
        adapter(session).preflight_video(HeyGenVideoConfig())
    assert not any(name == "create_video_from_image" for name, _ in session.calls)


def test_malformed_create_is_ambiguous() -> None:
    session = VideoSession()
    session.responses["create_video_from_image"] = {}
    provider = adapter(session)
    preflight = provider.preflight_video(HeyGenVideoConfig())
    with pytest.raises(HeyGenProviderFailure) as failure:
        provider.create_video(
            video_item(
                account_ref=preflight.account_ref, schema_fingerprint=preflight.schema_fingerprint
            ),
            callback_id="callback-one",
        )
    assert failure.value.kind == "ambiguous"
    assert failure.value.request_dispatched


def test_asset_preflight_stays_independent() -> None:
    session = FakeSession()
    from tests.test_heygen_provider import _adapter

    assert _adapter(session).preflight().connected


def test_wrapped_paid_dispatch_timeout_remains_ambiguous() -> None:
    class TimeoutVideoSession(VideoSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            if name == "create_video_from_image":
                raise TimeoutError("secret=value")
            return await super().call_tool(name, arguments)

    session = TimeoutVideoSession()

    @asynccontextmanager
    async def factory(interactive: bool) -> AsyncIterator[VideoSession]:
        try:
            yield session
        except Exception as error:
            raise ExceptionGroup("session failed", [error])

    provider = HeyGenMcpAdapter(session_factory=factory)
    preflight = provider.preflight_video(HeyGenVideoConfig())
    with pytest.raises(HeyGenProviderFailure) as failure:
        provider.create_video(video_item(account_ref=preflight.account_ref, schema_fingerprint=preflight.schema_fingerprint), callback_id="callback-one")
    assert failure.value.kind == "ambiguous" and failure.value.request_dispatched
    assert "secret=value" not in str(failure.value)


@pytest.mark.parametrize("composition", ["allOf", "anyOf", "oneOf"])
def test_composed_optional_engine_blocks_before_dispatch(composition: str) -> None:
    session = VideoSession()
    session.schema = {
        **session.schema,
        "additionalProperties": True,
        composition: [{"properties": {"engine": {"enum": ["engine-a", "engine-b"]}}}],
    }
    with pytest.raises(HeyGenProviderFailure):
        adapter(session).preflight_video(HeyGenVideoConfig())
    assert not any(name == "create_video_from_image" for name, _ in session.calls)
