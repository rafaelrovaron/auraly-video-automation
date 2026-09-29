from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import time

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.heygen.domain import (
    AssetSource,
    AssetUploadJobInput,
    ProviderAssetStatus,
    RemoteAssetStatus,
)
from auraly_pipeline.heygen.provider import HeyGenProvider, HeyGenProviderFailure
from auraly_pipeline.heygen.repository import RemoteAssetRepository
from auraly_pipeline.jobs.domain import JobExecutionOutcome, JobExecutionResult, RetrySafety
from auraly_pipeline.jobs.handlers import JobExecutionContext


class HeyGenAssetUploadHandler:
    retry_safety = RetrySafety.RECONCILE_BEFORE_RETRY

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: HeyGenProvider,
        work_root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
        poll_interval_seconds: float = 2.0,
        poll_timeout_seconds: float = 300.0,
    ) -> None:
        self._repository = RemoteAssetRepository(session_factory)
        self._provider = provider
        self._work_root = work_root.resolve()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleeper or time.sleep
        self._poll_interval = poll_interval_seconds
        self._poll_timeout = poll_timeout_seconds

    @staticmethod
    def _failure(
        outcome: JobExecutionOutcome, code: str, message: str
    ) -> JobExecutionResult:
        return JobExecutionResult(outcome=outcome, error_code=code, error_message=message)

    def _resolve_source(self, source: AssetSource) -> Path:
        path = (self._work_root / source.local_path).resolve()
        if not path.is_relative_to(self._work_root) or not path.is_file():
            raise ValueError("Local upload source is unavailable")
        if path.stat().st_size != source.size_bytes:
            raise ValueError("Local upload source changed")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.sha256:
            raise ValueError("Local upload source changed")
        return path

    def _provider_failure(self, failure: HeyGenProviderFailure) -> JobExecutionResult:
        outcome = {
            "configuration": JobExecutionOutcome.BLOCKED,
            "retryable": JobExecutionOutcome.RETRYABLE_FAILURE,
            "terminal": JobExecutionOutcome.TERMINAL_FAILURE,
            "ambiguous": JobExecutionOutcome.BLOCKED,
        }[failure.kind]
        return self._failure(outcome, f"heygen_{failure.kind}", failure.public_message)

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        try:
            request = AssetUploadJobInput.model_validate(context.input)
        except ValidationError:
            return self._failure(
                JobExecutionOutcome.TERMINAL_FAILURE,
                "invalid_job_input",
                "The HeyGen upload job input is invalid.",
            )
        try:
            preflight = self._provider.preflight()
        except HeyGenProviderFailure as failure:
            return self._provider_failure(failure)
        if preflight.account_ref != request.account_ref:
            return self._failure(
                JobExecutionOutcome.BLOCKED,
                "heygen_account_mismatch",
                "The connected HeyGen account does not match the upload plan.",
            )
        try:
            local_paths = {
                source.source_id: self._resolve_source(source) for source in request.sources
            }
        except ValueError:
            return self._failure(
                JobExecutionOutcome.TERMINAL_FAILURE,
                "local_asset_changed",
                "An approved local asset changed before upload.",
            )

        ready = {
            (asset.kind, asset.sha256)
            for asset in self._repository.find_by_keys(request.account_ref, request.sources)
            if asset.status is RemoteAssetStatus.READY
        }
        pending = [
            source
            for source in request.sources
            if (source.kind, source.sha256) not in ready
        ]
        if not pending:
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS,
                result={"uploaded": 0, "reused": len(request.sources)},
            )
        try:
            allocation = self._provider.allocate_asset_batch(
                pending, request.idempotency_key
            )
            self._repository.record_allocation(
                request.account_ref,
                allocation.batch_id,
                pending,
                allocation.slots,
                self._clock(),
            )
            for slot in allocation.slots:
                self._provider.upload_file(slot, local_paths[slot.source_id])
            self._provider.complete_asset_batch(
                allocation.batch_id, f"{request.idempotency_key}:complete"
            )
        except HeyGenProviderFailure as failure:
            if "allocation" in locals() and failure.kind == "ambiguous":
                self._repository.mark_reconciliation_required(
                    allocation.batch_id,
                    "ambiguous_remote_result",
                    failure.public_message,
                    self._clock(),
                )
            return self._provider_failure(failure)

        deadline = time.monotonic() + self._poll_timeout
        while True:
            try:
                state = self._provider.get_asset_batch(allocation.batch_id)
            except HeyGenProviderFailure as failure:
                if failure.kind == "ambiguous":
                    self._repository.mark_reconciliation_required(
                        allocation.batch_id,
                        "ambiguous_remote_result",
                        failure.public_message,
                        self._clock(),
                    )
                return self._provider_failure(failure)
            assets = self._repository.apply_batch_state(state, self._clock())
            if not any(
                status in {ProviderAssetStatus.QUEUED, ProviderAssetStatus.PROCESSING}
                for status in state.statuses.values()
            ):
                break
            if time.monotonic() >= deadline:
                return self._failure(
                    JobExecutionOutcome.BLOCKED,
                    "heygen_poll_timeout",
                    "HeyGen assets are still processing and require reconciliation.",
                )
            self._sleep(self._poll_interval)

        failed = sum(asset.status is RemoteAssetStatus.FAILED for asset in assets)
        if failed:
            return self._failure(
                JobExecutionOutcome.TERMINAL_FAILURE,
                "heygen_asset_failed",
                "One or more HeyGen assets failed processing.",
            )
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={
                "batchId": allocation.batch_id,
                "uploaded": len(pending),
                "reused": len(request.sources) - len(pending),
            },
        )
