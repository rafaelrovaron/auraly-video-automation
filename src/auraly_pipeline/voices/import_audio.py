from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CopyMasterRow
from auraly_pipeline.campaigns.persistence import create_sqlite_engine, migrate_database
from auraly_pipeline.config_paths import DEFAULT_PROJECT_ROOT, WORK_ROOT_RELATIVE
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.domain import (
    Job,
    JobExecutionOutcome,
    JobExecutionResult,
    JobSubmit,
    RetrySafety,
)
from auraly_pipeline.jobs.handlers import JobExecutionContext
from auraly_pipeline.jobs.repository import JobRepository
from auraly_pipeline.jobs.service import JobService
from auraly_pipeline.voices.audio import _probe, _sha256, decode_external_audio, process_voice_audio
from auraly_pipeline.voices.db_models import VoiceMasterRow
from auraly_pipeline.voices.domain import VoiceImportRequest, VoiceMaster, transcript_comparison
from auraly_pipeline.voices.handler import FasterWhisperTranscriber, TranscriptProvider
from auraly_pipeline.voices.repository import VoiceMasterRepository
from auraly_pipeline.voices.service import VoiceGenerationSubmission, VoiceMasterService


class VoiceImportError(RuntimeError):
    public_message = "The external Voice Master could not be imported safely."


def _safe_path(path: Path, root: Path | None = None) -> Path:
    if ".." in path.parts:
        raise VoiceImportError
    absolute = path.expanduser().absolute()
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise VoiceImportError
        if component.exists():
            info = component.lstat()
            if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise VoiceImportError
    resolved = absolute.resolve()
    if root is not None and not resolved.is_relative_to(root):
        raise VoiceImportError
    return resolved


def _verify_artifacts(row: VoiceMasterRow, root: Path) -> None:
    for name in ("raw", "processed", "transcript", "manifest"):
        path = getattr(
            row, f"{name}_audio_path" if name in {"raw", "processed"} else f"{name}_path"
        )
        digest = getattr(row, f"{name}_sha256")
        if path is not None:
            artifact = _safe_path(root / path, root)
            if not digest or not artifact.is_file() or _sha256(artifact) != digest:
                raise VoiceImportError
    if not row.raw_audio_path:
        raise VoiceImportError


