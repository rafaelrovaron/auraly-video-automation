from __future__ import annotations

from auraly_pipeline.api.contracts import (
    CampaignStatus, OperationalStatus, PendingCode, PendingItem, QueryError, SceneStatus,
)
from auraly_pipeline.campaigns.domain import Campaign
from auraly_pipeline.editing.batch_domain import EditBatchPlan
from auraly_pipeline.heygen.domain import AssetUploadJobInput
from auraly_pipeline.heygen.video_domain import HeyGenRender
from auraly_pipeline.images.domain import ImageCandidate
from auraly_pipeline.jobs.domain import Job
from auraly_pipeline.voices.domain import VoiceMaster

PENDING: dict[PendingCode, tuple[OperationalStatus, str, str]] = {
    "attention_required": ("needs_attention", "heygen", "Inspect the current failed or blocked operation."),
    "wait_for_job": ("in_progress", "heygen", "Wait for the current operation to complete."),
    "editing_plan_missing": ("ready_for_editing", "editing", "Create an editing plan for this source."),
    "caption_timing_missing": ("needs_input", "editing", "Provide timing for enabled captions."),
    "renderer_not_implemented": ("editing_planned", "editing", "Final rendering is a future milestone."),
    "copy_approval_missing": ("needs_input", "copy", "Provide an approved Copy Master."),
    "voice_review_required": ("needs_review", "voice", "Review the current Voice Master."),
    "voice_missing": ("needs_input", "voice", "Provide an approved Voice Master."),
    "image_review_required": ("needs_review", "images", "Review the scene image candidates."),
    "image_missing": ("needs_input", "images", "Import images for this scene."),
    "heygen_render_missing": ("needs_input", "heygen", "Generate the scene video with HeyGen."),
}
PRIORITY: dict[OperationalStatus, int] = {
    "needs_attention": 0, "in_progress": 1, "needs_review": 2, "needs_input": 3,
    "ready_for_editing": 4, "editing_planned": 5,
}


def pending(code: PendingCode, entity_id: str) -> PendingItem:
    _, stage, message = PENDING[code]
    return PendingItem.model_validate({"code": code, "stage": stage, "entityId": entity_id, "message": message})


def job_pending(job: Job) -> PendingItem | None:
    item: PendingItem | None = None
    if job.status in {"failed", "blocked"}:
        item = pending("attention_required", job.job_id)
    elif job.status in {"queued", "running", "retry_scheduled"}:
        item = pending("wait_for_job", job.job_id)
    if item and job.job_type == "voice.generate":
        return item.model_copy(update={"stage": "voice"})
    return item


