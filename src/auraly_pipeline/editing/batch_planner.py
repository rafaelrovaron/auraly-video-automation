from __future__ import annotations

from auraly_pipeline.editing.batch_domain import (
    BatchInputs, CaptionInput, CaptionTimingInput, CopyRef, EditBatchPlan,
    EditBatchRequest, EditPlannedOutput, EditVariant, ResolvedCaptionCue,
)
from auraly_pipeline.editing.domain import (
    EditManifestV2, EditResolveRequest, EditingError, IdentityRef, SourceVideoRef,
)
from auraly_pipeline.editing.resolver import content_hash, verify_manifest_hash


def _variant_id(campaign_id: str, video_id: str, source: SourceVideoRef, key: str) -> str:
    return "ab-" + content_hash({
        "campaignId": campaign_id, "videoId": video_id, "sourceId": source.id,
        "sourceSha256": source.sha256, "key": key,
    })[:60]


def variant_requests(request: EditBatchRequest, inputs: BatchInputs) -> list[tuple[EditVariant, EditResolveRequest]]:
    request = EditBatchRequest.model_validate(request.model_dump(mode="json", by_alias=True))
    if inputs.copy.approval_state != "approved" or inputs.copy.campaign_id != request.campaign_id:
        raise EditingError("copyRef", "approved campaign copy required")
    return [(variant, EditResolveRequest(
        profile_ref=request.profile_ref, campaign_id=request.campaign_id, video_id=request.video_id,
        output_variant_id=_variant_id(request.campaign_id, request.video_id, inputs.source, variant.key),
        source=inputs.source, headline_text=request.headline_text,
        copy_ref=IdentityRef(id=inputs.copy.copy_master_id, hash=inputs.copy.sha256),
        voice_ref=inputs.voice_ref, music_accepted=request.music_accepted,
        campaign=request.campaign, video=request.video, output_variant=variant.overrides,
    )) for variant in sorted(request.variants, key=lambda v: v.key)]


def _check_cues(caption: CaptionInput, duration: float) -> None:
    if caption.timing_status == "missing":
        return
    tokens = caption.text.split()
    index, end = 0, 0.0
    for cue in caption.cues:
        if (cue.token_start != index or cue.token_end > len(tokens)
                or cue.start_sec < end or cue.end_sec > duration
                or cue.text != " ".join(tokens[cue.token_start:cue.token_end])):
            raise EditingError("captionInput.cues", "invalid timing coverage, text or interval")
        index, end = cue.token_end, cue.end_sec
    if index != len(tokens):
        raise EditingError("captionInput.cues", "timing must cover all spoken tokens")


def _output_hash(manifest: EditManifestV2, caption: CaptionInput) -> str:
    return content_hash({"manifest": manifest.model_dump(mode="json", by_alias=True),
                         "captionInput": caption.model_dump(mode="json", by_alias=True),
                         "plannerVersion": "1.0"})


