from __future__ import annotations

import asyncio
from urllib.request import urlopen

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from auraly_pipeline.heygen.auth import (
    KeyringTokenStorage,
    LoopbackOAuthCallback,
    OAuthCallbackError,
)


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_storage_round_trips_tokens_and_client_registration() -> None:
    backend = MemoryKeyring()
    storage = KeyringTokenStorage(backend=backend)
    tokens = OAuthToken(access_token="access-token", refresh_token="refresh-token")
    client = OAuthClientInformationFull(client_id="client-id", client_secret="client-secret")

    async def exercise() -> None:
        await storage.set_tokens(tokens)
        await storage.set_client_info(client)
        assert await storage.get_tokens() == tokens
        assert await storage.get_client_info() == client
        assert await storage.has_session()
        await storage.clear()
        assert not await storage.has_session()

    asyncio.run(exercise())
    assert not backend.values


def test_loopback_callback_accepts_one_complete_callback_only() -> None:
    callback = LoopbackOAuthCallback()
    callback.receive({"code": "code", "state": "state", "iss": "issuer"})

    assert callback.wait(timeout_seconds=1).state == "state"
    with pytest.raises(OAuthCallbackError):
        callback.wait(timeout_seconds=1)


def test_loopback_callback_times_out_and_rejects_missing_code() -> None:
    callback = LoopbackOAuthCallback()
    with pytest.raises(OAuthCallbackError, match="timed out"):
        callback.wait(timeout_seconds=0.01)

    callback = LoopbackOAuthCallback()
    with pytest.raises(OAuthCallbackError, match="missing code"):
        callback.receive({"state": "state"})


def test_loopback_http_server_binds_only_to_loopback() -> None:
    callback = LoopbackOAuthCallback()
    callback.start()
    assert callback.address[0] == "127.0.0.1"

    with urlopen(f"{callback.redirect_uri}?code=code&state=state", timeout=2) as response:
        assert response.status == 200
    assert callback.wait(timeout_seconds=1).code == "code"
