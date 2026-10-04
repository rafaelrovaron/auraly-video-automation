from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, FastAPI, Request
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from auraly_pipeline.api.action_contracts import (
    EditPlanOperation, HeyGenAssetsOperation, HeyGenReconcileOperation,
    HeyGenVideoPlanOperation, HeyGenVideoSubmitOperation, ImageImportOperation,
    ImagePrepareOperation, ImageManifestOperation, ImageReviewAction, LocalOperationRequest,
    OperationRequest, OperationSubmission, OperationView,
    VoiceImportOperation, VoiceReviewOperation, WorkerStartAction, WorkerState,
)
from auraly_pipeline.api.app import CampaignId, EditId, JobId, Version
from auraly_pipeline.api.commands import ApiCommands
from auraly_pipeline.api.contracts import CampaignDetail, ImageSummary, JobSummary, ProfileView, QueryError
from auraly_pipeline.api.worker import LocalApiWorker
from auraly_pipeline.campaigns.domain import CampaignCreate, CopyMasterCreate
from auraly_pipeline.campaigns.persistence import validate_api_database
from auraly_pipeline.editing.domain import EditProfile
from auraly_pipeline.voices.domain import VoiceGenerateRequest


def command_dependency(request: Request) -> ApiCommands:
    try:
        validate_api_database(cast(Engine, request.app.state.write_engine))
    except (SQLAlchemyError, ValueError, OSError):
        raise QueryError("storage_unavailable") from None
    return cast(ApiCommands, request.app.state.commands)


Commands = Annotated[ApiCommands, Depends(command_dependency)]


def matching(campaign_id: str, body_campaign_id: str) -> None:
    if campaign_id != body_campaign_id:
        raise QueryError("invalid_request")


def enqueue(campaign_id: str, body: LocalOperationRequest, commands: ApiCommands) -> OperationSubmission:
    matching(campaign_id, body.campaign_id)
    return commands.submit_operation(body)


def register_action_routes(app: FastAPI) -> None:
    prefix = "/api/v1/campaigns/{campaignId}"

    @app.post("/api/v1/campaigns", status_code=201)
    def create_campaign(body: CampaignCreate, commands: Commands) -> CampaignDetail:
        return commands.create_campaign(body)

    @app.post(prefix + "/copies", status_code=201)
    def add_copy(campaignId: CampaignId, body: CopyMasterCreate, commands: Commands) -> CampaignDetail:
        return commands.add_copy(campaignId, body)

    @app.post(prefix + "/images/import/prepare", status_code=202)
    def prepare_images(campaignId: CampaignId, body: ImagePrepareOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/images/import", status_code=202)
    def import_images(campaignId: CampaignId, body: ImageImportOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/images/import/manifests", status_code=202)
    def publish_image_manifest(campaignId: CampaignId, body: ImageManifestOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/images/{candidateId}/review")
    def review_image(campaignId: CampaignId, candidateId: JobId, body: ImageReviewAction, commands: Commands) -> ImageSummary:
        matching(campaignId, body.campaign_id)
        return commands.review_image(campaignId, candidateId, body)

    @app.post(prefix + "/voices/generate", status_code=202)
    def generate_voice(campaignId: CampaignId, body: VoiceGenerateRequest, commands: Commands) -> OperationSubmission:
        matching(campaignId, body.campaign_id)
        return commands.submit_voice(body)

    @app.post(prefix + "/voices/import", status_code=202)
    def import_voice(campaignId: CampaignId, body: VoiceImportOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/voices/{voiceId}/review", status_code=202)
    def review_voice(campaignId: CampaignId, voiceId: JobId, body: VoiceReviewOperation, commands: Commands) -> OperationSubmission:
        if voiceId != body.voice_id:
            raise QueryError("invalid_request")
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/heygen/assets/prepare", status_code=202)
    def prepare_heygen_assets(campaignId: CampaignId, body: HeyGenAssetsOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/heygen/videos/plan", status_code=202)
    def plan_heygen_videos(campaignId: CampaignId, body: HeyGenVideoPlanOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/heygen/videos/submit", status_code=202)
    def submit_heygen_videos(campaignId: CampaignId, body: HeyGenVideoSubmitOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/heygen/renders/{renderId}/reconcile", status_code=202)
    def reconcile_heygen(campaignId: CampaignId, renderId: JobId, body: HeyGenReconcileOperation, commands: Commands) -> OperationSubmission:
        if renderId != body.render_id:
            raise QueryError("invalid_request")
        return enqueue(campaignId, body, commands)

    @app.post("/api/v1/editing/profiles", status_code=201)
    def create_profile(body: EditProfile, commands: Commands) -> ProfileView:
        return commands.publish_profile(body)

    @app.post("/api/v1/editing/profiles/{profileId}/{baseVersion}/versions", status_code=201)
    def create_profile_version(profileId: EditId, baseVersion: Version, body: EditProfile, commands: Commands) -> ProfileView:
        if profileId != body.profile_id:
            raise QueryError("invalid_request")
        return commands.publish_profile(body, base_version=baseVersion)

    @app.post(prefix + "/editing/plans", status_code=202)
    def plan_edit(campaignId: CampaignId, body: EditPlanOperation, commands: Commands) -> OperationSubmission:
        return enqueue(campaignId, body, commands)

    @app.post(prefix + "/jobs/{jobId}/cancel")
    def cancel_job(campaignId: CampaignId, jobId: JobId, body: OperationRequest, commands: Commands) -> JobSummary:
        matching(campaignId, body.campaign_id)
        return commands.change_job(campaignId, jobId, resume=False)

    @app.post(prefix + "/jobs/{jobId}/resume")
    def resume_job(campaignId: CampaignId, jobId: JobId, body: OperationRequest, commands: Commands) -> JobSummary:
        matching(campaignId, body.campaign_id)
        return commands.change_job(campaignId, jobId, resume=True)

    @app.post(prefix + "/worker/start", status_code=202)
    def start_worker(campaignId: CampaignId, body: WorkerStartAction, commands: Commands, request: Request) -> WorkerState:
        matching(campaignId, body.campaign_id)
        return cast(LocalApiWorker, request.app.state.worker).start(campaignId, body.kind)

    @app.post(prefix + "/worker/stop")
    def stop_worker(campaignId: CampaignId, body: OperationRequest, commands: Commands, request: Request) -> WorkerState:
        matching(campaignId, body.campaign_id)
        return cast(LocalApiWorker, request.app.state.worker).stop(campaignId)

    @app.get(prefix + "/worker")
    def worker_status(campaignId: CampaignId, commands: Commands, request: Request) -> WorkerState:
        return cast(LocalApiWorker, request.app.state.worker).status(campaignId)

    @app.get(prefix + "/operations/{jobId}")
    def operation_status(campaignId: CampaignId, jobId: JobId, commands: Commands) -> OperationView:
        return commands.get_operation(campaignId, jobId)
