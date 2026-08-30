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

from playwright.sync_api import Download, Locator, Page, TimeoutError as PlaywrightTimeoutError

from .artifacts import (
    FlowArtifactConflictError,
    FlowArtifactFacts,
    FlowArtifactInvalidError,
    allocate_flow_staging_path,
    inspect_flow_artifact,
    publish_flow_artifact_exclusive,
    resolve_flow_final_path,
)
from .config import FlowGenerationConfig
from .config import FlowRuntimeConfig
from .diagnostics import FlowGridEvidence, publish_flow_grid_evidence
from .domain import FlowUnexpectedStateError
from .generation_domain import (
    FlowCandidateObservation,
    FlowDispatchAmbiguousError,
    FlowDownloadCorrelationError,
    FlowGenerationObservation,
    FlowGenerationRuntimeError,
    FlowGenerationUiContractError,
    FlowWorkspaceIdentity,
)
from .generation_locators import (
    _GenerationLocatorTarget,
    _PRODUCTION_GENERATION_TARGET,
    observe_completed_candidate_slots,
    resolve_candidate_2k_action,
    resolve_generate_control,
    resolve_generating_indicator,
    resolve_generation_prompt,
    resolve_reference_input,
    resolve_upload_complete,
)
from .lock import BrowserRuntimeLock
from .locators import blocking_overlay_present
from .runtime import FlowBrowserSession


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CRASH_POINTS = frozenset(
    {
        "after_intent",
        "during_click",
        "before_confirmation",
        "after_download_event",
        "after_download_save",
        "after_download_checkpoint",
        "after_download_publication",
    }
)
_EVIDENCE_MASK_LABELS = (
    "Account identity",
    "Prompt",
    "Reference preview",
    "Upload filename",
)


class FlowGenerationCheckpointSink(Protocol):
    """Durable checkpoints around the one irreversible provider action."""

    def record_inputs_verified(self, observation: FlowGenerationObservation) -> None: ...

    def record_dispatch_intent(self, workspace: FlowWorkspaceIdentity) -> None: ...

    def record_dispatch_confirmed(self, observation: FlowGenerationObservation) -> None: ...


class FlowGenerationDownloadCheckpointSink(FlowGenerationCheckpointSink, Protocol):
    """Durable candidate/evidence/download checkpoints added by Task 9."""

    def bind_candidate_slot(
        self, slot_index: int, observation: FlowCandidateObservation
    ) -> None: ...

    def record_candidates_observed(self, evidence: FlowGridEvidence) -> None: ...

    def candidate_fingerprint(self, slot_index: int) -> str: ...

    def record_download_intent(self, slot_index: int, fingerprint: str) -> None: ...

    def record_downloaded(
        self,
        slot_index: int,
        *,
        relative_path: str,
        sha256: str,
    ) -> None: ...


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


