from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from mcp import types
import pytest

from auraly_pipeline.heygen.auth import KeyringTokenStorage
from auraly_pipeline.heygen.domain import AssetSource, ProviderAssetStatus, RemoteAssetKind
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.provider import HeyGenMcpAdapter, HeyGenProviderFailure


TOOLS: dict[str, dict[str, object]] = {
    "get_current_user": {},
    "create_asset_upload_batch": {"files": {"type": "array"}, "title": {"type": "string"}},
    "complete_asset_batch": {"batchId": {"type": "string"}},
    "get_asset_batch": {"batchId": {"type": "string"}, "token": {"type": "string"}},
    "bulk_asset_statuses": {"assetIds": {"type": "string"}, "batchIds": {"type": "string"}},
    "get_asset": {"assetId": {"type": "string"}},
}
IMAGE = AssetSource(
    source_id="00000000-0000-4000-8000-000000000001",
    kind=RemoteAssetKind.IMAGE,
    local_path="campaigns/one/image.png",
    sha256="1" * 64,
    mime_type="image/png",
    size_bytes=4,
)


class FakeSession:
    def __init__(self, *, tools: dict[str, dict[str, object]] = TOOLS) -> None:
        self.tools = tools
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, dict[str, Any]] = {
            "get_current_user": {
                "username": "user-one",
                "email": "user@example.com",
                "subscription": {"credits": {"premium_credits": {"remaining": 12}, "add_on_credits": {"remaining": None}}},
            },
            "create_asset_upload_batch": {
                "batch_id": "batch-1",
                "items": [
                    {
                        "asset_id": "asset-1",
                        "upload_url": "https://storage.example/upload",
                        "upload_headers": {"x-upload": "one"},
                        "expires_in_seconds": 300,
                        "max_bytes": 10,
                    }
                ],
            },
            "get_asset_batch": {
                "batch_id": "batch-1",
                "items": [{"video_id": "asset-1", "status": "completed", "error": None}],
                "has_more": False,
            },
            "bulk_asset_statuses": {
                "items": [{"video_id": "asset-1", "status": "completed", "error": None}],
                "has_more": False,
            },
            "complete_asset_batch": {"ok": True},
        }

    async def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=name, input_schema={"type": "object", "properties": properties})
                for name, properties in self.tools.items()
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        self.calls.append((name, arguments))
        return types.CallToolResult(content=[], structured_content=self.responses[name])


def _adapter(session: FakeSession, **kwargs: Any) -> HeyGenMcpAdapter:
    @asynccontextmanager
    async def factory(_interactive: bool) -> AsyncIterator[FakeSession]:
        yield session

    return HeyGenMcpAdapter(
        storage=KeyringTokenStorage(backend=object()), session_factory=factory, **kwargs
    )


def test_preflight_requires_tools_and_fingerprints_account() -> None:
    session = FakeSession()
    preflight = _adapter(session).preflight()

    assert preflight.connected
    assert preflight.account_ref == "account-" + __import__("hashlib").sha256(
        b"username:user-one"
    ).hexdigest()
    assert set(preflight.capabilities) == set(TOOLS)
    assert preflight.credits_remaining == 12
    assert "example.com" not in preflight.model_dump_json()


def test_preflight_rejects_missing_tool_or_schema_field() -> None:
    missing_tool = dict(TOOLS)
    missing_tool.pop("get_asset")
    with pytest.raises(HeyGenProviderFailure, match="required HeyGen tools"):
        _adapter(FakeSession(tools=missing_tool)).preflight()

    bad_schema = dict(TOOLS)
    bad_schema["create_asset_upload_batch"] = {"unsupported_files": {}}
    with pytest.raises(HeyGenProviderFailure, match="schema"):
        _adapter(FakeSession(tools=bad_schema)).preflight()


def test_allocate_maps_request_and_rejects_unsafe_response() -> None:
    session = FakeSession()
    allocation = _adapter(session).allocate_asset_batch([IMAGE], "heygen.asset.upload:" + "a" * 64)
    assert allocation.batch_id == "batch-1"
    assert session.calls[-1] == (
        "create_asset_upload_batch",
        {
            "files": [
                {
                    "filename": "image.png",
                    "content_type": "image/png",
                    "size_bytes": 4,
                    "checksum_sha256": "1" * 64,
                }
            ],
        },
    )

    session.responses["create_asset_upload_batch"]["items"][0]["upload_url"] = (  # type: ignore[index]
        "http://unsafe.example/upload"
    )
    with pytest.raises(HeyGenProviderFailure, match="invalid upload allocation"):
        _adapter(session).allocate_asset_batch([IMAGE], "heygen.asset.upload:" + "a" * 64)

    session.responses["create_asset_upload_batch"]["items"][0]["upload_url"] = (  # type: ignore[index]
        "https://storage.example/upload"
    )
    session.responses["create_asset_upload_batch"]["items"][0]["max_bytes"] = 3  # type: ignore[index]
    with pytest.raises(HeyGenProviderFailure, match="invalid upload allocation"):
        _adapter(session).allocate_asset_batch([IMAGE], "heygen.asset.upload:" + "a" * 64)


