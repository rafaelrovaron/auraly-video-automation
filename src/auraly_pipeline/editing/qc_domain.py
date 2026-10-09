from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from auraly_pipeline.editing.domain import EditingModel, Identifier, Number, Sha, relative_path, safe_id
from auraly_pipeline.editing.render_domain import RenderRuntime
from auraly_pipeline.editing.resolver import content_hash
from auraly_pipeline.probe import MediaProbe

QC_VERSION = "1.0"


class QcTarget(EditingModel):
    output_variant_id: Identifier
    render_key: Sha
    _id = field_validator("output_variant_id")(safe_id)


class QcRequest(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    plan_hash: Sha
    outputs: list[QcTarget] = Field(min_length=1)
    _ids = field_validator("campaign_id", "video_id")(safe_id)

    @model_validator(mode="after")
    def unique(self) -> Self:
        ids = [o.output_variant_id for o in self.outputs]
        if len(ids) != len(set(ids)):
            raise ValueError("unique output variants required")
        return self


class QcPolicy(EditingModel):
    version: Literal["final-qc-v1"] = "final-qc-v1"
    min_loudness_lufs: Number = -30.0
    max_true_peak_dbtp: Number = 0.0

    @model_validator(mode="after")
    def fixed(self) -> Self:
        if (self.min_loudness_lufs, self.max_true_peak_dbtp) != (-30.0, 0.0):
            raise ValueError("fixed final-qc-v1 policy required")
        return self


class QcFinding(EditingModel):
    code: Identifier
    field: str
    message: str


class QcCheck(EditingModel):
    name: Literal["integrity", "audio", "text"]
    status: Literal["passed", "blocked", "not_applicable", "skipped"]
    findings: list[QcFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def reasons(self) -> Self:
        if bool(self.findings) != (self.status in {"blocked", "skipped"}):
            raise ValueError("findings required only for blocked/skipped checks")
        return self


class QcAudio(EditingModel):
    integrated_loudness_lufs: Number | None
    true_peak_dbtp: Number | None


def audio_findings(audio: QcAudio, policy: QcPolicy) -> list[QcFinding]:
    """One fixed decision rule for measurement and stored-report validation."""
    loudness, peak = audio.integrated_loudness_lufs, audio.true_peak_dbtp
    codes: list[tuple[str, str, str]] = []
    if loudness is None:
        codes.append(("audio_silent" if peak is None else "audio_loudness_unmeasurable",
                      "audio.integratedLoudnessLufs", "audio loudness is not measurable"))
    elif peak is None:
        raise ValueError("true peak required for finite loudness")
    elif loudness < policy.min_loudness_lufs:
        codes.append(("audio_low_loudness", "audio.integratedLoudnessLufs", "audio volume is too low"))
    if peak is not None and peak >= policy.max_true_peak_dbtp:
        codes.append(("audio_peak_risk", "audio.truePeakDbtp", "audio true peak has clipping risk"))
    return [QcFinding(code=code, field=field, message=message) for code, field, message in codes]


def qc_key(*, plan_hash: str, output_variant_id: str, render_key: str, master_sha256: str,
           receipt_sha256: str, qc_version: str, policy_hash: str, runtime_fingerprint: str) -> str:
    return content_hash({"planHash": plan_hash, "outputVariantId": output_variant_id,
                         "renderKey": render_key, "masterSha256": master_sha256,
                         "receiptSha256": receipt_sha256, "qcVersion": qc_version,
                         "policyHash": policy_hash, "runtimeFingerprint": runtime_fingerprint})


class QcReport(EditingModel):
    schema_version: Literal["1.0"] = "1.0"
    qc_version: Literal["1.0"] = "1.0"
    campaign_id: Identifier
    video_id: Identifier
    output_variant_id: Identifier
    plan_hash: Sha
    manifest_hash: Sha
    output_hash: Sha
    render_key: Sha
    master_path: str
    master_sha256: Sha
    size_bytes: int = Field(gt=0, strict=True)
    receipt_sha256: Sha
    policy: QcPolicy
    policy_hash: Sha
    runtime: RenderRuntime
    qc_key: Sha
    probe: MediaProbe | None
    checks: list[QcCheck]
    audio: QcAudio | None
    status: Literal["passed", "blocked"]
    human_review_required: Literal[True] = True
    text_measurement_scope: Literal["manifest_layout_not_burned_frames"] = "manifest_layout_not_burned_frames"
    _ids = field_validator("campaign_id", "video_id", "output_variant_id")(safe_id)
    _path = field_validator("master_path")(relative_path)

    @model_validator(mode="after")
    def valid(self) -> Self:
        validate_qc_report(self)
        return self


def validate_qc_report(report: QcReport) -> None:
    if report.policy_hash != content_hash(report.policy.model_dump(mode="json", by_alias=True)):
        raise ValueError("QC policy hash mismatch")
    expected = qc_key(plan_hash=report.plan_hash, output_variant_id=report.output_variant_id,
                      render_key=report.render_key, master_sha256=report.master_sha256,
                      receipt_sha256=report.receipt_sha256, qc_version=report.qc_version,
                      policy_hash=report.policy_hash, runtime_fingerprint=report.runtime.fingerprint)
    if expected != report.qc_key or [c.name for c in report.checks] != ["integrity", "audio", "text"]:
        raise ValueError("QC identity or check set mismatch")
    integrity, audio, text = report.checks
    if integrity.status == "blocked":
        if (audio.status, text.status, report.audio, report.status) != ("skipped", "skipped", None, "blocked"):
            raise ValueError("blocked integrity requires skipped dependent checks")
        return
    if (integrity.status != "passed" or report.probe is None or report.audio is None
            or text.status not in {"passed", "blocked", "not_applicable"}):
        raise ValueError("complete QC measurements required")
    expected_findings = audio_findings(report.audio, report.policy)
    if (audio.status != ("blocked" if expected_findings else "passed")
            or [f.code for f in audio.findings] != [f.code for f in expected_findings]):
        raise ValueError("audio decision does not match measurements")
    status = "blocked" if expected_findings or text.status == "blocked" else "passed"
    if report.status != status:
        raise ValueError("QC aggregate mismatch")


class QcOutputResult(QcTarget):
    status: Literal["passed", "blocked", "error"]
    reused: bool = False
    qc_key: Sha | None = None
    path: str | None = None
    error: QcFinding | None = None

    @field_validator("path")
    @classmethod
    def local_path(cls, value: str | None) -> str | None:
        return relative_path(value) if value is not None else None

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.status == "error":
            if self.error is None or self.qc_key is not None or self.path is not None or self.reused:
                raise ValueError("error cannot have a stored report")
        elif self.error is not None or self.qc_key is None or self.path is None:
            raise ValueError("complete result requires stored report")
        return self


class QcBatchResult(QcRequest):
    outputs: list[QcOutputResult] = Field(min_length=1)  # type: ignore[assignment]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_failures(self) -> bool:
        return any(o.status != "passed" for o in self.outputs)
