"""Safe authenticated Flow input preparation and single Generate dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import time
from typing import Protocol

from playwright.sync_api import Locator, Page

from .config import FlowGenerationConfig
from .config import FlowRuntimeConfig
from .generation_domain import (
    FlowDispatchAmbiguousError,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowGenerationUiContractError,
    FlowWorkspaceIdentity,
)
from .generation_locators import (
    _GenerationLocatorTarget,
    _PRODUCTION_GENERATION_TARGET,
    observe_completed_candidate_slots,
    resolve_generate_control,
    resolve_generating_indicator,
    resolve_generation_prompt,
    resolve_reference_input,
    resolve_upload_complete,
)
from .lock import BrowserRuntimeLock
from .runtime import FlowBrowserSession


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CRASH_POINTS = frozenset({"after_intent", "during_click", "before_confirmation"})


class FlowGenerationCheckpointSink(Protocol):
    """Durable checkpoints around the one irreversible provider action."""

    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None: ...

    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None: ...

    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None: ...


class _AuthenticatedFlowSession(Protocol):
    @property
    def page(self) -> Page: ...

    def require_current_flow_page(self) -> None: ...

    def workspace_identity(self) -> FlowWorkspaceIdentity: ...


_AuthenticatedSessionFactory = Callable[[], AbstractContextManager[_AuthenticatedFlowSession]]
InputFileSetter = Callable[[Locator, Path], None]


@dataclass(frozen=True)
class FlowGenerationRequest:
    """Private worker input; its sensitive values never cross a checkpoint boundary."""

    reference_path: Path
    reference_sha256: str
    prompt_snapshot: str
    prompt_sha256: str
    workspace: FlowWorkspaceIdentity


class FlowGenerationRuntime:
    """Perform verified input preparation and one checkpoint-protected Generate click."""

    def __init__(
        self,
        config: FlowGenerationConfig,
        *,
        runtime_config: FlowRuntimeConfig | None = None,
        _session_factory: _AuthenticatedSessionFactory | None = None,
        _locator_target: _GenerationLocatorTarget = _PRODUCTION_GENERATION_TARGET,
        _set_input_files: InputFileSetter | None = None,
        _monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (runtime_config is None) == (_session_factory is None):
            raise ValueError("generation runtime requires exactly one session source")
        self._config = config
        self._runtime_config = runtime_config
        self._session_factory = _session_factory
        self._locator_target = _locator_target
        self._set_input_files = _set_input_files or _playwright_set_input_files
        self._monotonic = _monotonic
        self._crash_point: str | None = None

    def inject_crash(self, crash_point: str) -> None:
        """Private deterministic-test seam for post-intent crash boundaries."""
        if crash_point not in _CRASH_POINTS:
            raise ValueError("unknown generation crash point")
        self._crash_point = crash_point

    def prepare_inputs(
        self,
        *,
        reference_path: Path,
        reference_sha256: str,
        prompt_snapshot: str,
        prompt_sha256: str,
    ) -> FlowGenerationObservation:
        """Upload and read back private inputs without retaining their raw values."""
        self._verify_reference_hash(reference_path, reference_sha256)
        self._verify_prompt_hash(prompt_snapshot, prompt_sha256)
        try:
            with self._open_authenticated_session() as session:
                return self._prepare_inputs_in_session(
                    session,
                    reference_path=reference_path,
                    prompt_snapshot=prompt_snapshot,
                    prompt_sha256=prompt_sha256,
                )
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None

    def prepare_and_dispatch(
        self,
        request: FlowGenerationRequest,
        checkpoint_sink: FlowGenerationCheckpointSink,
    ) -> FlowGenerationObservation:
        """Persist verified inputs, then persist intent before one and only one click."""
        self._verify_reference_hash(request.reference_path, request.reference_sha256)
        self._verify_prompt_hash(request.prompt_snapshot, request.prompt_sha256)
        intent_started = False
        try:
            with self._open_authenticated_session(workspace=request.workspace) as session:
                observation = self._prepare_inputs_in_session(
                    session,
                    reference_path=request.reference_path,
                    prompt_snapshot=request.prompt_snapshot,
                    prompt_sha256=request.prompt_sha256,
                    workspace=request.workspace,
                )
                checkpoint_sink.record_inputs_verified(observation)

                self._require_workspace_identity(session, request.workspace)
                generate = resolve_generate_control(session.page, _target=self._locator_target)
                initial_results = self._completed_result_fingerprints(session.page)

                intent_started = True
                checkpoint_sink.record_dispatch_intent(request.workspace)
                self._require_workspace_identity(session, request.workspace)
                self._raise_if_injected("after_intent")
                generate.click()
                self._raise_if_injected("during_click")
                self._raise_if_injected("before_confirmation")
                self._await_positive_confirmation(session, initial_results, request.workspace)
                checkpoint_sink.record_dispatch_confirmed(observation)
                return observation
        except FlowDispatchAmbiguousError:
            raise
        except FlowGenerationRuntimeError:
            if intent_started:
                raise FlowDispatchAmbiguousError() from None
            raise
        except BaseException:
            if intent_started:
                raise FlowDispatchAmbiguousError() from None
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None

    def reconcile(
        self,
        workspace: FlowWorkspaceIdentity,
        _checkpoint_sink: FlowGenerationCheckpointSink,
    ) -> bool:
        """Validate the persisted workspace without attributing provider state in Task 8."""
        try:
            with self._open_authenticated_session(workspace=workspace) as session:
                self._require_workspace_identity(session, workspace)
                return False
        except FlowDispatchAmbiguousError:
            return False
        except BaseException:
            raise FlowDispatchAmbiguousError() from None

    @contextmanager
    def _open_authenticated_session(
        self,
        *,
        workspace: FlowWorkspaceIdentity | None = None,
    ) -> Iterator[_AuthenticatedFlowSession]:
        """Keep the Goal 4B lock across authenticated browser open, work, and close."""
        if self._session_factory is not None:
            try:
                with self._session_factory() as session:
                    if workspace is not None:
                        self._open_workspace(session, workspace)
                    yield session
            except FlowGenerationRuntimeError:
                raise
            except BaseException:
                raise FlowGenerationRuntimeError(failed_step="open_workspace") from None
            return

        if self._runtime_config is None:
            raise FlowGenerationRuntimeError(failed_step="open_workspace")
        lock = BrowserRuntimeLock(self._runtime_config.lock_path)
        lock_acquired = False
        try:
            lock.acquire()
            lock_acquired = True
            with FlowBrowserSession(self._runtime_config) as session:
                if workspace is not None:
                    session.open_workspace(workspace)
                yield session
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None
        finally:
            if lock_acquired:
                try:
                    lock.release()
                except BaseException:
                    raise FlowGenerationRuntimeError(failed_step="close_browser") from None

    def _prepare_inputs_in_session(
        self,
        session: _AuthenticatedFlowSession,
        *,
        reference_path: Path,
        prompt_snapshot: str,
        prompt_sha256: str,
        workspace: FlowWorkspaceIdentity | None = None,
    ) -> FlowGenerationObservation:
        self._require_session_page(session, workspace)
        try:
            reference_input = resolve_reference_input(
                session.page,
                _target=self._locator_target,
            )
            upload_was_complete = self._upload_complete_present(session.page)
            self._set_input_files(reference_input, reference_path)
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(
                failed_step="upload_reference", failed_locator="REFERENCE_INPUT"
            ) from None

        self._wait_for_upload_transition(session, upload_was_complete, workspace)

        self._require_session_page(session, workspace)
        try:
            prompt = resolve_generation_prompt(session.page, _target=self._locator_target)
            prompt.fill(prompt_snapshot)
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(
                failed_step="fill_prompt", failed_locator="GENERATION_PROMPT"
            ) from None

        self._require_session_page(session, workspace)
        try:
            actual_prompt_hash = _sha256_text(prompt.input_value())
        except BaseException:
            raise FlowGenerationRuntimeError(
                failed_step="verify_prompt", failed_locator="GENERATION_PROMPT"
            ) from None
        if actual_prompt_hash != prompt_sha256:
            raise FlowGenerationRuntimeError(
                failed_step="verify_prompt", failed_locator="GENERATION_PROMPT"
            )
        return FlowGenerationObservation(reference_verified=True, prompt_verified=True)

    def _await_positive_confirmation(
        self,
        session: _AuthenticatedFlowSession,
        initial_results: frozenset[str],
        workspace: FlowWorkspaceIdentity,
    ) -> FlowGenerationObservation:
        deadline = self._monotonic() + self._config.generation_timeout_seconds
        while True:
            self._require_workspace_identity(session, workspace)
            try:
                resolve_generating_indicator(session.page, _target=self._locator_target)
                return FlowGenerationObservation(reference_verified=True, prompt_verified=True)
            except FlowGenerationUiContractError:
                pass

            result_fingerprints = self._completed_result_fingerprints(session.page)
            if result_fingerprints - initial_results:
                return FlowGenerationObservation(reference_verified=True, prompt_verified=True)
            if self._monotonic() >= deadline:
                raise FlowDispatchAmbiguousError()
            session.page.wait_for_timeout(50)

    def _wait_for_upload_transition(
        self,
        session: _AuthenticatedFlowSession,
        upload_was_complete: bool,
        workspace: FlowWorkspaceIdentity | None,
    ) -> None:
        deadline = self._monotonic() + self._config.generation_timeout_seconds
        completion_was_absent = not upload_was_complete
        while True:
            self._require_session_page(session, workspace)
            completion_present = self._upload_complete_present(session.page)
            if completion_present and completion_was_absent:
                return
            if not completion_present:
                completion_was_absent = True
            if self._monotonic() >= deadline:
                raise FlowGenerationRuntimeError(
                    failed_step="verify_reference", failed_locator="UPLOAD_COMPLETE"
                )
            session.page.wait_for_timeout(50)

    def _upload_complete_present(self, page: Page) -> bool:
        try:
            resolve_upload_complete(page, _target=self._locator_target)
            return True
        except FlowGenerationUiContractError:
            return False

    def _completed_result_fingerprints(self, page: Page) -> frozenset[str]:
        try:
            return frozenset(
                candidate.fingerprint
                for candidate in observe_completed_candidate_slots(page, _target=self._locator_target)
            )
        except FlowGenerationUiContractError:
            return frozenset()

    def _require_current_flow_page(self, session: _AuthenticatedFlowSession) -> None:
        try:
            session.require_current_flow_page()
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None

    def _require_session_page(
        self,
        session: _AuthenticatedFlowSession,
        workspace: FlowWorkspaceIdentity | None,
    ) -> None:
        if workspace is None:
            self._require_current_flow_page(session)
            return
        self._require_workspace_identity(session, workspace)

    def _require_workspace_identity(
        self,
        session: _AuthenticatedFlowSession,
        expected: FlowWorkspaceIdentity,
    ) -> None:
        try:
            actual = session.workspace_identity()
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None
        if actual != expected:
            raise FlowGenerationRuntimeError(failed_step="open_workspace")

    @staticmethod
    def _open_workspace(
        session: _AuthenticatedFlowSession,
        workspace: FlowWorkspaceIdentity,
    ) -> None:
        opener = getattr(session, "open_workspace", None)
        if callable(opener):
            opener(workspace)

    def _verify_reference_hash(self, reference_path: Path, expected_hash: str) -> None:
        if not _is_sha256(expected_hash):
            raise FlowGenerationRuntimeError(failed_step="verify_reference")
        try:
            actual_hash = hashlib.sha256(reference_path.read_bytes()).hexdigest()
        except OSError:
            raise FlowGenerationRuntimeError(failed_step="verify_reference") from None
        if actual_hash != expected_hash:
            raise FlowGenerationRuntimeError(failed_step="verify_reference")

    @staticmethod
    def _verify_prompt_hash(prompt_snapshot: str, expected_hash: str) -> None:
        if not _is_sha256(expected_hash) or _sha256_text(prompt_snapshot) != expected_hash:
            raise FlowGenerationRuntimeError(failed_step="verify_prompt")

    def _raise_if_injected(self, crash_point: str) -> None:
        if self._crash_point == crash_point:
            self._crash_point = None
            raise RuntimeError("injected generation crash")


def _playwright_set_input_files(locator: Locator, path: Path) -> None:
    locator.set_input_files(path)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return _SHA256.fullmatch(value) is not None