def test_upload_revalidates_size_and_sanitizes_errors(tmp_path: Path) -> None:
    source = tmp_path / "image.png"
    source.write_bytes(b"data")
    session = FakeSession()
    allocation = _adapter(session).allocate_asset_batch([IMAGE], "heygen.asset.upload:" + "a" * 64)
    slot = allocation.slots[0]
    sent: list[tuple[str, bytes, dict[str, str]]] = []

    class HttpClient:
        def put(self, url: str, *, content: object, headers: dict[str, str]) -> object:
            sent.append((url, Path(content.name).read_bytes(), headers))  # type: ignore[attr-defined]
            return type("Response", (), {"raise_for_status": lambda self: None})()

    _adapter(session, http_client=HttpClient()).upload_file(slot, source)
    assert sent == [("https://storage.example/upload", b"data", {"x-upload": "one"})]

    source.write_bytes(b"changed")
    with pytest.raises(HeyGenProviderFailure) as failure:
        _adapter(session).upload_file(slot, source)
    assert failure.value.kind == "terminal"
    assert "storage.example" not in str(failure.value)


def test_parser_accepts_single_json_text_block() -> None:
    session = FakeSession()

    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        payload = session.responses[name]
        return types.CallToolResult(content=[types.TextContent(text=json.dumps(payload))])

    session.call_tool = call_tool  # type: ignore[method-assign]
    assert _adapter(session).preflight().connected


def test_asset_transport_uses_camelcase_and_csv_ids() -> None:
    session = FakeSession()
    provider = _adapter(session)
    provider.complete_asset_batch("batch-1", "local-key")
    assert session.calls[-1] == ("complete_asset_batch", {"batchId": "batch-1"})
    assert provider.get_asset_batch("batch-1").statuses == {"asset-1": ProviderAssetStatus.COMPLETED}
    assert session.calls[-1] == ("get_asset_batch", {"batchId": "batch-1"})
    provider.get_assets(["asset-1", "asset-2"])
    assert session.calls[-1] == ("bulk_asset_statuses", {"assetIds": "asset-1,asset-2"})


def test_parser_accepts_documented_data_envelopes() -> None:
    assert HeyGenMcpAdapter._payload(types.CallToolResult(content=[], structured_content={"data": {"batch_id": "batch-one"}})) == {"batch_id": "batch-one"}
    assert HeyGenMcpAdapter._payload(types.CallToolResult(content=[], structured_content={"data": [{"video_id": "asset-one", "status": "completed"}], "has_more": False})) == {"items": [{"video_id": "asset-one", "status": "completed"}], "has_more": False}


@pytest.mark.parametrize("change", [{"has_more": True}, {"items": [{"video_id": None, "status": "completed"}]}, {"batch_id": "wrong-batch"}])
def test_incomplete_or_wrong_batch_response_never_marks_assets_ready(change: dict[str, object]) -> None:
    session = FakeSession()
    session.responses["get_asset_batch"].update(change)
    with pytest.raises(HeyGenProviderFailure):
        _adapter(session).get_asset_batch("batch-1")


def test_allocation_malformed_response_is_ambiguous() -> None:
    session = FakeSession()
    session.responses["create_asset_upload_batch"] = {}
    with pytest.raises(HeyGenProviderFailure) as error:
        _adapter(session).allocate_asset_batch([IMAGE], "local-key")
    assert error.value.kind == "ambiguous" and error.value.request_dispatched


def test_wrapped_allocation_timeout_remains_ambiguous() -> None:
    class TimeoutSession(FakeSession):
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            raise TimeoutError("secret=value")

    @asynccontextmanager
    async def factory(interactive: bool) -> AsyncIterator[FakeSession]:
        try:
            yield TimeoutSession()
        except Exception as error:
            raise ExceptionGroup("session failed", [error])

    with pytest.raises(HeyGenProviderFailure) as error:
        HeyGenMcpAdapter(session_factory=factory).allocate_asset_batch([IMAGE], "local-key")
    assert error.value.kind == "ambiguous" and error.value.request_dispatched
    assert "secret=value" not in str(error.value)


def test_fake_provider_covers_success_failure_timeout_and_ambiguity() -> None:
    success = FakeHeyGenProvider()
    allocation = success.allocate_asset_batch([IMAGE], "heygen.asset.upload:" + "a" * 64)
    assert success.get_asset_batch(allocation.batch_id).statuses[allocation.slots[0].asset_id] is (
        ProviderAssetStatus.COMPLETED
    )

    scenarios: list[
        tuple[Literal["terminal", "timeout", "ambiguous"], str]
    ] = [
        ("terminal", "terminal"),
        ("timeout", "retryable"),
        ("ambiguous", "ambiguous"),
    ]
    for scenario, kind in scenarios:
        with pytest.raises(HeyGenProviderFailure) as failure:
            FakeHeyGenProvider(scenario=scenario).allocate_asset_batch(
                [IMAGE], "heygen.asset.upload:" + "a" * 64
            )
        assert failure.value.kind == kind
