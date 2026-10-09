from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Literal

from pydantic import TypeAdapter

from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingError, Sha
from auraly_pipeline.editing.qc_checks import (
    check_qc_integrity, check_qc_text, measure_qc_audio, validate_qc_text_assets,
)
from auraly_pipeline.editing.qc_domain import (
    QC_VERSION, QcBatchResult, QcCheck, QcFinding, QcOutputResult, QcPolicy,
    QcReport, QcRequest, QcTarget, qc_key,
)
from auraly_pipeline.editing.render_artifacts import VerifiedRender, open_verified_render, render_work_root
from auraly_pipeline.editing.render_domain import RenderRuntime
from auraly_pipeline.editing.render_runtime import detect_runtime
from auraly_pipeline.editing.resolver import content_hash
from auraly_pipeline.editing.service import publish_editing_json, validate_editing_path


def _error(target: QcTarget, exc: Exception) -> QcOutputResult:
    field = exc.field if isinstance(exc, EditingError) else "artifact"
    code = {"runtime": "runtime_unavailable", "audio": "audio_measurement_failed"}.get(field, "artifact_invalid")
    return QcOutputResult(**target.model_dump(), status="error", error=QcFinding(
        code=code, field=field, message="local QC could not complete; repair input or runtime and retry"))


class QcService:
    def __init__(self, *, project_root: Path, work_root: Path) -> None:
        self.batch = EditBatchService(project_root=project_root, work_root=render_work_root(work_root))
        self.project_root, self.work_root = self.batch.project_root, self.batch.work_root
        self.editing = self.batch.editing

    def _path(self, plan: EditBatchPlan, target: QcTarget, key: str) -> Path:
        TypeAdapter(Sha).validate_python(key)
        return validate_editing_path(self.work_root, self.work_root / "campaigns" / plan.campaign_id
            / "editing" / "qc" / target.output_variant_id / target.render_key / key / "report.json")

    def _read_report(self, plan: EditBatchPlan, target: QcTarget, key: str,
                     artifact: VerifiedRender) -> QcReport:
        path = self._path(plan, target, key)
        report = QcReport.model_validate_json(path.read_bytes())
        output = next(o for o in plan.outputs if o.output_variant_id == target.output_variant_id)
        if ((report.campaign_id, report.video_id, report.plan_hash, report.output_variant_id,
             report.render_key, report.qc_key, report.manifest_hash, report.output_hash,
             report.master_path, report.master_sha256, report.size_bytes, report.receipt_sha256) !=
            (plan.campaign_id, plan.video_id, plan.plan_hash, target.output_variant_id,
             target.render_key, key, output.manifest_hash, output.output_hash,
             artifact.receipt.path, artifact.receipt.sha256, artifact.receipt.size_bytes, artifact.receipt_sha256)):
            raise EditingError("artifact", "QC report identity mismatch")
        if report.probe is not None and report.probe != artifact.receipt.probe:
            raise EditingError("artifact", "QC report metadata mismatch")
        if report.checks[0].status == "passed":
            disabled = not output.manifest.headline.enabled and not output.manifest.captions.enabled
            if (report.checks[2].status == "not_applicable") != disabled:
                raise EditingError("artifact", "QC text applicability does not match saved plan")
        return report

    def get_report(self, campaign_id: str, video_id: str, plan_hash: str,
                   output_variant_id: str, render_key: str, qc_key: str) -> QcReport:
        try:
            target = QcTarget(output_variant_id=output_variant_id, render_key=render_key)
            plan = self.batch.get_plan(campaign_id, video_id, plan_hash)
            artifact = open_verified_render(work_root=self.work_root, plan=plan,
                output_variant_id=target.output_variant_id, render_key=target.render_key)
            with artifact.stream:
                return self._read_report(plan, target, qc_key, artifact)
        except (OSError, ValueError, StopIteration):
            raise EditingError("artifact", "cannot read valid QC report for current master") from None

    def run(self, request: QcRequest) -> QcBatchResult:
        request = QcRequest.model_validate(request.model_dump(mode="json", by_alias=True))
        plan = self.batch.get_plan(request.campaign_id, request.video_id, request.plan_hash)
        available = {o.output_variant_id for o in plan.outputs}
        if len(request.outputs) > len(available) or any(o.output_variant_id not in available for o in request.outputs):
            raise EditingError("outputs", "QC selection must belong to saved plan")
        context = request.model_dump(exclude={"outputs"})
        try:
            runtime = detect_runtime()
        except (EditingError, OSError, ValueError) as exc:
            return QcBatchResult(**context, outputs=[_error(target, exc) for target in request.outputs])
        outputs = []
        for target in request.outputs:
            try:
                outputs.append(self._run_one(plan, target, runtime))
            except (OSError, ValueError) as exc:
                outputs.append(_error(target, exc))
        return QcBatchResult(**context, outputs=outputs)

    def _run_one(self, plan: EditBatchPlan, target: QcTarget, runtime: RenderRuntime) -> QcOutputResult:
        artifact = open_verified_render(work_root=self.work_root, plan=plan,
            output_variant_id=target.output_variant_id, render_key=target.render_key)
        with artifact.stream:
            policy = QcPolicy()
            policy_hash = content_hash(policy.model_dump(mode="json", by_alias=True))
            key = qc_key(plan_hash=plan.plan_hash, output_variant_id=target.output_variant_id,
                render_key=target.render_key, master_sha256=artifact.receipt.sha256,
                receipt_sha256=artifact.receipt_sha256, qc_version=QC_VERSION,
                policy_hash=policy_hash, runtime_fingerprint=runtime.fingerprint)
            path = self._path(plan, target, key)
            output = next(o for o in plan.outputs if o.output_variant_id == target.output_variant_id)
            if path.exists():
                report = self._read_report(plan, target, key, artifact)
                if report.checks[0].status == "passed":
                    validate_qc_text_assets(output.manifest, plan.caption_input, editing=self.editing)
                return QcOutputResult(**target.model_dump(), status=report.status,
                    reused=True, qc_key=key, path=path.relative_to(self.work_root).as_posix())

            integrity, probe = check_qc_integrity(artifact.path,
                duration_sec=plan.source.duration_sec, receipt_probe=artifact.receipt.probe)
            audio = None
            if integrity.status == "blocked":
                dependent: tuple[Literal["audio", "text"], ...] = ("audio", "text")
                checks = [integrity, *[QcCheck(name=name, status="skipped", findings=[QcFinding(
                    code="integrity_prerequisite", field="output", message="master integrity blocked dependent check")])
                    for name in dependent]]
            else:
                audio_check, audio = measure_qc_audio(artifact.path, policy=policy)
                # ponytail: sequential local analysis; add scheduling only if personal batch throughput requires it.
                temporary = validate_editing_path(self.work_root, self.work_root / ".qc-staging")
                temporary.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="qc-", dir=temporary) as name:
                    staging = validate_editing_path(self.work_root, Path(name))
                    text_check = check_qc_text(output.manifest, plan.caption_input,
                        editing=self.editing, staging=staging, runtime=runtime)
                validate_qc_text_assets(output.manifest, plan.caption_input, editing=self.editing)
                checks = [integrity, audio_check, text_check]

            # Validate bytes again before publishing; never adopt an altered master or receipt.
            after = open_verified_render(work_root=self.work_root, plan=plan,
                output_variant_id=target.output_variant_id, render_key=target.render_key)
            with after.stream:
                if (after.receipt_sha256, after.receipt.sha256, after.receipt.size_bytes) != (
                        artifact.receipt_sha256, artifact.receipt.sha256, artifact.receipt.size_bytes):
                    raise EditingError("artifact", "master inputs changed during analysis")
            report = QcReport(campaign_id=plan.campaign_id, video_id=plan.video_id,
                plan_hash=plan.plan_hash, output_variant_id=target.output_variant_id,
                manifest_hash=output.manifest_hash, output_hash=output.output_hash,
                render_key=target.render_key, master_path=artifact.receipt.path,
                master_sha256=artifact.receipt.sha256, size_bytes=artifact.receipt.size_bytes,
                receipt_sha256=artifact.receipt_sha256, policy=policy, policy_hash=policy_hash,
                runtime=runtime, qc_key=key, probe=probe, audio=audio, checks=checks,
                status="blocked" if any(c.status == "blocked" for c in checks) else "passed")
            publish_editing_json(self.work_root, path,
                report.model_dump(mode="json", by_alias=True, exclude_computed_fields=True))
            return QcOutputResult(**target.model_dump(), status=report.status, qc_key=key,
                                  path=path.relative_to(self.work_root).as_posix())