def build_batch_plan(request: EditBatchRequest, inputs: BatchInputs,
                     manifests: list[EditManifestV2], *,
                     timing: CaptionTimingInput | None = None) -> EditBatchPlan:
    requests = variant_requests(request, inputs)
    copy_ref = CopyRef(id=inputs.copy.copy_master_id, version=inputs.copy.version, hash=inputs.copy.sha256)
    caption = CaptionInput(copy_ref=copy_ref, voice_ref=inputs.voice_ref,
                           text=inputs.copy.spoken_text, timing_status="missing")
    if (timing is None) != (request.timing_ref is None):
        raise EditingError("timingRef", "timing input and reference must be supplied together")
    if timing is not None:
        timing = CaptionTimingInput.model_validate(timing.model_dump(mode="json", by_alias=True))
        if (timing.source_sha256, timing.copy_master_id, timing.copy_hash, timing.processed_audio_sha256) != (
                inputs.source.sha256, copy_ref.id, copy_ref.hash, inputs.voice_ref.hash):
            raise EditingError("timingRef", "timing identity/content mismatch")
        tokens = inputs.copy.spoken_text.split()
        caption = CaptionInput(
            copy_ref=copy_ref, voice_ref=inputs.voice_ref, text=inputs.copy.spoken_text,
            timing_status="provided", timing_ref=request.timing_ref, origin=timing.origin,
            accepted_by=timing.accepted_by, timebase=timing.timebase,
            cues=[ResolvedCaptionCue(**cue.model_dump(by_alias=True),
                text=" ".join(tokens[cue.token_start:cue.token_end])) for cue in timing.cues],
        )
    _check_cues(caption, inputs.source.duration_sec)
    if len(manifests) != len(requests):
        raise EditingError("outputs", "one manifest required per variant")
    outputs = []
    for (variant, resolved_request), manifest in zip(requests, manifests, strict=True):
        if (manifest.output_variant_id != resolved_request.output_variant_id
                or manifest.profile_ref != request.profile_ref
                or manifest.overrides != {"campaign": request.campaign, "video": request.video,
                                         "outputVariant": variant.overrides}):
            raise EditingError("outputs", "manifest does not match variant request")
        output_hash = _output_hash(manifest, caption)
        outputs.append(EditPlannedOutput(
            key=variant.key, label=variant.label, output_variant_id=manifest.output_variant_id,
            manifest=manifest, manifest_hash=manifest.manifest_hash, output_hash=output_hash,
            filename=f"{manifest.output_variant_id}-{output_hash}.mp4",
            caption_state=("disabled" if not manifest.captions.enabled else
                           "timing_provided" if caption.timing_status == "provided" else "timing_missing"),
        ))
    plan = EditBatchPlan(
        campaign_id=request.campaign_id, render_id=request.render_id, video_id=request.video_id,
        source=inputs.source, copy_ref=copy_ref, voice_ref=inputs.voice_ref, image_ref=inputs.image_ref,
        max_outputs=request.max_outputs, output_count=len(outputs), caption_input=caption,
        outputs=outputs, plan_hash="0" * 64,
    )
    plan.plan_hash = content_hash(plan.model_dump(mode="json", by_alias=True, exclude={"plan_hash"}))
    verify_batch_plan(plan)
    return plan


def verify_batch_plan(plan: EditBatchPlan) -> None:
    plan = EditBatchPlan.model_validate(plan.model_dump(mode="json", by_alias=True))
    if plan.plan_hash != content_hash(plan.model_dump(mode="json", by_alias=True, exclude={"plan_hash"})):
        raise EditingError("planHash", "plan content mismatch")
    keys = [o.key for o in plan.outputs]
    if (keys != sorted(set(keys)) or plan.output_count != len(keys)
            or plan.output_count > plan.max_outputs or plan.source.id != plan.render_id):
        raise EditingError("outputs", "invalid plan count/order/identity")
    if (plan.caption_input.copy_ref != plan.copy_ref or plan.caption_input.voice_ref != plan.voice_ref):
        raise EditingError("captionInput", "upstream references mismatch")
    _check_cues(plan.caption_input, plan.source.duration_sec)
    ids = set()
    for output in plan.outputs:
        manifest = output.manifest
        verify_manifest_hash(manifest)
        expected_id = _variant_id(plan.campaign_id, plan.video_id, plan.source, output.key)
        expected_hash = _output_hash(manifest, plan.caption_input)
        state = "disabled" if not manifest.captions.enabled else "timing_" + plan.caption_input.timing_status
        if (output.output_variant_id != expected_id or expected_id in ids
                or manifest.output_variant_id != expected_id
                or manifest.campaign_id != plan.campaign_id or manifest.video_id != plan.video_id
                or manifest.source != plan.source
                or manifest.copy_ref != IdentityRef(id=plan.copy_ref.id, hash=plan.copy_ref.hash)
                or manifest.voice_ref != plan.voice_ref
                or output.manifest_hash != manifest.manifest_hash
                or output.output_hash != expected_hash or output.caption_state != state
                or output.filename != f"{expected_id}-{expected_hash}.mp4"):
            raise EditingError("outputs", "inconsistent output identity/content")
        ids.add(expected_id)
