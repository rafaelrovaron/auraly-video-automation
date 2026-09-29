from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlparse

import httpx
import httpx2
from mcp import ClientSession, types
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata
from pydantic import AnyUrl

from auraly_pipeline.heygen.auth import KeyringTokenStorage, LoopbackOAuthCallback
from auraly_pipeline.heygen.domain import (
    AssetBatchAllocation,
    AssetBatchState,
    AssetSource,
    AssetUploadSlot,
    HeyGenPreflight,
    ProviderAssetStatus,
)


HEYGEN_MCP_URL = "https://mcp.heygen.com/mcp/v1/"
REQUIRED_TOOLS = {
    "get_current_user",
    "create_asset_upload_batch",
    "complete_asset_batch",
    "get_asset_batch",
    "bulk_asset_statuses",
    "get_asset",
}
_REQUIRED_PROPERTIES = {
    "create_asset_upload_batch": {"files", "idempotency_key"},
    "complete_asset_batch": {"batch_id"},
    "get_asset_batch": {"batch_id"},
    "get_asset": {"asset_id"},
}


class HeyGenProviderFailure(RuntimeError):
    def __init__(
        self,
        kind: Literal["configuration", "retryable", "terminal", "ambiguous"],
        public_message: str,
        *,
        request_dispatched: bool = False,
    ) -> None:
        super().__init__(public_message)
        self.kind = kind
        self.public_message = public_message
        self.request_dispatched = request_dispatched


class HeyGenProvider(Protocol):
    def connect(self) -> HeyGenPreflight: ...
    def preflight(self) -> HeyGenPreflight: ...
    def allocate_asset_batch(
        self, sources: Sequence[AssetSource], idempotency_key: str
    ) -> AssetBatchAllocation: ...
    def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None: ...
    def complete_asset_batch(self, batch_id: str, idempotency_key: str) -> None: ...
    def get_asset_batch(self, batch_id: str) -> AssetBatchState: ...
    def get_assets(self, asset_ids: Sequence[str]) -> AssetBatchState: ...


SessionFactory = Callable[[bool], AbstractAsyncContextManager[Any]]


