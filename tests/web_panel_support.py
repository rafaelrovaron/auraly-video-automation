from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from threading import Event, Thread
import time
from typing import Any

import httpx
import pytest
import uvicorn

from auraly_pipeline.api.action_contracts import ImagePrepareOperation, LocalOperationRequest, OperationResult
from auraly_pipeline.api.app import create_app
from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.contracts import ApiSettings
from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from tests.api_helpers import create_ready_api_fixture
from tests.test_campaign_domain import valid_campaign_data

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class PanelServers:
    ui_url: str
    settings: ApiSettings
    entered: Event
    release: Event
    app: Any
    job_ids: list[str]
    provider: Any


def reserve_port(port: int) -> socket.socket:
    owned = socket.socket()
    if sys.platform == 'win32':
        owned.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        owned.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        owned.bind(('127.0.0.1', port))
        owned.listen()
        return owned
    except OSError:
        owned.close()
        raise ValueError('Local panel port is occupied; stop your local dev server before tests.') from None


@pytest.fixture(name='panel_servers')
def panel_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mp4: bytes) -> Iterator[PanelServers]:
    import auraly_pipeline.api.app as module

    node = shutil.which('node')
    vite = ROOT / 'web' / 'node_modules' / 'vite' / 'bin' / 'vite.js'
    if node is None or not vite.is_file():
        pytest.fail('Prepare Node 22 and npm --prefix web ci before browser tests.')
    api_socket = reserve_port(8000)
    try:
        probe = reserve_port(5173)
        probe.close()
    except Exception:
        api_socket.close()
        raise
    entered, release = Event(), Event()
    server: uvicorn.Server | None = None
    thread: Thread | None = None
    process: subprocess.Popen[bytes] | None = None
    provider = FakeHeyGenProvider()
    try:
        settings, _ = create_ready_api_fixture(tmp_path, mp4)
        original = ApiCommands.execute_operation

        def holding(self: ApiCommands, request: LocalOperationRequest, *, job_id: str | None = None) -> OperationResult:
            if isinstance(request, ImagePrepareOperation):
                entered.set()
                if not release.wait(30):
                    raise RuntimeError('Test did not release active Job.')
            return original(self, request, job_id=job_id)

        monkeypatch.setattr(ApiCommands, 'execute_operation', holding)
        monkeypatch.setattr(module, 'ApiCommands', partial(ApiCommands, heygen_provider=provider))
        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8000, log_config=None, access_log=False, ws='none'))
        thread = Thread(target=server.run, kwargs={'sockets': [api_socket]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail('Local API did not start for browser tests.')
            time.sleep(0.05)
        data = valid_campaign_data()
        data['campaignId'] = 'campaign-two'
        app.state.commands.campaigns.create_campaign(CampaignCreate.model_validate(data))
        job_ids = [app.state.commands.submit_operation(ImagePrepareOperation(
            campaign_id='campaign-one', output_path=f'panel-inbox-{index}',
        )).job_id for index in range(2)]
        creation_flags = 0
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            [node, str(vite), '--host', '127.0.0.1', '--port', '5173', '--strictPort'],
            cwd=ROOT / 'web', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        deadline = time.monotonic() + 15
        with httpx.Client(timeout=0.5) as client:
            while True:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail('Local Vite proxy did not start for browser tests.')
                try:
                    if client.get('http://127.0.0.1:5173/health').status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        yield PanelServers('http://127.0.0.1:5173/', settings, entered, release, app, job_ids, provider)
    finally:
        release.set()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=15)
            assert not thread.is_alive(), 'Local API fixture did not drain.'
        api_socket.close()
