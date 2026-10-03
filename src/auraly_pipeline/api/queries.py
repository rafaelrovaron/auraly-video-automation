from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from auraly_pipeline.api.contracts import (
    ApiSettings, CampaignDetail, CampaignStatus, CampaignSummary, ImageSummary, JobSummary,
    OutputSummary, PlanSummary, ProfileView,
    QueryError, RenderSummary, SceneImages, VoiceSummary,
)
from auraly_pipeline.campaigns.domain import Campaign
from auraly_pipeline.campaigns.service import CampaignNotFoundError, CampaignService
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditingArtifactNotFoundError, EditingError
from auraly_pipeline.editing.resolver import profile_hash
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.heygen.video_repository import HeyGenVideoRepository
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.repository import JobRepository
from auraly_pipeline.jobs.service import JobNotFoundError, JobService
from auraly_pipeline.voices.service import VoiceMasterService
from auraly_pipeline.api.status import compute_campaign_status


class ApiQueries:
    def __init__(self, settings: ApiSettings, engine: Engine) -> None:
        sessions = sessionmaker(engine, expire_on_commit=False)
        self.campaigns = CampaignService(engine)
        self.jobs = JobService(engine, JobRepository(sessions), handlers={})
        self.images = ImageService(engine, self.jobs, work_root=settings.work_root)
        self.voices = VoiceMasterService(engine, work_root=settings.work_root)
        self.renders = HeyGenVideoRepository(sessions)
        self.editing = EditingService(project_root=settings.project_root, work_root=settings.work_root)
        self.batches = EditBatchService(project_root=settings.project_root, work_root=settings.work_root)

    def campaign(self, campaign_id: str) -> Campaign:
        try:
            return self.campaigns.get_campaign(campaign_id)
        except CampaignNotFoundError:
            raise QueryError("not_found", "campaignId") from None

    def get_campaign(self, campaign_id: str) -> CampaignDetail:
        campaign = self.campaign(campaign_id)
        status = self.get_status(campaign_id)
        return CampaignDetail(
            campaign_id=campaign.campaign_id, character=campaign.character,
            stored_status=campaign.status, scene_count=len(campaign.scene_variants),
            created_at=campaign.created_at, updated_at=campaign.updated_at,
            proof_object=campaign.proof_object, voice_preset=campaign.voice_preset,
            edit_preset=campaign.edit_preset, copy_masters=campaign.copy_masters,
            scene_variants=campaign.scene_variants,
            operational_status=status.operational_status, next_pending=status.next_pending,
        )

    def list_campaigns(self) -> list[CampaignSummary]:
        return [CampaignSummary.model_validate(self.get_campaign(campaign.campaign_id).model_dump(
            include=set(CampaignSummary.model_fields)))
            for campaign in sorted(self.campaigns.list_campaigns(), key=lambda c: c.campaign_id)]

    def get_status(self, campaign_id: str) -> CampaignStatus:
        campaign = self.campaign(campaign_id)
        try:
            return compute_campaign_status(
                campaign, images=[image for scene in campaign.scene_variants
                                  for image in self.images.list_candidates_for_scene(scene.scene_variant_id)],
                voices=self.voices.list(campaign_id=campaign_id),
                renders=self.renders.list_campaign(campaign_id),
                jobs=self.jobs.list_jobs(campaign_id=campaign_id),
                plans=self.batches.list_plans(campaign_id),
            )
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def list_images(self, campaign_id: str) -> list[SceneImages]:
        campaign = self.campaign(campaign_id)
        return [SceneImages(scene_variant_id=scene.scene_variant_id, items=[
            ImageSummary.model_validate(candidate.model_dump(include=set(ImageSummary.model_fields)))
            for candidate in sorted(self.images.list_candidates_for_scene(scene.scene_variant_id),
                                    key=lambda item: item.image_candidate_id)
        ]) for scene in sorted(campaign.scene_variants, key=lambda item: item.scene_variant_id)]

    def list_voices(self, campaign_id: str) -> list[VoiceSummary]:
        self.campaign(campaign_id)
        return [VoiceSummary.model_validate(voice.model_dump(include=set(VoiceSummary.model_fields)))
                for voice in sorted(self.voices.list(campaign_id=campaign_id),
                                    key=lambda item: item.voice_master_id)]

    def list_renders(self, campaign_id: str) -> list[RenderSummary]:
        self.campaign(campaign_id)
        return [RenderSummary(
            render_id=render.render_id, campaign_id=render.item.campaign_id,
            scene_variant_id=render.item.scene_variant_id,
            image_candidate_id=render.item.image_candidate_id,
            voice_master_id=render.item.voice_master_id, job_id=render.job_id,
            status=render.status, remote_video_id=render.remote_video_id,
            manual_binding=render.manual_binding, image_sha256=render.item.image_sha256,
            audio_sha256=render.item.audio_sha256, source=render.source, error_code=render.error_code,
            created_at=render.created_at, updated_at=render.updated_at,
        ) for render in sorted(self.renders.list_campaign(campaign_id), key=lambda item: item.render_id)]

    def list_jobs(self, campaign_id: str) -> list[JobSummary]:
        self.campaign(campaign_id)
        return [JobSummary.model_validate(job.model_dump(include=set(JobSummary.model_fields)))
                for job in sorted(self.jobs.list_jobs(campaign_id=campaign_id), key=lambda item: item.job_id)]

    def get_job(self, campaign_id: str, job_id: str) -> JobSummary:
        self.campaign(campaign_id)
        try:
            job = self.jobs.get_job(job_id)
        except JobNotFoundError:
            raise QueryError("not_found", "jobId") from None
        if job.campaign_id != campaign_id:
            raise QueryError("not_found", "jobId")
        return JobSummary.model_validate(job.model_dump(include=set(JobSummary.model_fields)))

    def list_profiles(self) -> list[ProfileView]:
        try:
            return [ProfileView(profile=profile, profile_hash=profile_hash(profile))
                    for profile in self.editing.list_profiles()]
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def get_profile(self, profile_id: str, version: int) -> ProfileView:
        try:
            profile = self.editing.get_profile(profile_id, version)
            return ProfileView(profile=profile, profile_hash=profile_hash(profile))
        except EditingArtifactNotFoundError:
            raise QueryError("not_found", "profileId") from None
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def list_plans(self, campaign_id: str) -> list[PlanSummary]:
        self.campaign(campaign_id)
        try:
            return [PlanSummary(
                video_id=plan.video_id, render_id=plan.render_id, plan_hash=plan.plan_hash,
                output_count=plan.output_count, max_outputs=plan.max_outputs,
                timing_status=plan.caption_input.timing_status, copy_ref=plan.copy_ref,
                voice_ref=plan.voice_ref, source=plan.source, outputs=[
                    OutputSummary.model_validate(output.model_dump(include=set(OutputSummary.model_fields)))
                    for output in plan.outputs
                ],
            ) for plan in self.batches.list_plans(campaign_id)]
        except EditingError:
            raise QueryError("artifact_invalid") from None

    def get_plan(self, campaign_id: str, video_id: str, plan_hash: str) -> EditBatchPlan:
        self.campaign(campaign_id)
        try:
            return self.batches.get_plan(campaign_id, video_id, plan_hash)
        except EditingArtifactNotFoundError:
            raise QueryError("not_found", "planHash") from None
        except EditingError:
            raise QueryError("artifact_invalid") from None
