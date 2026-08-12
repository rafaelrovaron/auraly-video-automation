from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from faster_whisper import WhisperModel  # type: ignore[import-untyped]
from sqlalchemy.orm import Session, sessionmaker

from auraly_pipeline.campaigns.db_models import CampaignRow, CopyMasterRow
from auraly_pipeline.jobs.db_models import JobEventRow, JobRow
from auraly_pipeline.jobs.domain import (
    JobExecutionOutcome,
    JobExecutionResult,
    RetrySafety,
)
from auraly_pipeline.jobs.handlers import JobExecutionContext
from auraly_pipeline.voices.audio import AudioProcessingError, process_voice_audio
from auraly_pipeline.voices.db_models import VoiceMasterRow
from auraly_pipeline.voices.domain import TranscriptSource, transcript_comparison
from auraly_pipeline.voices.provider import (
    ElevenLabsAdapter,
    ProviderFailure,
    ProviderFailureKind,
    SpeechGeneration,
)


class SpeechProvider(Protocol):
    def generate_speech(
        self,
        *,
        text: str,
        voice_id: str,
        model_id: str,
        output_format: str,
        voice_settings: dict[str, float | bool] | None = None,
    ) -> SpeechGeneration: ...


class TranscriptProvider(Protocol):
    def transcribe(self, audio_path: Path) -> str: ...


class FasterWhisperTranscriber:
    def __init__(self) -> None:
        self._model: WhisperModel | None = None

    def transcribe(self, audio_path: Path) -> str:
        if self._model is None:
            self._model = WhisperModel("small.en", device="cpu", compute_type="int8")
        segments, _ = self._model.transcribe(str(audio_path), language="en", beam_size=5)
        return " ".join(segment.text.strip() for segment in segments).strip()


