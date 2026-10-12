from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from auraly_pipeline.api.action_contracts import EditQcOperation, OperationView
from auraly_pipeline.api.contracts import QueryError
from auraly_pipeline.api.render_commands import RenderCommands
from auraly_pipeline.api.review_contracts import FinalReviewState, QcRunView, ReviewTarget
from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.delivery_domain import DeliveryRequest, DeliveryRecord, DeliveryState
from auraly_pipeline.editing.delivery_service import DeliveryService
from auraly_pipeline.editing.qc_domain import QcRequest, QcTarget
from auraly_pipeline.editing.qc_service import QcService
from auraly_pipeline.editing.review_domain import HumanReviewRecord, HumanReviewRequest
from auraly_pipeline.editing.review_service import HumanReviewService
from auraly_pipeline.jobs.service import JobService


class ReviewCommands:
    def __init__(self, *, renders: RenderCommands, qc: QcService, reviews: HumanReviewService,
                 jobs: JobService, get_operation: Callable[[str, str], OperationView],
                 deliveries: DeliveryService) -> None:
        self.renders, self.qc, self.reviews = renders, qc, reviews
        self.jobs, self.get_operation = jobs, get_operation
        self.deliveries = deliveries

    def _delivery_request(self, campaign_id: str, job_id: str, output_variant_id: str) -> DeliveryRequest:
        target = self._target(campaign_id, job_id, output_variant_id)
        try:
            review = self.reviews.find_review(campaign_id, target.video_id, target.plan_hash,
                                              output_variant_id, target.render_key)
            if review is None or review.decision != "approved":
                raise QueryError("operation_not_allowed")
            return DeliveryRequest(**{name: getattr(review, name) for name in DeliveryRequest.model_fields})
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def delivery_state(self, campaign_id: str, job_id: str, output_variant_id: str) -> DeliveryState:
        request = self._delivery_request(campaign_id, job_id, output_variant_id)
        try:
            return DeliveryState(request=request, record=self.deliveries.get(request))
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def delivery(self, campaign_id: str, job_id: str, output_variant_id: str,
                 request: DeliveryRequest) -> DeliveryRecord:
        if self._delivery_request(campaign_id, job_id, output_variant_id) != request:
            raise QueryError("invalid_request")
        try:
            return self.deliveries.deliver(request)
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def _target(self, campaign_id: str, job_id: str, output_variant_id: str) -> ReviewTarget:
        view = self.renders.get(campaign_id, job_id)
        if view.result is None:
            raise QueryError("operation_not_allowed")
        output = next((item for item in view.result.outputs if item.output_variant_id == output_variant_id), None)
        if output is None:
            raise QueryError("not_found")
        if output.status not in {"rendered", "reused"}:
            raise QueryError("operation_not_allowed")
        stream, _ = self.renders.open_media(campaign_id, job_id, output_variant_id)
        stream.close()
        return ReviewTarget(campaign_id=campaign_id, job_id=UUID(view.job_id), video_id=view.video_id,
            plan_hash=view.plan_hash, output_variant_id=output_variant_id, render_key=output.render_key)

    @staticmethod
    def _matches(target: ReviewTarget, request: EditQcOperation | HumanReviewRequest) -> None:
        names = ("campaign_id", "video_id", "plan_hash", "output_variant_id", "render_key")
        if (any(getattr(target, name) != getattr(request, name) for name in names)
                or isinstance(request, EditQcOperation) and target.job_id != request.job_id):
            raise QueryError("invalid_request")

    def validate_qc(self, request: EditQcOperation) -> QcRequest:
        target = self._target(request.campaign_id, str(request.job_id), request.output_variant_id)
        self._matches(target, request)
        return QcRequest(campaign_id=target.campaign_id, video_id=target.video_id, plan_hash=target.plan_hash,
            outputs=[QcTarget(output_variant_id=target.output_variant_id, render_key=target.render_key)])

    def state(self, campaign_id: str, job_id: str, output_variant_id: str) -> FinalReviewState:
        target = self._target(campaign_id, job_id, output_variant_id)
        selectors = (target.campaign_id, target.video_id, target.plan_hash, target.output_variant_id, target.render_key)
        try:
            reports = self.qc.list_reports(*selectors)
            review = self.reviews.find_review(*selectors)
            runs = []
            for summary in self.jobs.list_jobs(campaign_id=campaign_id):
                if summary.job_type != "api.local.operation":
                    continue
                job = self.jobs.get_job(summary.job_id)
                if job.input.get("operation") != "edit_qc":
                    continue
                request = EditQcOperation.model_validate(job.input)
                try:
                    self._matches(target, request)
                except QueryError:
                    continue
                view = self.get_operation(campaign_id, job.job_id)
                runs.append(QcRunView(**view.model_dump(exclude_computed_fields=True), request=request))
            return FinalReviewState(**target.model_dump(), reports=reports, review=review,
                                    runs=sorted(runs, key=lambda run: run.job_id))
        except (EditingError, OSError, ValueError):
            raise QueryError("artifact_invalid") from None

    def record(self, campaign_id: str, job_id: str, output_variant_id: str,
               request: HumanReviewRequest) -> HumanReviewRecord:
        target = self._target(campaign_id, job_id, output_variant_id)
        self._matches(target, request)
        try:
            return self.reviews.record(request)
        except EditingError as error:
            if error.field == "review":
                # Only an existing verified decision is a conflict; blocked QC is not.
                if self.reviews.find_review(campaign_id, target.video_id, target.plan_hash,
                                            output_variant_id, target.render_key) is not None:
                    raise QueryError("operation_conflict") from None
                raise QueryError("operation_not_allowed") from None
            raise QueryError("artifact_invalid") from None