@dataclass(frozen=True)
class FlowGenerationArtifactContext:
    """Trusted generation identity and work root for evidence and two artifacts."""

    campaign_id: str
    scene_variant_id: str
    generation_number: int
    work_root: Path
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
        artifact_context: FlowGenerationArtifactContext | None = None,
    ) -> None:
        if (runtime_config is None) == (_session_factory is None):
            raise ValueError("generation runtime requires exactly one session source")
        self._config = config
        self._runtime_config = runtime_config
        self._session_factory = _session_factory
        self._locator_target = _locator_target
        self._set_input_files = _set_input_files or _playwright_set_input_files
        self._monotonic = _monotonic
        self._artifact_context = artifact_context
        self._crash_point: str | None = None
        self._inject_unrelated_download = False
        self._download_actions: list[tuple[int, str]] = []

    def inject_crash(self, crash_point: str) -> None:
        """Private deterministic-test seam for post-intent crash boundaries."""
        if crash_point not in _CRASH_POINTS:
            raise ValueError("unknown generation crash point")
        self._crash_point = crash_point

    def inject_unrelated_download_before_slot(self) -> None:
        """Private deterministic seam proving an unrelated event makes correlation fail."""
        self._inject_unrelated_download = True

    @property
    def download_actions(self) -> list[tuple[int, str]]:
        """Safe test-facing record of slot/resolution actions, never provider download data."""
        return list(self._download_actions)

    def observe_and_download(
        self,
        checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> tuple[FlowArtifactFacts, FlowArtifactFacts]:
        """Observe exactly two stable slots and download each bound 2K artifact once."""
        self.observe_candidates(checkpoint_sink)
        first = self.download_slot(0, checkpoint_sink)
        second = self.download_slot(1, checkpoint_sink)
        return first, second

    def observe_candidates(
        self,
        checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> tuple[FlowCandidateObservation, FlowCandidateObservation]:
        """Bind the first two stable semantic candidates and persist masked grid evidence."""
        context = self._require_artifact_context("observe_candidates")
        try:
            with self._open_authenticated_session(workspace=context.workspace) as session:
                observations = self._await_stable_candidates(session, context.workspace)
                for slot_index, observation in enumerate(observations):
                    checkpoint_sink.bind_candidate_slot(slot_index, observation)
                evidence = self._capture_grid_evidence_in_session(
                    session,
                    expected=observations,
                )
                checkpoint_sink.record_candidates_observed(evidence)
                return observations
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="observe_candidates") from None

    def capture_grid_evidence(self) -> FlowGridEvidence:
        """Capture and publish only a screenshot with every private region masked."""
        context = self._require_artifact_context("capture_grid_evidence")
        try:
            with self._open_authenticated_session(workspace=context.workspace) as session:
                return self._capture_grid_evidence_in_session(session, expected=None)
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="capture_grid_evidence") from None

    def download_slot(
        self,
        slot_index: int,
        checkpoint_sink: FlowGenerationDownloadCheckpointSink,
    ) -> FlowArtifactFacts:
        """Correlate one exact persisted slot action to one Playwright download event."""
        context = self._require_artifact_context("capture_download")
        if isinstance(slot_index, bool) or slot_index not in {0, 1}:
            raise FlowDownloadCorrelationError()
        try:
            with self._open_authenticated_session(workspace=context.workspace) as session:
                self._require_workspace_identity(session, context.workspace)
                try:
                    fingerprint = checkpoint_sink.candidate_fingerprint(slot_index)
                    action = resolve_candidate_2k_action(
                        session.page,
                        fingerprint,
                        _target=self._locator_target,
                    )
                except BaseException:
                    raise FlowDownloadCorrelationError() from None

                checkpoint_sink.record_download_intent(slot_index, fingerprint)
                download = self._one_download_from_action(session.page, action, slot_index)
                self._raise_if_injected("after_download_event")
                if download.failure() is not None:
                    raise FlowDownloadCorrelationError()

                staging_path = allocate_flow_staging_path(
                    work_root=context.work_root,
                    campaign_id=context.campaign_id,
                    scene_variant_id=context.scene_variant_id,
                    generation_number=context.generation_number,
                    candidate_index=slot_index,
                )
                try:
                    download.save_as(staging_path)
                except BaseException:
                    raise FlowDownloadCorrelationError() from None
                if download.failure() is not None:
                    raise FlowDownloadCorrelationError()
                self._raise_if_injected("after_download_save")

                staged = inspect_flow_artifact(staging_path)
                relative_staging = staging_path.relative_to(
                    context.work_root.resolve(strict=False)
                ).as_posix()
                checkpoint_sink.record_downloaded(
                    slot_index,
                    relative_path=relative_staging,
                    sha256=staged.sha256,
                )
                self._raise_if_injected("after_download_checkpoint")

                final_path = resolve_flow_final_path(
                    work_root=context.work_root,
                    campaign_id=context.campaign_id,
                    scene_variant_id=context.scene_variant_id,
                    generation_number=context.generation_number,
                    candidate_index=slot_index,
                    image_format=staged.format,
                )
                published = publish_flow_artifact_exclusive(
                    staging_path,
                    final_path,
                    trusted_root=context.work_root,
                )
                self._raise_if_injected("after_download_publication")
                return published
        except (FlowArtifactConflictError, FlowArtifactInvalidError):
            raise
        except FlowDownloadCorrelationError:
            raise
        except BaseException:
            raise FlowDownloadCorrelationError() from None

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
        dispatch_confirmed = False
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
                initial_results = self._completed_result_fingerprints(session.page)

                intent_started = True
                checkpoint_sink.record_dispatch_intent(request.workspace)
                generate = self._fresh_generate_control_after_intent(session, request)
                self._require_workspace_identity(session, request.workspace)
                self._raise_if_injected("after_intent")
                generate.click()
                self._raise_if_injected("during_click")
                self._raise_if_injected("before_confirmation")
                self._await_positive_confirmation(session, initial_results, request.workspace)
                checkpoint_sink.record_dispatch_confirmed(observation)
                dispatch_confirmed = True
                return observation
        except FlowDispatchAmbiguousError:
            raise
        except FlowGenerationRuntimeError:
            if intent_started and not dispatch_confirmed:
                raise FlowDispatchAmbiguousError() from None
            raise
        except BaseException:
            if intent_started and not dispatch_confirmed:
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

    def _await_stable_candidates(
        self,
        session: _AuthenticatedFlowSession,
        workspace: FlowWorkspaceIdentity,
    ) -> tuple[FlowCandidateObservation, FlowCandidateObservation]:
        deadline = self._monotonic() + self._config.generation_timeout_seconds
        previous: tuple[FlowCandidateObservation, FlowCandidateObservation] | None = None
        last_error: FlowGenerationUiContractError | None = None
        while True:
            self._require_workspace_identity(session, workspace)
            try:
                observed = observe_completed_candidate_slots(
                    session.page,
                    _target=self._locator_target,
                )
                last_error = None
            except FlowGenerationUiContractError as error:
                observed = ()
                last_error = error

            if len(observed) >= 2:
                selected = (observed[0], observed[1])
                if previous == selected:
                    return selected
                previous = selected
            else:
                previous = None

            if self._monotonic() >= deadline:
                if last_error is not None:
                    raise last_error
                raise FlowGenerationUiContractError(
                    failed_step="observe_candidates",
                    failed_locator="CANDIDATE_GRID",
                )
            session.page.wait_for_timeout(50)

    def _capture_grid_evidence_in_session(
        self,
        session: _AuthenticatedFlowSession,
        *,
        expected: tuple[FlowCandidateObservation, FlowCandidateObservation] | None,
    ) -> FlowGridEvidence:
        context = self._require_artifact_context("capture_grid_evidence")
        self._require_workspace_identity(session, context.workspace)
        self._require_evidence_candidates(session.page, expected)
        try:
            masks = [
                self._unique_visible_label(session.page, label)
                for label in _EVIDENCE_MASK_LABELS
            ]
            screenshot_png = session.page.screenshot(
                type="png",
                mask=masks,
                mask_color="#FF00FF",
                animations="disabled",
            )
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="capture_grid_evidence") from None

        self._require_workspace_identity(session, context.workspace)
        self._require_evidence_candidates(session.page, expected)
        try:
            inspection_root = self._inspection_root(context)
            local_evidence = publish_flow_grid_evidence(
                screenshot_png,
                evidence_root=inspection_root,
            )
            final_path = inspection_root / local_evidence.relative_path
            relative_path = final_path.relative_to(
                context.work_root.resolve(strict=False)
            ).as_posix()
            return FlowGridEvidence(
                relative_path=relative_path,
                sha256=local_evidence.sha256,
            )
        except BaseException:
            raise FlowGenerationRuntimeError(failed_step="capture_grid_evidence") from None

    def _require_evidence_candidates(
        self,
        page: Page,
        expected: tuple[FlowCandidateObservation, FlowCandidateObservation] | None,
    ) -> None:
        try:
            observed = observe_completed_candidate_slots(page, _target=self._locator_target)
        except FlowGenerationUiContractError:
            raise FlowGenerationUiContractError(
                failed_step="capture_grid_evidence",
                failed_locator="CANDIDATE_SLOT",
            ) from None
        if len(observed) < 2:
            raise FlowGenerationUiContractError(
                failed_step="capture_grid_evidence",
                failed_locator="CANDIDATE_GRID",
            )
        selected = (observed[0], observed[1])
        if expected is not None and selected != expected:
            raise FlowGenerationUiContractError(
                failed_step="capture_grid_evidence",
                failed_locator="CANDIDATE_SLOT",
            )

    @staticmethod
    def _unique_visible_label(page: Page, label: str) -> Locator:
        candidates = tuple(
            candidate
            for candidate in page.get_by_label(label, exact=True).all()
            if candidate.is_visible()
        )
        if len(candidates) != 1:
            raise FlowGenerationRuntimeError(failed_step="capture_grid_evidence")
        return candidates[0]

    def _one_download_from_action(
        self,
        page: Page,
        action: Locator,
        slot_index: int,
    ) -> Download:
        events: list[Download] = []

        def observe(download: Download) -> None:
            events.append(download)

        page.on("download", observe)
        try:
            with page.expect_download(
                timeout=self._config.download_timeout_seconds * 1000
            ) as pending:
                if self._inject_unrelated_download:
                    self._inject_unrelated_download = False
                    unrelated = page.get_by_role(
                        "button", name="Unrelated download", exact=True
                    )
                    matches = tuple(
                        candidate
                        for candidate in unrelated.all()
                        if candidate.is_visible() and candidate.is_enabled()
                    )
                    if len(matches) != 1:
                        raise FlowDownloadCorrelationError()
                    matches[0].click()
                self._download_actions.append((slot_index, "2K"))
                action.click()
            download = pending.value
            page.wait_for_timeout(0)
            if len(events) != 1:
                raise FlowDownloadCorrelationError()
            return download
        except PlaywrightTimeoutError:
            raise FlowDownloadCorrelationError() from None
        finally:
            page.remove_listener("download", observe)

    @staticmethod
    def _inspection_root(context: FlowGenerationArtifactContext) -> Path:
        candidate_path = resolve_flow_final_path(
            work_root=context.work_root,
            campaign_id=context.campaign_id,
            scene_variant_id=context.scene_variant_id,
            generation_number=context.generation_number,
            candidate_index=0,
            image_format="png",
        )
        return candidate_path.parent / "inspection"

    def _require_artifact_context(
        self,
        failed_step: str,
    ) -> FlowGenerationArtifactContext:
        if self._artifact_context is None:
            if failed_step == "capture_download":
                raise FlowDownloadCorrelationError()
            if failed_step == "observe_candidates":
                raise FlowGenerationRuntimeError(failed_step="observe_candidates")
            raise FlowGenerationRuntimeError(failed_step="capture_grid_evidence")
        return self._artifact_context

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
            except FlowUnexpectedStateError as error:
                if error.failed_step == "close_browser":
                    raise FlowGenerationRuntimeError(failed_step="close_browser") from None
                raise FlowGenerationRuntimeError(failed_step="open_workspace") from None
            except FlowGenerationRuntimeError:
                raise
            except (FlowArtifactConflictError, FlowArtifactInvalidError):
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
        except FlowUnexpectedStateError as error:
            if error.failed_step == "close_browser":
                raise FlowGenerationRuntimeError(failed_step="close_browser") from None
            raise FlowGenerationRuntimeError(failed_step="open_workspace") from None
        except FlowGenerationRuntimeError:
            raise
        except (FlowArtifactConflictError, FlowArtifactInvalidError):
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

    def _fresh_generate_control_after_intent(
        self,
        session: _AuthenticatedFlowSession,
        request: FlowGenerationRequest,
    ) -> Locator:
        """Revalidate the mutable dispatch surface and return the one current Generate control."""
        self._require_workspace_identity(session, request.workspace)
        try:
            if blocking_overlay_present(session.page):
                raise FlowGenerationRuntimeError(
                    failed_step="dispatch_generate", failed_locator="GENERATE_CONTROL"
                )
            if not self._upload_complete_present(session.page):
                raise FlowGenerationRuntimeError(
                    failed_step="verify_reference", failed_locator="UPLOAD_COMPLETE"
                )
            prompt = resolve_generation_prompt(session.page, _target=self._locator_target)
            if _sha256_text(prompt.input_value()) != request.prompt_sha256:
                raise FlowGenerationRuntimeError(
                    failed_step="verify_prompt", failed_locator="GENERATION_PROMPT"
                )
            return resolve_generate_control(session.page, _target=self._locator_target)
        except FlowGenerationRuntimeError:
            raise
        except BaseException:
            raise FlowGenerationRuntimeError(
                failed_step="dispatch_generate", failed_locator="GENERATE_CONTROL"
            ) from None

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
        except FlowGenerationUiContractError as error:
            if self._candidate_grid_is_absent(page, error):
                return frozenset()
            raise

    @staticmethod
    def _candidate_grid_is_absent(page: Page, error: FlowGenerationUiContractError) -> bool:
        """Treat only a truly absent, unblocked grid as a valid empty result baseline."""
        if (
            error.failed_step != "observe_candidates"
            or error.failed_locator != "CANDIDATE_GRID"
            or blocking_overlay_present(page)
        ):
            return False
        return not page.get_by_role("list", name="Generated candidates", exact=True).all()

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