class VoiceGenerateHandler:
    retry_safety = RetrySafety.RECONCILE_BEFORE_RETRY

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        work_root: Path,
        provider: SpeechProvider | None = None,
        transcriber: TranscriptProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._work_root = work_root.resolve()
        self._provider = provider
        self._transcriber = transcriber or FasterWhisperTranscriber()
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        temporary = path.with_name(f".{path.name}.partial")
        try:
            with temporary.open("xb") as target:
                target.write(encoded)
                target.flush()
                os.fsync(target.fileno())
            with path.open("xb") as target, temporary.open("rb") as source:
                target.write(source.read())
                target.flush()
                os.fsync(target.fileno())
        finally:
            temporary.unlink(missing_ok=True)

    def execute(self, context: JobExecutionContext) -> JobExecutionResult:
        voice_master_id = context.input.get("voiceMasterId")
        if not isinstance(voice_master_id, str):
            return self._terminal("voice_request_invalid", "The Voice Master job input is invalid.")
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            if row is None:
                return self._terminal("voice_master_not_found", "Voice Master not found.")
            if row.campaign_id != context.campaign_id:
                return self._terminal(
                    "voice_job_integrity_failed",
                    "The Voice Master job relationship is invalid.",
                )
            if row.job_id is None:
                job_row = session.get(JobRow, context.job_id)
                if (
                    job_row is None
                    or job_row.job_type != "voice.generate"
                    or job_row.campaign_id != row.campaign_id
                    or job_row.idempotency_key != f"voice.generate:{row.logical_key}"
                    or job_row.input_json != {"voiceMasterId": row.id}
                ):
                    return self._terminal(
                        "voice_job_integrity_failed",
                        "The Voice Master job relationship is invalid.",
                    )
                row.job_id = context.job_id
                row.updated_at = self._clock()
                session.commit()
            elif row.job_id != context.job_id:
                return self._terminal(
                    "voice_job_integrity_failed",
                    "The Voice Master job relationship is invalid.",
                )
            job_row = session.get(JobRow, context.job_id)
            campaign = session.get(CampaignRow, row.campaign_id)
            paid_event = (
                session.query(JobEventRow)
                .filter_by(job_id=context.job_id, event_type="job.paid_authorized")
                .one_or_none()
            )
            budget_limit = None if campaign is None else campaign.budget_json.get("limitCents")
            budget_currency = None if campaign is None else campaign.budget_json.get("currency")
            approved_budget = row.settings_json.get("approved_budget_cents")
            approved_by = row.settings_json.get("paid_request_approved_by")
            approved_at = row.settings_json.get("paid_request_approved_at")
            expected_paid_metadata = {
                "approvedBy": approved_by,
                "approvedBudgetCents": approved_budget,
                "approvedAt": approved_at,
                "campaignBudgetLimitCents": budget_limit,
                "currency": budget_currency,
            }
            if (
                job_row is None
                or job_row.input_json != {"voiceMasterId": row.id}
                or job_row.idempotency_key != f"voice.generate:{row.logical_key}"
                or row.settings_json.get("paid_request_approved") is not True
                or not isinstance(budget_limit, int)
                or isinstance(budget_limit, bool)
                or budget_limit <= 0
                or not isinstance(budget_currency, str)
                or len(budget_currency) != 3
                or not budget_currency.isascii()
                or not budget_currency.isalpha()
                or budget_currency != budget_currency.upper()
                or not isinstance(approved_budget, int)
                or isinstance(approved_budget, bool)
                or approved_budget <= 0
                or approved_budget > budget_limit
                or paid_event is None
                or paid_event.metadata_json != expected_paid_metadata
            ):
                return self._terminal(
                    "voice_paid_authorization_missing",
                    "Durable paid request authorization is unavailable.",
                )
            copy = session.get(CopyMasterRow, row.copy_master_id)
            if copy is None or copy.approval_state != "approved":
                return self._terminal(
                    "approved_copy_not_found", "The approved CopyMaster is unavailable."
                )
            if row.status == "review_required":
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.SUCCESS,
                    result={"voiceMasterId": row.id, "reconciled": True},
                )
            voice_root = (
                self._work_root / "campaigns" / row.campaign_id / "voice" / row.id
            ).resolve()
            recovery_raw_path = (voice_root / "raw" / "provider.mp3").resolve()
            recovery_processed_path = (voice_root / "processed" / "voice-master.wav").resolve()
            recovery_transcript_path = (voice_root / "inspection" / "transcript.json").resolve()
            recovery_manifest_path = (voice_root / "manifest" / "voice-master.json").resolve()
            if (
                row.status in {"generating", "processing"}
                and row.provider_state == "response_received"
                and voice_root.is_relative_to(self._work_root)
                and recovery_raw_path.is_relative_to(self._work_root)
                and recovery_processed_path.is_relative_to(self._work_root)
                and recovery_transcript_path.is_relative_to(self._work_root)
                and recovery_manifest_path.is_relative_to(self._work_root)
                and recovery_raw_path.is_file()
                and recovery_processed_path.is_file()
                and recovery_transcript_path.is_file()
                and recovery_manifest_path.is_file()
            ):
                try:
                    transcript_bytes = recovery_transcript_path.read_bytes()
                    manifest_bytes = recovery_manifest_path.read_bytes()
                    manifest = json.loads(manifest_bytes.decode("utf-8"))
                    json.loads(transcript_bytes.decode("utf-8"))
                    transcript_sha256 = hashlib.sha256(transcript_bytes).hexdigest()
                    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
                    processed_sha256 = hashlib.sha256(
                        recovery_processed_path.read_bytes()
                    ).hexdigest()
                    raw_sha256 = hashlib.sha256(recovery_raw_path.read_bytes()).hexdigest()
                except (OSError, ValueError, KeyError, TypeError):
                    pass
                else:
                    if (
                        row.transcript_sha256 is not None
                        and row.manifest_sha256 is not None
                        and manifest.get("voiceMasterId") == row.id
                        and manifest.get("rawSha256") == raw_sha256
                        and manifest.get("processedSha256") == processed_sha256
                        and manifest.get("transcriptSha256") == transcript_sha256
                        and row.transcript_sha256 == transcript_sha256
                        and row.manifest_sha256 == manifest_sha256
                    ):
                        row.raw_audio_path = (
                            f"campaigns/{row.campaign_id}/voice/{row.id}/raw/provider.mp3"
                        )
                        row.processed_audio_path = (
                            f"campaigns/{row.campaign_id}/voice/{row.id}/processed/voice-master.wav"
                        )
                        row.transcript_path = (
                            f"campaigns/{row.campaign_id}/voice/{row.id}/inspection/transcript.json"
                        )
                        row.manifest_path = (
                            f"campaigns/{row.campaign_id}/voice/{row.id}/manifest/voice-master.json"
                        )
                        row.raw_sha256 = raw_sha256
                        row.raw_size_bytes = recovery_raw_path.stat().st_size
                        row.raw_format = "mp3"
                        row.processed_sha256 = processed_sha256
                        row.transcript_sha256 = transcript_sha256
                        row.manifest_sha256 = manifest_sha256
                        row.duration_seconds = manifest["durationSeconds"]
                        row.word_count = manifest["wordCount"]
                        row.wpm = manifest["wpm"]
                        row.sample_rate = manifest["sampleRate"]
                        row.channels = manifest["channels"]
                        row.loudness_lufs = manifest["loudnessLufs"]
                        row.true_peak_dbfs = manifest["truePeakDbfs"]
                        row.leading_silence_seconds = manifest["leadingSilenceSeconds"]
                        row.trailing_silence_seconds = manifest["trailingSilenceSeconds"]
                        row.long_internal_pauses_json = manifest["longInternalPauses"]
                        row.transcript_source = manifest["transcriptSource"]
                        row.transcript_match_status = manifest["transcriptMatchStatus"]
                        row.transcript_match_score = manifest["transcriptMatchScore"]
                        row.headline_spoken = manifest["headlineSpoken"]
                        row.provider_request_id = manifest.get("requestId")
                        row.qc_findings_json = []
                        if row.headline_spoken:
                            row.qc_findings_json.append(
                                "The visual-only headline was detected in the narration."
                            )
                        if row.transcript_match_status != "matched":
                            row.qc_findings_json.append(
                                "The narration transcript requires human review."
                            )
                        row.status = "review_required"
                        row.updated_at = self._clock()
                        session.commit()
                        return JobExecutionResult(
                            outcome=JobExecutionOutcome.SUCCESS,
                            result={"voiceMasterId": row.id, "reconciled": True},
                        )
            recover_raw = False
            recovery_raw_path = (
                self._work_root
                / "campaigns"
                / row.campaign_id
                / "voice"
                / row.id
                / "raw"
                / "provider.mp3"
            ).resolve()
            if (
                row.status in {"generating", "processing"}
                and row.provider_state == "response_received"
                and recovery_raw_path.is_relative_to(self._work_root)
                and recovery_raw_path.is_file()
                and row.raw_sha256 is not None
                and hashlib.sha256(recovery_raw_path.read_bytes()).hexdigest() == row.raw_sha256
            ):
                recover_raw = True
                for incomplete in (
                    recovery_processed_path,
                    recovery_processed_path.with_name(f".{recovery_processed_path.name}.partial"),
                    recovery_transcript_path,
                    recovery_transcript_path.with_name(f".{recovery_transcript_path.name}.partial"),
                    recovery_manifest_path,
                    recovery_manifest_path.with_name(f".{recovery_manifest_path.name}.partial"),
                ):
                    incomplete.unlink(missing_ok=True)
            if row.status == "generating" and row.provider_state == "not_dispatched":
                row.status = "pending"
                session.commit()
            if row.status != "pending" and not recover_raw:
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.BLOCKED,
                    error_code="voice_reconciliation_required",
                    error_message="Voice Master provider reconciliation is required.",
                )
            row.status = "generating"
            row.provider_state = "not_dispatched"
            row.updated_at = self._clock()
            session.commit()
            text = copy.spoken_text
            headline = copy.headline
            copy_master_id = row.copy_master_id
            copy_master_version = row.copy_master_version
            generation_number = row.generation
            provider_name = row.provider
            voice_preset = row.voice_preset
            settings_fingerprint = row.settings_fingerprint
            settings_payload = dict(row.settings_json)
            threshold_value = settings_payload.pop("transcript_match_threshold", 0.97)
            approved_budget_value = settings_payload.pop("approved_budget_cents", None)
            paid_approved_value = settings_payload.pop("paid_request_approved", None)
            paid_approved_by = settings_payload.pop("paid_request_approved_by", None)
            paid_approved_at = settings_payload.pop("paid_request_approved_at", None)
            if (
                paid_approved_value is not True
                or not isinstance(paid_approved_by, str)
                or not paid_approved_by
                or not isinstance(paid_approved_at, str)
                or not paid_approved_at
            ):
                return self._terminal(
                    "voice_paid_authorization_invalid",
                    "The paid Voice Master authorization is invalid.",
                )
            if (
                not isinstance(approved_budget_value, int)
                or isinstance(approved_budget_value, bool)
                or approved_budget_value <= 0
            ):
                return self._terminal(
                    "voice_budget_invalid",
                    "The Voice Master approved budget is invalid.",
                )
            if not isinstance(threshold_value, (int, float)) or isinstance(threshold_value, bool):
                return self._terminal(
                    "voice_request_invalid",
                    "The Voice Master transcript threshold is invalid.",
                )
            match_threshold = float(threshold_value)
            settings = cast("dict[str, float | bool]", settings_payload)
            voice_id = row.voice_id
            model_id = row.model_id
            output_format = row.output_format

        if recover_raw:
            raw_audio = recovery_raw_path.read_bytes()
            if not raw_audio:
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.BLOCKED,
                    error_code="voice_reconciliation_required",
                    error_message="Voice Master provider reconciliation is required.",
                )
            generation = SpeechGeneration(
                audio=raw_audio,
                aligned_text=None,
                alignment=None,
                request_id=None,
                output_format=output_format,
            )
            return self._persist_and_process(
                voice_master_id,
                generation,
                expected_text=text,
                headline=headline,
                match_threshold=match_threshold,
                raw_already_exists=True,
                manifest_provenance={
                    "copyMasterId": copy_master_id,
                    "copyMasterVersion": copy_master_version,
                    "generation": generation_number,
                    "provider": provider_name,
                    "voicePreset": voice_preset,
                    "settingsFingerprint": settings_fingerprint,
                },
            )

        with self._sessions() as session:
            dispatch_row = session.get(VoiceMasterRow, voice_master_id)
            assert dispatch_row is not None
            if dispatch_row.provider_state != "not_dispatched":
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.BLOCKED,
                    error_code="voice_reconciliation_required",
                    error_message="Voice Master provider reconciliation is required.",
                )
            dispatch_row.provider_state = "dispatching"
            dispatch_row.updated_at = self._clock()
            session.commit()

        provider: SpeechProvider | None = self._provider
        owned_provider = False
        try:
            if provider is None:
                provider = ElevenLabsAdapter.from_environment()
                owned_provider = True
            generation = provider.generate_speech(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                output_format=output_format,
                voice_settings=settings,
            )
        except ProviderFailure as exc:
            self._mark_provider_failure(voice_master_id, exc)
            if exc.kind is ProviderFailureKind.AMBIGUOUS:
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.BLOCKED,
                    error_code="provider_outcome_ambiguous",
                    error_message="The paid provider outcome requires reconciliation.",
                )
            if exc.kind is ProviderFailureKind.RETRYABLE:
                return JobExecutionResult(
                    outcome=JobExecutionOutcome.RETRYABLE_FAILURE,
                    error_code="provider_temporarily_unavailable",
                    error_message="The provider temporarily rejected the speech request.",
                )
            return self._terminal("provider_request_failed", exc.public_message)
        finally:
            if owned_provider and isinstance(provider, ElevenLabsAdapter):
                provider.close()

        try:
            return self._persist_and_process(
                voice_master_id,
                generation,
                expected_text=text,
                headline=headline,
                match_threshold=match_threshold,
                raw_already_exists=False,
                manifest_provenance={
                    "copyMasterId": copy_master_id,
                    "copyMasterVersion": copy_master_version,
                    "generation": generation_number,
                    "provider": provider_name,
                    "voicePreset": voice_preset,
                    "settingsFingerprint": settings_fingerprint,
                },
            )
        except AudioProcessingError:
            self._mark_failed(voice_master_id, "audio_processing_failed")
            return self._terminal(
                "audio_processing_failed", "The Voice Master audio could not be processed safely."
            )
        except Exception:
            self._mark_failed(voice_master_id, "voice_processing_failed")
            return self._terminal(
                "voice_processing_failed", "The Voice Master processing operation failed safely."
            )

    def _persist_and_process(
        self,
        voice_master_id: str,
        generation: SpeechGeneration,
        *,
        expected_text: str,
        headline: str,
        match_threshold: float,
        raw_already_exists: bool,
        manifest_provenance: Mapping[str, object],
    ) -> JobExecutionResult:
        relative_root = (
            Path("campaigns") / self._campaign_id(voice_master_id) / "voice" / voice_master_id
        )
        absolute_root = (self._work_root / relative_root).resolve()
        if not absolute_root.is_relative_to(self._work_root):
            raise AudioProcessingError(AudioProcessingError.public_message)
        raw_relative = relative_root / "raw" / "provider.mp3"
        processed_relative = relative_root / "processed" / "voice-master.wav"
        transcript_relative = relative_root / "inspection" / "transcript.json"
        manifest_relative = relative_root / "manifest" / "voice-master.json"
        raw = self._work_root / raw_relative
        processed = self._work_root / processed_relative
        transcript_path = self._work_root / transcript_relative
        manifest_path = self._work_root / manifest_relative
        raw.parent.mkdir(parents=True, exist_ok=True)
        if raw_already_exists:
            if not raw.is_file() or raw.read_bytes() != generation.audio:
                raise AudioProcessingError(AudioProcessingError.public_message)
        else:
            with raw.open("xb") as target:
                target.write(generation.audio)
                target.flush()
                os.fsync(target.fileno())
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            assert row is not None
            row.raw_audio_path = raw_relative.as_posix()
            row.raw_sha256 = hashlib.sha256(generation.audio).hexdigest()
            row.raw_size_bytes = len(generation.audio)
            row.raw_format = "mp3"
            row.provider_request_id = generation.request_id
            row.provider_state = "response_received"
            row.status = "processing"
            row.updated_at = self._clock()
            session.commit()
        report = process_voice_audio(raw, processed)
        transcript_source = TranscriptSource.ELEVENLABS_ALIGNMENT
        recognized = generation.aligned_text
        alignment = generation.alignment
        if alignment is not None:
            ends = alignment.get("character_end_times_seconds")
            if (
                not isinstance(ends, list)
                or not ends
                or float(ends[-1]) > report.duration_seconds + 0.001
            ):
                recognized = None
                alignment = None
        if not recognized:
            recognized = self._transcriber.transcribe(processed)
            transcript_source = TranscriptSource.FASTER_WHISPER
        comparison = transcript_comparison(
            expected=expected_text,
            recognized=recognized,
            headline=headline,
            match_threshold=match_threshold,
        )
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_payload = {
            "source": transcript_source.value,
            "recognizedText": recognized,
            "comparison": comparison.model_dump(by_alias=True, mode="json"),
            "alignment": alignment,
        }
        self._write_json_exclusive(transcript_path, transcript_payload)
        transcript_sha256 = hashlib.sha256(transcript_path.read_bytes()).hexdigest()
        findings: list[str] = []
        if comparison.headline_spoken:
            findings.append("The visual-only headline was detected in the narration.")
        if comparison.status.value != "matched":
            findings.append("The narration transcript requires human review.")
        word_count = len(
            transcript_comparison(
                expected=expected_text,
                recognized=expected_text,
                headline="",
            ).normalized_expected.split()
        )
        wpm = word_count / report.duration_seconds * 60
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "voiceMasterId": voice_master_id,
            "campaignId": self._campaign_id(voice_master_id),
            "copyMasterId": manifest_provenance["copyMasterId"],
            "copyMasterVersion": manifest_provenance["copyMasterVersion"],
            "generation": manifest_provenance["generation"],
            "provider": manifest_provenance["provider"],
            "voicePreset": manifest_provenance["voicePreset"],
            "voiceId": row.voice_id,
            "modelId": row.model_id,
            "outputFormat": row.output_format,
            "settingsFingerprint": manifest_provenance["settingsFingerprint"],
            "rawAudioPath": raw_relative.as_posix(),
            "processedAudioPath": processed_relative.as_posix(),
            "transcriptPath": transcript_relative.as_posix(),
            "transcriptSha256": transcript_sha256,
            "rawSha256": report.raw_sha256,
            "processedSha256": report.processed_sha256,
            "durationSeconds": report.duration_seconds,
            "sampleRate": report.sample_rate,
            "channels": report.channels,
            "loudnessLufs": report.loudness_lufs,
            "truePeakDbfs": report.true_peak_dbfs,
            "leadingSilenceSeconds": report.leading_silence_seconds,
            "trailingSilenceSeconds": report.trailing_silence_seconds,
            "longInternalPauses": report.long_internal_pauses,
            "wordCount": word_count,
            "wpm": wpm,
            "transcriptSource": transcript_source.value,
            "transcriptMatchStatus": comparison.status.value,
            "transcriptMatchScore": comparison.score,
            "headlineSpoken": comparison.headline_spoken,
            "requestId": generation.request_id,
            "ffmpegFilter": report.ffmpeg_filter,
            "createdAt": self._clock().isoformat(),
        }
        self._write_json_exclusive(manifest_path, manifest)
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            assert row is not None
            row.processed_audio_path = processed_relative.as_posix()
            row.transcript_path = transcript_relative.as_posix()
            row.manifest_path = manifest_relative.as_posix()
            row.processed_sha256 = report.processed_sha256
            row.transcript_sha256 = transcript_sha256
            row.manifest_sha256 = manifest_sha256
            row.duration_seconds = report.duration_seconds
            row.word_count = word_count
            row.wpm = wpm
            row.sample_rate = report.sample_rate
            row.channels = report.channels
            row.loudness_lufs = report.loudness_lufs
            row.true_peak_dbfs = report.true_peak_dbfs
            row.leading_silence_seconds = report.leading_silence_seconds
            row.trailing_silence_seconds = report.trailing_silence_seconds
            row.long_internal_pauses_json = [list(item) for item in report.long_internal_pauses]
            row.transcript_source = transcript_source.value
            row.transcript_match_status = comparison.status.value
            row.transcript_match_score = comparison.score
            row.headline_spoken = comparison.headline_spoken
            row.qc_findings_json = findings
            row.status = "review_required"
            row.updated_at = self._clock()
            session.commit()
        return JobExecutionResult(
            outcome=JobExecutionOutcome.SUCCESS,
            result={
                "voiceMasterId": voice_master_id,
                "processedSha256": report.processed_sha256,
                "transcriptMatchStatus": comparison.status.value,
                "headlineSpoken": comparison.headline_spoken,
            },
        )

    def _campaign_id(self, voice_master_id: str) -> str:
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            if row is None:
                raise AudioProcessingError(AudioProcessingError.public_message)
            return row.campaign_id

    def _mark_provider_failure(self, voice_master_id: str, failure: ProviderFailure) -> None:
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            if row is None:
                return
            row.status = "generating" if failure.request_dispatched else "failed"
            row.provider_state = "ambiguous" if failure.request_dispatched else "not_dispatched"
            row.failure_code = (
                "provider_outcome_ambiguous"
                if failure.request_dispatched
                else "provider_request_failed"
            )
            row.updated_at = self._clock()
            session.commit()

    def _mark_failed(self, voice_master_id: str, code: str) -> None:
        with self._sessions() as session:
            row = session.get(VoiceMasterRow, voice_master_id)
            if row is not None:
                row.status = "failed"
                row.failure_code = code
                row.updated_at = self._clock()
                session.commit()

    @staticmethod
    def _terminal(code: str, message: str) -> JobExecutionResult:
        return JobExecutionResult(
            outcome=JobExecutionOutcome.TERMINAL_FAILURE,
            error_code=code,
            error_message=message,
        )
