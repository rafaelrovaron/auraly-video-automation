from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from uuid import uuid4

from auraly_pipeline.api.action_contracts import WorkerKind, WorkerState
from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.contracts import ErrorCode, QueryError

JOB_TYPES: dict[WorkerKind, str] = {
    "local_operations": "api.local.operation", "voice_generate": "voice.generate",
    "voice_import": "voice.import", "heygen_assets": "heygen.asset.upload",
    "heygen_videos": "heygen.video.generate",
    "editing_render": "editing.render",
}


class LocalApiWorker:
    def __init__(self, commands: ApiCommands) -> None:
        self.commands = commands
        self._lock = Lock()
        self._stop = Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="auraly-api-worker")
        self._future: Future[None] | None = None
        self._state = WorkerState()
        self._closed = False

    def start(self, campaign_id: str, kind: WorkerKind) -> WorkerState:
        self.commands.require_campaign(campaign_id)
        if kind not in JOB_TYPES:
            raise QueryError("invalid_request")
        with self._lock:
            if self._closed or self._state.state != "idle":
                raise QueryError("operation_conflict")
            self._stop.clear()
            self._state = WorkerState(state="running", campaign_id=campaign_id, kind=kind)
            self._future = self._executor.submit(self._run, campaign_id, kind)
            return self._state.model_copy()

    def _check_scope(self, campaign_id: str) -> None:
        if self._state.campaign_id is not None and self._state.campaign_id != campaign_id:
            raise QueryError("not_found")

    def status(self, campaign_id: str) -> WorkerState:
        self.commands.require_campaign(campaign_id)
        with self._lock:
            self._check_scope(campaign_id)
            return self._state.model_copy()

    def stop(self, campaign_id: str) -> WorkerState:
        self.commands.require_campaign(campaign_id)
        with self._lock:
            self._check_scope(campaign_id)
            self._stop.set()
            if self._state.state == "running":
                self._state = self._state.model_copy(update={"state": "stopping"})
            return self._state.model_copy()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._stop.set()
            if self._state.state == "running":
                self._state = self._state.model_copy(update={"state": "stopping"})
        # Never hold admission while waiting: active work needs it to record completion.
        self._executor.shutdown(wait=True)

    def _run(self, campaign_id: str, kind: WorkerKind) -> None:
        error_code: ErrorCode | None = None
        try:
            if kind == "heygen_videos":
                self.commands.heygen_videos.run_videos(campaign_id, stop_requested=self._stop.is_set)
            else:
                worker_id = "api-worker-" + str(uuid4())
                jobs = self.commands.heygen_jobs if kind == "heygen_assets" else self.commands.jobs
                while not self._stop.is_set():
                    job = (
                        self.commands.voices.worker_once(
                            worker_id, campaign_id=campaign_id, job_type=JOB_TYPES[kind],
                        ) if kind == "voice_generate" else jobs.worker_once(
                            worker_id, campaign_id=campaign_id, job_type=JOB_TYPES[kind],
                        )
                    )
                    if job is None:
                        break
        except QueryError as error:
            error_code = error.code
        except Exception:
            error_code = "internal_error"
        finally:
            with self._lock:
                self._state = WorkerState(
                    state="idle", campaign_id=campaign_id, kind=kind, error_code=error_code,
                )