def compute_campaign_status(
    campaign: Campaign, *, images: list[ImageCandidate], voices: list[VoiceMaster],
    renders: list[HeyGenRender], jobs: list[Job], plans: list[EditBatchPlan],
) -> CampaignStatus:
    copies = {copy.copy_master_id: copy for copy in campaign.copy_masters if copy.approval_state == "approved"}
    approved = [voice for voice in voices if voice.status == "approved" and voice.copy_master_id in copies]
    current_voice = max(approved, key=lambda v: (v.copy_master_version, v.generation, v.created_at, v.voice_master_id), default=None)
    current_copy = copies.get(current_voice.copy_master_id) if current_voice else max(copies.values(), key=lambda c: c.version, default=None)
    current_candidates = [v for v in voices if current_copy and v.copy_master_id == current_copy.copy_master_id]
    unapproved = max(current_candidates, key=lambda v: (v.generation, v.created_at, v.voice_master_id), default=None)
    scenes: list[SceneStatus] = []
    ranked: list[tuple[int, str, str, str, PendingItem]] = []
    for scene in sorted(campaign.scene_variants, key=lambda s: (s.variant_id, s.scene_variant_id)):
        candidates = [i for i in images if i.scene_variant_id == scene.scene_variant_id]
        image = next((i for i in sorted(candidates, key=lambda i: i.image_candidate_id) if i.review_status == "approved"), None)
        scene_renders = sorted([r for r in renders if r.item.scene_variant_id == scene.scene_variant_id], key=lambda r: (r.created_at, r.render_id))
        ready = [r for r in scene_renders if r.status == "ready"]
        matched_plans = [p for p in plans if any(r.source and p.render_id == r.render_id and p.source.sha256 == r.source.sha256 for r in ready)]
        items: list[PendingItem] = []
        compatible = [r for r in scene_renders if current_voice and image and r.item.voice_master_id == current_voice.voice_master_id and r.item.image_candidate_id == image.image_candidate_id]
        latest = compatible[-1] if compatible else None
        if latest and latest.status != "ready":
            code: PendingCode = "attention_required" if latest.status in {"failed", "reconciliation_required"} else "wait_for_job"
            items.append(pending(code, latest.render_id))
        for render in ready:
            if render.source is None:
                raise QueryError("artifact_invalid")
            render_plans = [p for p in matched_plans if p.render_id == render.render_id]
            if not render_plans:
                items.append(pending("editing_plan_missing", render.render_id))
            else:
                missing = [o for p in render_plans for o in p.outputs if o.caption_state == "timing_missing"]
                items.extend(pending("caption_timing_missing", o.output_variant_id) for o in missing)
                if not missing:
                    items.append(pending("renderer_not_implemented", render.render_id))
        if not ready:
            relevant_jobs = [j for j in jobs if j.campaign_id == campaign.campaign_id and (
                latest and j.job_id == latest.job_id
                or not current_voice and unapproved and j.job_type == "voice.generate"
                and j.input.get("voiceMasterId") == unapproved.voice_master_id
            )]
            uploads: list[Job] = []
            for job in jobs:
                if job.campaign_id != campaign.campaign_id or job.job_type != "heygen.asset.upload":
                    continue
                sources = AssetUploadJobInput.model_validate(job.input).sources
                if any((current_voice and s.source_id == current_voice.voice_master_id and s.sha256 == current_voice.processed_sha256)
                       or (image and s.source_id == image.image_candidate_id and s.sha256 == image.sha256) for s in sources):
                    uploads.append(job)
            if uploads:
                relevant_jobs.append(max(uploads, key=lambda j: (j.created_at, j.job_id)))
            items.extend(item for job in relevant_jobs if (item := job_pending(job)) is not None)
            if not items:
                if current_copy is None:
                    items.append(pending("copy_approval_missing", scene.scene_variant_id))
                elif current_voice is None:
                    review = unapproved and unapproved.status == "review_required"
                    items.append(pending("voice_review_required" if review else "voice_missing",
                                         unapproved.voice_master_id if review and unapproved else scene.scene_variant_id))
                elif image is None:
                    review_image = next((i for i in sorted(candidates, key=lambda i: i.image_candidate_id) if i.review_status == "pending_review"), None)
                    items.append(pending("image_review_required" if review_image else "image_missing",
                                         review_image.image_candidate_id if review_image else scene.scene_variant_id))
                else:
                    items.append(pending("heygen_render_missing", scene.scene_variant_id))
        items = sorted({(i.code, i.entity_id): i for i in items}.values(), key=lambda i: (PRIORITY[PENDING[i.code][0]], i.entity_id, i.code))
        ranked.extend((PRIORITY[PENDING[i.code][0]], scene.variant_id, i.entity_id, i.code, i) for i in items)
        scenes.append(SceneStatus(
            scene_variant_id=scene.scene_variant_id, variant_id=scene.variant_id,
            current_copy_id=current_copy.copy_master_id if current_copy else None,
            current_voice_id=current_voice.voice_master_id if current_voice else None,
            approved_image_id=image.image_candidate_id if image else None,
            ready_render_ids=sorted(r.render_id for r in ready),
            plan_hashes=sorted(p.plan_hash for p in matched_plans), pending=items,
        ))
    next_pending = min(ranked, key=lambda item: item[:4])[4] if ranked else None
    return CampaignStatus(
        campaign_id=campaign.campaign_id, stored_status=campaign.status,
        operational_status=PENDING[next_pending.code][0] if next_pending else "needs_input",
        next_pending=next_pending, scene_count=len(scenes), approved_copy_count=len(copies),
        approved_voice_count=len(approved), approved_image_count=sum(i.review_status == "approved" for i in images),
        ready_render_count=sum(len(s.ready_render_ids) for s in scenes), plan_count=len(plans), scenes=scenes,
    )
