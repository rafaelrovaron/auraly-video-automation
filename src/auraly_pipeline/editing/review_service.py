from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from pydantic import TypeAdapter

from auraly_pipeline.editing.domain import EditingError, Sha, safe_id
from auraly_pipeline.editing.qc_domain import QcReport
from auraly_pipeline.editing.qc_service import QcService
from auraly_pipeline.editing.review_domain import HumanReviewRecord, HumanReviewRequest, review_key
from auraly_pipeline.editing.service import publish_editing_json, validate_editing_path


class HumanReviewService:
    def __init__(self, *, project_root: Path, work_root: Path) -> None:
        self.qc = QcService(project_root=project_root, work_root=work_root)
        self.project_root, self.work_root = self.qc.project_root, self.qc.work_root

    def _path(self, request: HumanReviewRequest, key: str) -> Path:
        return validate_editing_path(self.work_root, self.work_root / "campaigns" / request.campaign_id
            / "editing" / "reviews" / request.output_variant_id / request.render_key / key / "review.json")

    def _evidence(self, request: HumanReviewRequest) -> tuple[QcReport, str]:
        selectors = (request.campaign_id, request.video_id, request.plan_hash,
                     request.output_variant_id, request.render_key, request.qc_key)
        verified = self.qc.get_report(*selectors)
        path = validate_editing_path(self.work_root, self.work_root / "campaigns" / request.campaign_id
            / "editing" / "qc" / request.output_variant_id / request.render_key / request.qc_key / "report.json")
        raw = path.read_bytes()
        if QcReport.model_validate_json(raw) != verified or self.qc.get_report(*selectors) != verified:
            raise EditingError("artifact", "QC evidence changed during review")
        if validate_editing_path(self.work_root, path).read_bytes() != raw:
            raise EditingError("artifact", "QC report bytes changed during review")
        return verified, hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _request(record: HumanReviewRecord) -> HumanReviewRequest:
        return HumanReviewRequest.model_validate({name: getattr(record, name)
                                                 for name in HumanReviewRequest.model_fields})

    def _read(self, request: HumanReviewRequest, key: str) -> HumanReviewRecord:
        record = HumanReviewRecord.model_validate_json(self._path(request, key).read_bytes())
        if ((record.campaign_id, record.video_id, record.plan_hash, record.output_variant_id,
             record.render_key, record.review_key) != (request.campaign_id, request.video_id,
                request.plan_hash, request.output_variant_id, request.render_key, key)):
            raise EditingError("artifact", "review context mismatch")
        report, sha = self._evidence(self._request(record))
        if ((record.master_path, record.master_sha256, record.size_bytes, record.receipt_sha256,
             record.qc_status, record.qc_report_sha256) != (report.master_path, report.master_sha256,
                report.size_bytes, report.receipt_sha256, report.status, sha)):
            raise EditingError("artifact", "review evidence no longer matches")
        return record

    def _existing(self, request: HumanReviewRequest, key: str) -> HumanReviewRecord:
        record = self._read(request, key)
        if self._request(record) != request:
            raise EditingError("review", "definitive decision conflicts; create a new revision")
        return record

    def record(self, request: HumanReviewRequest) -> HumanReviewRecord:
        try:
            request = HumanReviewRequest.model_validate(request.model_dump())
            report, sha = self._evidence(request)
            if request.decision == "approved" and report.status != "passed":
                raise EditingError("review", "approval requires passed QC")
            key = review_key(campaign_id=request.campaign_id, video_id=request.video_id,
                plan_hash=request.plan_hash, output_variant_id=request.output_variant_id,
                render_key=request.render_key, master_sha256=report.master_sha256,
                receipt_sha256=report.receipt_sha256)
            path = self._path(request, key)
            if path.exists():
                return self._existing(request, key)
            record = HumanReviewRecord(**request.model_dump(), reviewed_at=datetime.now(timezone.utc),
                review_key=key, master_path=report.master_path, master_sha256=report.master_sha256,
                size_bytes=report.size_bytes, receipt_sha256=report.receipt_sha256,
                qc_report_sha256=sha, qc_status=report.status)
            if self._evidence(request) != (report, sha):
                raise EditingError("artifact", "evidence changed before review publication")
            try:
                publish_editing_json(self.work_root, path, record.model_dump(mode="json", by_alias=True))
            except EditingError:
                # Equal concurrent requests have distinct generated timestamps; reconcile the winner.
                if not path.exists():
                    raise
                return self._existing(request, key)
            return record
        except EditingError:
            raise
        except (OSError, ValueError):
            raise EditingError("artifact", "cannot record review for valid existing evidence") from None

    def get_review(self, campaign_id: str, video_id: str, plan_hash: str,
                   output_variant_id: str, render_key: str, review_key: str) -> HumanReviewRecord:
        try:
            for identifier in (campaign_id, video_id, output_variant_id):
                safe_id(identifier)
            for key in (plan_hash, render_key, review_key):
                TypeAdapter(Sha).validate_python(key)
            path = validate_editing_path(self.work_root, self.work_root / "campaigns" / campaign_id
                / "editing" / "reviews" / output_variant_id / render_key / review_key / "review.json")
            stored = HumanReviewRecord.model_validate_json(path.read_bytes())
            request = self._request(stored)
            if ((request.campaign_id, request.video_id, request.plan_hash, request.output_variant_id,
                 request.render_key) != (campaign_id, video_id, plan_hash, output_variant_id, render_key)):
                raise EditingError("artifact", "review context mismatch")
            return self._read(request, review_key)
        except (OSError, ValueError):
            raise EditingError("artifact", "cannot read valid review for current master and QC evidence") from None