class HeyGenMcpAdapter:
    def __init__(
        self,
        *,
        storage: KeyringTokenStorage | None = None,
        session_factory: SessionFactory | None = None,
        http_client: Any = None,
    ) -> None:
        self.storage = storage or KeyringTokenStorage()
        self._session_factory = session_factory or self._default_session
        self._http_client = http_client

    @asynccontextmanager
    async def _default_session(self, interactive: bool) -> AsyncIterator[ClientSession]:
        callback = LoopbackOAuthCallback()
        metadata = OAuthClientMetadata(
            client_name="Auraly local video automation",
            redirect_uris=[AnyUrl(callback.redirect_uri)],
        )
        oauth = OAuthClientProvider(
            HEYGEN_MCP_URL,
            metadata,
            self.storage,
            redirect_handler=callback.redirect_handler if interactive else None,
            callback_handler=callback.callback_handler if interactive else None,
        )
        async with httpx2.AsyncClient(auth=oauth) as client:
            async with streamable_http_client(HEYGEN_MCP_URL, http_client=client) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    yield session

    @staticmethod
    def _payload(result: types.CallToolResult) -> dict[str, Any]:
        if result.is_error:
            raise HeyGenProviderFailure("terminal", "HeyGen rejected the request")
        payload = result.structured_content
        if payload is None and len(result.content) == 1:
            block = result.content[0]
            if isinstance(block, types.TextContent):
                try:
                    payload = json.loads(block.text)
                except json.JSONDecodeError as error:
                    raise HeyGenProviderFailure(
                        "terminal", "HeyGen returned an invalid response"
                    ) from error
        if not isinstance(payload, dict):
            raise HeyGenProviderFailure("terminal", "HeyGen returned an invalid response")
        return cast(dict[str, Any], payload)

    async def _call_tool(
        self, session: Any, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            result = await session.call_tool(name, arguments)
        except BaseException as error:
            raise HeyGenProviderFailure(
                "ambiguous", "HeyGen request outcome is unknown", request_dispatched=True
            ) from error
        return self._payload(result)

    async def _preflight(self, interactive: bool) -> HeyGenPreflight:
        try:
            async with self._session_factory(interactive) as session:
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                if not REQUIRED_TOOLS.issubset(tools):
                    raise HeyGenProviderFailure(
                        "configuration", "Missing required HeyGen tools"
                    )
                for name, required in _REQUIRED_PROPERTIES.items():
                    properties = tools[name].input_schema.get("properties", {})
                    if not required.issubset(properties):
                        raise HeyGenProviderFailure(
                            "configuration", f"Invalid HeyGen tool schema: {name}"
                        )
                bulk_properties = tools["bulk_asset_statuses"].input_schema.get(
                    "properties", {}
                )
                if not ({"asset_ids", "batch_ids"} & set(bulk_properties)):
                    raise HeyGenProviderFailure(
                        "configuration", "Invalid HeyGen tool schema: bulk_asset_statuses"
                    )
                user = await self._call_tool(session, "get_current_user", {})
        except HeyGenProviderFailure:
            raise
        except BaseException as error:
            raise HeyGenProviderFailure(
                "configuration", "HeyGen OAuth connection is unavailable"
            ) from error

        stable_id = user.get("workspace_id") or user.get("workspaceId") or user.get("id")
        if not isinstance(stable_id, str) or not stable_id:
            raise HeyGenProviderFailure("configuration", "HeyGen account identity is unavailable")
        account_ref = "account-" + hashlib.sha256(stable_id.encode()).hexdigest()
        credits = user.get("credits")
        return HeyGenPreflight(
            connected=True,
            account_ref=account_ref,
            capabilities=sorted(REQUIRED_TOOLS),
            max_batch_size=100,
            credits_remaining=float(credits) if isinstance(credits, (int, float)) else None,
        )

    def connect(self) -> HeyGenPreflight:
        return asyncio.run(self._preflight(True))

    def preflight(self) -> HeyGenPreflight:
        return asyncio.run(self._preflight(False))

    def disconnect(self) -> None:
        asyncio.run(self.storage.clear())

    async def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session_factory(False) as session:
                return await self._call_tool(session, name, arguments)
        except HeyGenProviderFailure:
            raise
        except BaseException as error:
            raise HeyGenProviderFailure("retryable", "HeyGen connection failed") from error

    def allocate_asset_batch(
        self, sources: Sequence[AssetSource], idempotency_key: str
    ) -> AssetBatchAllocation:
        payload = asyncio.run(
            self._call(
                "create_asset_upload_batch",
                {
                    "files": [
                        {
                            "filename": PurePosixPath(source.local_path).name,
                            "content_type": source.mime_type,
                            "size_bytes": source.size_bytes,
                            "checksum_sha256": source.sha256,
                        }
                        for source in sources
                    ],
                    "idempotency_key": idempotency_key,
                },
            )
        )
        raw_slots = payload.get("files") or payload.get("slots")
        batch_id = payload.get("batch_id") or payload.get("batchId")
        if not isinstance(batch_id, str) or not isinstance(raw_slots, list) or len(raw_slots) != len(sources):
            raise HeyGenProviderFailure("terminal", "HeyGen returned an invalid upload allocation")
        slots: list[AssetUploadSlot] = []
        try:
            for source, raw in zip(sources, raw_slots, strict=True):
                if not isinstance(raw, dict):
                    raise ValueError
                upload_url = raw.get("upload_url") or raw.get("uploadUrl")
                asset_id = raw.get("asset_id") or raw.get("assetId")
                expires = raw.get("expires_in_seconds") or raw.get("expiresInSeconds")
                max_bytes = raw.get("max_bytes") or raw.get("maxBytes")
                headers = raw.get("upload_headers") or raw.get("uploadHeaders") or {}
                if (
                    not isinstance(upload_url, str)
                    or urlparse(upload_url).scheme != "https"
                    or not isinstance(asset_id, str)
                    or not isinstance(expires, int)
                    or not isinstance(max_bytes, int)
                    or not isinstance(headers, dict)
                    or not all(isinstance(key, str) and isinstance(value, str) for key, value in headers.items())
                ):
                    raise ValueError
                slots.append(
                    AssetUploadSlot(
                        source_id=source.source_id,
                        asset_id=asset_id,
                        upload_url=upload_url,
                        upload_headers=headers,
                        size_bytes=source.size_bytes,
                        expires_in_seconds=expires,
                        max_bytes=max_bytes,
                    )
                )
            return AssetBatchAllocation(batch_id=batch_id, slots=slots)
        except (TypeError, ValueError) as error:
            raise HeyGenProviderFailure(
                "terminal", "HeyGen returned an invalid upload allocation"
            ) from error

    def upload_file(self, slot: AssetUploadSlot, local_path: Path) -> None:
        if not local_path.is_file() or local_path.stat().st_size != slot.size_bytes:
            raise HeyGenProviderFailure("terminal", "Local upload source changed")
        client = self._http_client or httpx.Client()
        close_client = self._http_client is None
        try:
            with local_path.open("rb") as source:
                response = client.put(slot.upload_url, content=source, headers=slot.upload_headers)
                response.raise_for_status()
        except HeyGenProviderFailure:
            raise
        except BaseException as error:
            raise HeyGenProviderFailure(
                "ambiguous", "HeyGen upload outcome is unknown", request_dispatched=True
            ) from error
        finally:
            if close_client:
                client.close()

    def complete_asset_batch(self, batch_id: str, idempotency_key: str) -> None:
        asyncio.run(
            self._call(
                "complete_asset_batch",
                {"batch_id": batch_id, "idempotency_key": idempotency_key},
            )
        )

    @staticmethod
    def _batch_state(payload: dict[str, Any], fallback_batch_id: str) -> AssetBatchState:
        raw_assets = payload.get("assets") or payload.get("files")
        if not isinstance(raw_assets, list):
            raise HeyGenProviderFailure("terminal", "HeyGen returned invalid asset statuses")
        statuses: dict[str, ProviderAssetStatus] = {}
        error_codes: dict[str, str] = {}
        error_messages: dict[str, str] = {}
        try:
            for item in raw_assets:
                asset_id = item.get("asset_id") or item.get("assetId")
                statuses[asset_id] = ProviderAssetStatus(item["status"])
                error = item.get("error") or {}
                if error.get("code"):
                    error_codes[asset_id] = error["code"]
                if error.get("message"):
                    error_messages[asset_id] = error["message"]
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise HeyGenProviderFailure("terminal", "HeyGen returned invalid asset statuses") from error
        return AssetBatchState(
            batch_id=payload.get("batch_id") or payload.get("batchId") or fallback_batch_id,
            statuses=statuses,
            error_codes=error_codes,
            error_messages=error_messages,
        )

    def get_asset_batch(self, batch_id: str) -> AssetBatchState:
        return self._batch_state(
            asyncio.run(self._call("get_asset_batch", {"batch_id": batch_id})), batch_id
        )

    def get_assets(self, asset_ids: Sequence[str]) -> AssetBatchState:
        return self._batch_state(
            asyncio.run(self._call("bulk_asset_statuses", {"asset_ids": list(asset_ids)})),
            "assets",
        )
