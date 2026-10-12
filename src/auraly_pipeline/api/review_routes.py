from __future__ import annotations

from fastapi import FastAPI, Response

from auraly_pipeline.api.action_contracts import EditQcOperation, OperationSubmission
from auraly_pipeline.api.action_routes import Commands, matching
from auraly_pipeline.api.app import CampaignId, EditId, JobId
from auraly_pipeline.api.review_contracts import FinalReviewState
from auraly_pipeline.editing.review_domain import HumanReviewRecord, HumanReviewRequest
from auraly_pipeline.editing.delivery_domain import DeliveryRecord, DeliveryRequest, DeliveryState


def register_review_routes(app: FastAPI) -> None:
    prefix = "/api/v1/campaigns/{campaignId}/editing/renders/{jobId}/outputs/{outputVariantId}"

    @app.get(prefix + "/delivery")
    def delivery_state(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
                       commands: Commands, response: Response) -> DeliveryState:
        response.headers["Cache-Control"] = "no-store"
        return commands.editorial_reviews.delivery_state(campaignId, jobId, outputVariantId)

    @app.post(prefix + "/delivery")
    def delivery(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
                 body: DeliveryRequest, commands: Commands) -> DeliveryRecord:
        return commands.editorial_reviews.delivery(campaignId, jobId, outputVariantId, body)

    @app.get(prefix + "/review-state")
    def state(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
              commands: Commands, response: Response) -> FinalReviewState:
        response.headers["Cache-Control"] = "no-store"
        return commands.editorial_reviews.state(campaignId, jobId, outputVariantId)

    @app.post(prefix + "/qc", status_code=202)
    def qc(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
           body: EditQcOperation, commands: Commands) -> OperationSubmission:
        matching(campaignId, body.campaign_id)
        matching(jobId, str(body.job_id))
        matching(outputVariantId, body.output_variant_id)
        return commands.submit_operation(body)

    @app.post(prefix + "/review")
    def review(campaignId: CampaignId, jobId: JobId, outputVariantId: EditId,
               body: HumanReviewRequest, commands: Commands) -> HumanReviewRecord:
        return commands.editorial_reviews.record(campaignId, jobId, outputVariantId, body)
