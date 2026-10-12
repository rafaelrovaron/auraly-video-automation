from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile

from auraly_pipeline.editing.delivery_domain import DeliveryRecord, DeliveryRequest
from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_artifacts import open_verified_render, render_work_root
from auraly_pipeline.editing.review_domain import HumanReviewRecord
from auraly_pipeline.editing.review_service import HumanReviewService
from auraly_pipeline.editing.service import publish_editing_json, validate_editing_path


class DeliveryService:
    def __init__(self, *, project_root: Path, work_root: Path, delivery_root: Path | None = None) -> None:
        self.reviews = HumanReviewService(project_root=project_root, work_root=work_root)
        self.work_root = self.reviews.work_root
        configured = os.getenv("AURALY_DELIVERY_ROOT", "").strip()
        root = delivery_root if delivery_root is not None else (
            Path(configured) if configured else self.work_root / "deliveries")
        root = render_work_root(root.expanduser())
        work = self.work_root.resolve()
        canonical = root.resolve()
        if (canonical == Path(canonical.anchor) or work.is_relative_to(canonical)
                or canonical.is_relative_to(work) and not canonical.is_relative_to(work / "deliveries")):
            raise EditingError("path", "delivery root overlaps managed artifacts")
        self.delivery_root = render_work_root(root)

    def _evidence(self, request: DeliveryRequest) -> tuple[HumanReviewRecord, str]:
        request = DeliveryRequest.model_validate(request.model_dump())
        record = self.reviews.get_review(request.campaign_id, request.video_id, request.plan_hash,
            request.output_variant_id, request.render_key, request.review_key)
        if record.decision != "approved" or record.qc_status != "passed":
            raise EditingError("review", "delivery requires an approved master")
        path = validate_editing_path(self.work_root, self.work_root / "campaigns" / request.campaign_id
            / "editing" / "reviews" / request.output_variant_id / request.render_key
            / request.review_key / "review.json")
        raw = path.read_bytes()
        if HumanReviewRecord.model_validate_json(raw) != record:
            raise EditingError("artifact", "review evidence changed")
        if self.reviews.get_review(request.campaign_id, request.video_id, request.plan_hash,
                request.output_variant_id, request.render_key, request.review_key) != record:
            raise EditingError("artifact", "review evidence changed")
        if validate_editing_path(self.work_root, path).read_bytes() != raw:
            raise EditingError("artifact", "review bytes changed")
        return record, hashlib.sha256(raw).hexdigest()

    def _paths(self, request: DeliveryRequest) -> tuple[Path, Path]:
        directory = self.delivery_root / request.campaign_id / request.output_variant_id / request.review_key
        return (validate_editing_path(self.delivery_root, directory / "master.mp4"),
                validate_editing_path(self.delivery_root, directory / "delivery.json"))

    def _verify_copy(self, path: Path, review: HumanReviewRecord) -> None:
        path = validate_editing_path(self.delivery_root, path)
        if not stat.S_ISREG(path.lstat().st_mode):
            raise EditingError("artifact", "regular delivery file required")
        with path.open("rb") as stream:
            if (os.fstat(stream.fileno()).st_size != review.size_bytes
                    or hashlib.file_digest(stream, "sha256").hexdigest() != review.master_sha256):
                raise EditingError("artifact", "delivery bytes do not match approved master")

    def _read(self, request: DeliveryRequest, evidence: tuple[HumanReviewRecord, str]) -> DeliveryRecord:
        review, review_sha = evidence
        master, receipt = self._paths(request)
        record = DeliveryRecord.model_validate_json(receipt.read_bytes())
        if (any(getattr(record, name) != getattr(request, name) for name in DeliveryRequest.model_fields)
                or (record.master_sha256, record.size_bytes, record.receipt_sha256,
                    record.qc_report_sha256, record.review_sha256) !=
                (review.master_sha256, review.size_bytes, review.receipt_sha256,
                    review.qc_report_sha256, review_sha)):
            raise EditingError("artifact", "delivery receipt evidence mismatch")
        self._verify_copy(master, review)
        return record

    def get(self, request: DeliveryRequest) -> DeliveryRecord | None:
        try:
            evidence = self._evidence(request)
            master, receipt = self._paths(request)
            if not master.exists() and not receipt.exists():
                return None
            return self._read(request, evidence)
        except EditingError:
            raise
        except (OSError, ValueError):
            raise EditingError("artifact", "cannot verify local delivery") from None

    def deliver(self, request: DeliveryRequest) -> DeliveryRecord:
        staging: Path | None = None
        try:
            evidence = self._evidence(request)
            review, review_sha = evidence
            master, receipt = self._paths(request)
            if receipt.exists():
                return self._read(request, evidence)
            master.parent.mkdir(parents=True, exist_ok=True)
            self._paths(request)
            if not master.exists():
                plan = self.reviews.qc.batch.get_plan(request.campaign_id, request.video_id, request.plan_hash)
                artifact = open_verified_render(work_root=self.work_root, plan=plan,
                    output_variant_id=request.output_variant_id, render_key=request.render_key,
                    expected_path=review.master_path)
                with artifact.stream:
                    fd, name = tempfile.mkstemp(prefix=".delivery-", suffix=".part", dir=master.parent)
                    staging = Path(name)
                    with os.fdopen(fd, "wb") as destination:
                        shutil.copyfileobj(artifact.stream, destination, 1024 * 1024)
                        destination.flush()
                        os.fsync(destination.fileno())
                self._verify_copy(staging, review)
                if self._evidence(request) != evidence:
                    raise EditingError("artifact", "evidence changed during copy")
                self._paths(request)
                try:
                    os.link(staging, master)
                except FileExistsError:
                    self._verify_copy(master, review)
            self._verify_copy(master, review)
            if self._evidence(request) != evidence:
                raise EditingError("artifact", "evidence changed before delivery publication")
            record = DeliveryRecord(**request.model_dump(), delivered_at=datetime.now(timezone.utc),
                path=master.relative_to(self.delivery_root).as_posix(),
                master_sha256=review.master_sha256, size_bytes=review.size_bytes,
                receipt_sha256=review.receipt_sha256, qc_report_sha256=review.qc_report_sha256,
                review_sha256=review_sha)
            try:
                publish_editing_json(self.delivery_root, receipt, record.model_dump(mode="json", by_alias=True))
            except EditingError:
                if not receipt.exists():
                    raise
                return self._read(request, self._evidence(request))
            verified = self.get(request)
            if verified is None:
                raise EditingError("artifact", "delivery publication missing")
            return verified
        except EditingError:
            raise
        except (OSError, ValueError):
            raise EditingError("artifact", "local delivery failed safely") from None
        finally:
            if staging is not None:
                staging.unlink(missing_ok=True)