class VoiceImportService:
    def __init__(
        self, sessions: sessionmaker[Session], jobs: JobService, project_root: Path, work_root: Path
    ) -> None:
        self._sessions = sessions
        self._jobs = jobs
        self._project_root = project_root
        self._work_root = work_root

    @classmethod
    def for_database(
        cls,
        database_path: Path,
        *,
        project_root: Path | None = None,
        work_root: Path | None = None,
        transcriber: TranscriptProvider | None = None,
    ) -> VoiceImportService:
        project = _safe_path(
            project_root or Path(os.environ.get("AURALY_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT)
        )
        work = _safe_path(work_root or project / WORK_ROOT_RELATIVE)
        migrate_database(database_path)
        engine = create_sqlite_engine(database_path)
        sessions = sessionmaker(engine, expire_on_commit=False, class_=Session)
        jobs = JobService(
            engine,
            JobRepository(sessions),
            handlers={
                "voice.import": VoiceImportHandler(
                    sessions, work_root=work, transcriber=transcriber
                ),
            },
        )
        return cls(sessions, jobs, project, work)

    def close(self) -> None:
        self._jobs.close()

    def worker_once(self, worker_id: str, *, campaign_id: str) -> Job | None:
        return self._jobs.worker_once(
            worker_id, campaign_id=campaign_id, job_type="voice.import", lease_seconds=300
        )

    def import_audio(
        self, request: VoiceImportRequest, *, source: Path
    ) -> VoiceGenerationSubmission:
        source = _safe_path(source, self._project_root)
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 100 * 1024 * 1024:
            raise VoiceImportError
        audio_format = _probe(source)["format"]
        if audio_format not in {"mp3", "wav"}:
            raise VoiceImportError
        with source.open("rb") as stream:
            data = stream.read(100 * 1024 * 1024 + 1)
        after = source.stat()
        if len(data) != info.st_size or (after.st_ino, after.st_size, after.st_mtime_ns) != (
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
        ):
            raise VoiceImportError
        digest = hashlib.sha256(data).hexdigest()
        with self._sessions() as session:
            copy = VoiceMasterRepository(session).approved_copy(
                request.campaign_id, request.copy_master_version
            )
            if copy is None:
                raise VoiceImportError
            copy_id, version = copy.id, copy.version
        key = VoiceMasterService._hash(
            {
                "campaignId": request.campaign_id,
                "copyMasterId": copy_id,
                "rawSha256": digest,
                "processingVersion": 1,
            }
        )
        voice_id = str(uuid5(NAMESPACE_URL, f"voice.import:{key}"))

        def create(session: Session, job: JobRow) -> VoiceMaster:
            repository = VoiceMasterRepository(session)
            campaign = repository.campaign(request.campaign_id)
            copy = repository.approved_copy(request.campaign_id, version)
            if campaign is None or copy is None or copy.id != copy_id:
                raise VoiceImportError
            if repository.approved_for_campaign(
                request.campaign_id
            ) or repository.active_other_for_campaign(request.campaign_id, voice_id):
                raise VoiceImportError
            relative = (
                Path("campaigns")
                / request.campaign_id
                / "voice"
                / voice_id
                / "raw"
                / f"source.{audio_format}"
            )
            raw = _safe_path(self._work_root / relative, self._work_root)
            raw.parent.mkdir(parents=True, exist_ok=True)
            with raw.open("xb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            if _sha256(raw) != digest:
                raise VoiceImportError
            now = datetime.now(UTC)
            row = VoiceMasterRow(
                id=voice_id,
                campaign_id=request.campaign_id,
                copy_master_id=copy_id,
                copy_master_version=version,
                generation=repository.next_generation(copy_id),
                logical_key=key,
                status="pending",
                provider="imported",
                voice_preset=campaign.voice_preset,
                voice_id="imported",
                model_id="external-audio-v1",
                output_format="wav_48000_mono",
                settings_json={"processingVersion": 1},
                settings_fingerprint=VoiceMasterService._hash({"processingVersion": 1}),
                raw_audio_path=relative.as_posix(),
                raw_sha256=digest,
                raw_size_bytes=len(data),
                raw_format=audio_format,
                provider_state="not_dispatched",
                long_internal_pauses_json=[],
                qc_findings_json=[],
                job_id=job.id,
                created_at=now,
                updated_at=now,
            )
            repository.add(row)
            return VoiceMasterService._to_domain(row)

        def existing(job: Job) -> VoiceMaster:
            with self._sessions() as session:
                row = session.get(VoiceMasterRow, voice_id)
                if row is None or row.job_id != job.job_id:
                    raise VoiceImportError
                _verify_artifacts(row, self._work_root)
                return VoiceMasterService._to_domain(row)

        linked = self._jobs.submit_linked_job(
            JobSubmit(
                job_type="voice.import",
                campaign_id=request.campaign_id,
                idempotency_key=f"voice.import:{key}",
                input={"voiceMasterId": voice_id},
                retry_safety=RetrySafety.MANUAL_ONLY,
                max_attempts=1,
            ),
            create,
            existing,
        )
        return VoiceGenerationSubmission(linked.linked, linked.job)


class VoiceImportHandler:
    retry_safety = RetrySafety.MANUAL_ONLY

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        work_root: Path,
        transcriber: TranscriptProvider | None = None,
    ) -> None:
        self._sessions = session_factory
        self._work_root = _safe_path(work_root)
        self._transcriber = transcriber or FasterWhisperTranscriber()

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        voice_id = context.input.get("voiceMasterId")
        try:
            with self._sessions() as session:
                row = session.get(VoiceMasterRow, voice_id) if isinstance(voice_id, str) else None
                job = session.get(JobRow, context.job_id)
                if (
                    row is None
                    or row.provider != "imported"
                    or row.job_id != context.job_id
                    or row.campaign_id != context.campaign_id
                    or job is None
                    or job.job_type != "voice.import"
                    or job.input_json != {"voiceMasterId": row.id}
                    or job.idempotency_key != f"voice.import:{row.logical_key}"
                    or row.status != "pending"
                ):
                    raise VoiceImportError
                _verify_artifacts(row, self._work_root)
                copy = session.get(CopyMasterRow, row.copy_master_id)
                if copy is None or copy.approval_state != "approved":
                    raise VoiceImportError
                expected, headline = copy.spoken_text, copy.headline
                row.status = "processing"
                row.updated_at = datetime.now(UTC)
                session.commit()
                assert row.raw_audio_path is not None
                folder = _safe_path(
                    self._work_root / row.raw_audio_path, self._work_root
                ).parent.parent
                decoded = _safe_path(folder / "decoded.wav", self._work_root)
                processed = _safe_path(folder / "processed" / "voice-master.wav", self._work_root)
                decode_external_audio(self._work_root / row.raw_audio_path, decoded)
                report = process_voice_audio(decoded, processed)
                recognized = self._transcriber.transcribe(processed)
                comparison = transcript_comparison(
                    expected=expected, recognized=recognized, headline=headline
                )
                payloads = {
                    "transcript": {
                        "source": "faster_whisper",
                        "recognizedText": recognized,
                        "comparison": comparison.model_dump(mode="json", by_alias=True),
                    },
                    "manifest": {
                        "voiceMasterId": row.id,
                        "campaignId": row.campaign_id,
                        "copyMasterId": row.copy_master_id,
                        "copyMasterVersion": row.copy_master_version,
                        "provider": "imported",
                        "processingVersion": 1,
                        "rawSha256": row.raw_sha256,
                        "processing": report.model_dump(mode="json", by_alias=True),
                    },
                }
                for name, payload in payloads.items():
                    artifact = _safe_path(folder / name / f"{name}.json", self._work_root)
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    with artifact.open("x", encoding="utf-8") as target:
                        json.dump(payload, target, ensure_ascii=False, indent=2)
                    setattr(row, f"{name}_path", artifact.relative_to(self._work_root).as_posix())
                    setattr(row, f"{name}_sha256", _sha256(artifact))
                row.processed_audio_path = processed.relative_to(self._work_root).as_posix()
                row.processed_sha256 = report.processed_sha256
                for field in (
                    "duration_seconds",
                    "sample_rate",
                    "channels",
                    "loudness_lufs",
                    "true_peak_dbfs",
                    "leading_silence_seconds",
                    "trailing_silence_seconds",
                ):
                    setattr(row, field, getattr(report, field))
                row.long_internal_pauses_json = [
                    list(pause) for pause in report.long_internal_pauses
                ]
                row.word_count = len(comparison.normalized_expected.split())
                row.wpm = row.word_count / report.duration_seconds * 60
                row.transcript_source = "faster_whisper"
                row.transcript_match_status = comparison.status.value
                row.transcript_match_score = comparison.score
                row.headline_spoken = comparison.headline_spoken
                row.qc_findings_json = (
                    []
                    if comparison.status.value == "matched"
                    else ["The narration transcript requires human review."]
                )
                row.status = "review_required"
                row.provider_state = "local_ready"
                row.updated_at = datetime.now(UTC)
                _verify_artifacts(row, self._work_root)
                session.commit()
            return JobExecutionResult(
                outcome=JobExecutionOutcome.SUCCESS, result={"voiceMasterId": voice_id}
            )
        except Exception:
            with self._sessions() as session:
                row = session.get(VoiceMasterRow, voice_id) if isinstance(voice_id, str) else None
                if (
                    row is not None
                    and row.job_id == context.job_id
                    and row.status in {"pending", "processing"}
                ):
                    row.status = "failed"
                    row.failure_code = "voice_import_failed"
                    row.updated_at = datetime.now(UTC)
                    session.commit()
            return JobExecutionResult(
                outcome=JobExecutionOutcome.TERMINAL_FAILURE,
                error_code="voice_import_failed",
                error_message=VoiceImportError.public_message,
            )
