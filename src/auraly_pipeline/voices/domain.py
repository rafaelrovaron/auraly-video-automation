from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from auraly_pipeline.metadata_security import validate_safe_error_message, validate_safe_identifier
from auraly_pipeline.models import ContractModel


_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


class VoiceMasterStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    PROCESSING = "processing"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class TranscriptMatchStatus(StrEnum):
    MATCHED = "matched"
    REVIEW_REQUIRED = "review_required"
    MISMATCHED = "mismatched"


class TranscriptSource(StrEnum):
    ELEVENLABS_ALIGNMENT = "elevenlabs_alignment"
    FASTER_WHISPER = "faster_whisper"


class TranscriptComparison(ContractModel):
    status: TranscriptMatchStatus
    score: float = Field(ge=0, le=1)
    normalized_expected: str
    normalized_recognized: str
    missing_tokens: list[str]
    unexpected_tokens: list[str]
    headline_spoken: bool


def normalize_transcript(value: str) -> str:
    return " ".join(_TOKEN.findall(value.casefold()))


def transcript_comparison(
    *,
    expected: str,
    recognized: str,
    headline: str,
    match_threshold: float = 0.97,
) -> TranscriptComparison:
    expected_normalized = normalize_transcript(expected)
    recognized_normalized = normalize_transcript(recognized)
    expected_tokens = expected_normalized.split()
    recognized_tokens = recognized_normalized.split()
    matcher = SequenceMatcher(a=expected_tokens, b=recognized_tokens, autojunk=False)
    missing: list[str] = []
    unexpected: list[str] = []
    for tag, expected_start, expected_end, actual_start, actual_end in matcher.get_opcodes():
        if tag in {"delete", "replace"}:
            missing.extend(expected_tokens[expected_start:expected_end])
        if tag in {"insert", "replace"}:
            unexpected.extend(recognized_tokens[actual_start:actual_end])
    score = round(matcher.ratio(), 6)
    headline_normalized = normalize_transcript(headline)
    headline_spoken = bool(
        headline_normalized
        and re.search(rf"(?:^| ){re.escape(headline_normalized)}(?: |$)", recognized_normalized)
    )
    if headline_spoken or score < match_threshold:
        status = TranscriptMatchStatus.MISMATCHED
    elif missing or unexpected:
        status = TranscriptMatchStatus.REVIEW_REQUIRED
    else:
        status = TranscriptMatchStatus.MATCHED
    return TranscriptComparison(
        status=status,
        score=score,
        normalized_expected=expected_normalized,
        normalized_recognized=recognized_normalized,
        missing_tokens=missing,
        unexpected_tokens=unexpected,
        headline_spoken=headline_spoken,
    )


def validate_workspace_path(value: str) -> str:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or "\x00" in value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or ".." in posix.parts
        or ".." in windows.parts
        or _SAFE_PATH.fullmatch(value) is None
    ):
        raise ValueError("artifact paths must be safe workspace-relative paths")
    return value


class VoiceGenerateRequest(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    copy_master_version: int | None = Field(default=None, ge=1)
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    model_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    output_format: Literal["mp3_44100_128"] = "mp3_44100_128"
    voice_settings: dict[str, float | bool] = Field(default_factory=dict)
    paid_request_approved: bool = False
    paid_request_approved_by: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$", max_length=120
    )
    transcript_match_threshold: float = Field(default=0.97, ge=0.9, le=1.0)
    approved_budget_cents: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        allowed = {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
        if set(self.voice_settings) - allowed:
            raise ValueError("voice_settings contains unsupported options")
        for key, value in self.voice_settings.items():
            if key == "use_speaker_boost":
                if not isinstance(value, bool):
                    raise ValueError("use_speaker_boost must be boolean")
            elif isinstance(value, bool) or not 0 <= value <= (1.2 if key == "speed" else 1):
                raise ValueError("voice setting is outside the supported range")
        return self


class VoiceMaster(ContractModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    voice_master_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    campaign_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    copy_master_id: str = Field(pattern=_UUID_PATTERN, max_length=36)
    copy_master_version: int = Field(ge=1)
    generation: int = Field(ge=1)
    status: VoiceMasterStatus
    provider: Literal["elevenlabs"]
    voice_preset: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", max_length=120)
    voice_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    model_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=120)
    settings_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    raw_audio_path: str | None = None
    processed_audio_path: str | None = None
    transcript_path: str | None = None
    manifest_path: str | None = None
    raw_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    processed_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    transcript_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    manifest_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    raw_size_bytes: int | None = Field(default=None, gt=0)
    raw_format: str | None = Field(default=None, pattern=r"^[a-z0-9]+$", max_length=20)
    duration_seconds: float | None = Field(default=None, gt=0)
    word_count: int | None = Field(default=None, ge=1)
    wpm: float | None = Field(default=None, gt=0)
    sample_rate: int | None = Field(default=None, gt=0)
    channels: int | None = Field(default=None, ge=1, le=8)
    loudness_lufs: float | None = None
    true_peak_dbfs: float | None = None
    leading_silence_seconds: float | None = Field(default=None, ge=0)
    trailing_silence_seconds: float | None = Field(default=None, ge=0)
    long_internal_pauses: list[tuple[float, float]] = Field(default_factory=list)
    transcript_source: TranscriptSource | None = None
    transcript_match_status: TranscriptMatchStatus | None = None
    transcript_match_score: float | None = Field(default=None, ge=0, le=1)
    headline_spoken: bool | None = None
    qc_findings: list[str] = Field(default_factory=list)
    provider_request_id: str | None = Field(default=None, max_length=200)
    approved_at: datetime | None = None
    approved_by: str | None = Field(default=None, max_length=120)
    rejected_at: datetime | None = None
    rejected_by: str | None = Field(default=None, max_length=120)
    rejection_reason: str | None = Field(default=None, max_length=512)
    created_at: datetime
    updated_at: datetime

    _paths = field_validator(
        "raw_audio_path", "processed_audio_path", "transcript_path", "manifest_path"
    )(lambda value: None if value is None else validate_workspace_path(value))

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        for field_name in ("voice_master_id", "copy_master_id", "campaign_id", "voice_preset"):
            validate_safe_identifier(getattr(self, field_name), field_name, max_length=120)
        if self.provider_request_id is not None:
            validate_safe_identifier(
                self.provider_request_id, "provider_request_id", max_length=200
            )
        for finding in self.qc_findings:
            validate_safe_error_message(finding, "qc_finding")
        if self.rejection_reason is not None:
            validate_safe_error_message(self.rejection_reason, "rejection_reason")
        if self.status is VoiceMasterStatus.APPROVED:
            if not self.approved_at or not self.approved_by:
                raise ValueError("approved VoiceMaster requires approval metadata")
            if (
                self.headline_spoken
                or self.qc_findings
                or self.transcript_match_status is not TranscriptMatchStatus.MATCHED
            ):
                raise ValueError("VoiceMaster with failed transcript QC cannot be approved")
        if self.status is VoiceMasterStatus.REJECTED and not (
            self.rejected_at and self.rejected_by and self.rejection_reason
        ):
            raise ValueError("rejected VoiceMaster requires rejection metadata")
        return self
