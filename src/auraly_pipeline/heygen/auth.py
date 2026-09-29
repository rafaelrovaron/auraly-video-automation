from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse
import webbrowser

import keyring
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthToken


class OAuthCallbackError(RuntimeError):
    pass


class KeyringTokenStorage:
    SERVICE_NAME = "auraly.heygen.oauth"

    def __init__(self, *, backend: Any = keyring) -> None:
        self._backend = backend

    async def get_tokens(self) -> OAuthToken | None:
        raw = self._backend.get_password(self.SERVICE_NAME, "tokens")
        return None if raw is None else OAuthToken.model_validate_json(raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._backend.set_password(self.SERVICE_NAME, "tokens", tokens.model_dump_json())

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        raw = self._backend.get_password(self.SERVICE_NAME, "client-info")
        return None if raw is None else OAuthClientInformationFull.model_validate_json(raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._backend.set_password(
            self.SERVICE_NAME, "client-info", client_info.model_dump_json()
        )

    async def clear(self) -> None:
        for username in ("tokens", "client-info"):
            if self._backend.get_password(self.SERVICE_NAME, username) is not None:
                self._backend.delete_password(self.SERVICE_NAME, username)

    async def has_session(self) -> bool:
        return self._backend.get_password(self.SERVICE_NAME, "tokens") is not None


class LoopbackOAuthCallback:
    def __init__(self) -> None:
        self._results: queue.Queue[AuthorizationCodeResult | OAuthCallbackError] = queue.Queue(1)
        self._consumed = False
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                values = {
                    key: items[0]
                    for key, items in parse_qs(urlparse(self.path).query).items()
                    if items
                }
                try:
                    callback.receive(values)
                except OAuthCallbackError as error:
                    callback._put_error(error)
                    self.send_response(400)
                    body = b"OAuth callback rejected. You can close this tab."
                else:
                    self.send_response(200)
                    body = b"HeyGen connected. You can close this tab."
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        address = self._server.server_address
        return str(address[0]), int(address[1])

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.address[1]}/callback"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._server.handle_request, daemon=True)
        self._thread.start()

    def receive(self, values: Mapping[str, str]) -> None:
        code = values.get("code")
        if not code:
            raise OAuthCallbackError("OAuth callback is missing code")
        try:
            self._results.put_nowait(
                AuthorizationCodeResult(
                    code=code,
                    state=values.get("state"),
                    iss=values.get("iss"),
                )
            )
        except queue.Full as error:
            raise OAuthCallbackError("OAuth callback was already received") from error

    def _put_error(self, error: OAuthCallbackError) -> None:
        try:
            self._results.put_nowait(error)
        except queue.Full:
            pass

    def wait(self, *, timeout_seconds: float) -> AuthorizationCodeResult:
        if self._consumed:
            raise OAuthCallbackError("OAuth callback was already consumed")
        self._consumed = True
        try:
            result = self._results.get(timeout=timeout_seconds)
        except queue.Empty as error:
            raise OAuthCallbackError("OAuth callback timed out") from error
        finally:
            self._server.server_close()
        if isinstance(result, OAuthCallbackError):
            raise result
        return result

    async def redirect_handler(self, authorization_url: str) -> None:
        self.start()
        await asyncio.to_thread(webbrowser.open, authorization_url)

    async def callback_handler(self) -> AuthorizationCodeResult:
        return await asyncio.to_thread(self.wait, timeout_seconds=300)
