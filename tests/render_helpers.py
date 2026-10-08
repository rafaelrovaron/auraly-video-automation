from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from auraly_pipeline.editing.batch_domain import EditBatchPlan, EditBatchRequest
from auraly_pipeline.editing.batch_planner import build_batch_plan, variant_requests
from auraly_pipeline.editing.domain import EditProfile, SourceVideoRef
from auraly_pipeline.editing.resolver import content_hash, profile_hash, resolve_manifest
from auraly_pipeline.probe import probe_media
from tests.editing_batch_helpers import batch_data, batch_inputs
from tests.editing_helpers import file_sha, profile_data


def make_render_plan(project_root: Path, *, pattern: bool = False) -> EditBatchPlan:
    project_root.mkdir(parents=True, exist_ok=True)
    font = project_root / "font.ttf"
    choices = (Path("C:/Windows/Fonts/arial.ttf"),
               Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
               Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"))
    origin = next((p for p in choices if p.is_file()), None)
    assert origin is not None, "a local test font is required (no silent skip)"
    shutil.copyfile(origin, font)
    source = project_root / "source.mp4"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        ("color=c=black:s=320x180:r=30:d=2" + (
            ",drawbox=x=0:y=0:w=160:h=90:c=red:t=fill,drawbox=x=160:y=0:w=160:h=90:c=green:t=fill,"
            "drawbox=x=0:y=90:w=160:h=90:c=blue:t=fill,drawbox=x=160:y=90:w=160:h=90:c=white:t=fill"
            if pattern else "")), "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], capture_output=True, check=True, timeout=30)
    profile = EditProfile.model_validate(profile_data())
    profile.defaults.headline.enabled = True
    from auraly_pipeline.editing.domain import AssetRef
    profile.defaults.headline.font = AssetRef(path="font.ttf", sha256=file_sha(font))
    inputs = batch_inputs()
    inputs.source = SourceVideoRef(id=batch_data()["renderId"], path="source.mp4",
                                  sha256=file_sha(source), duration_sec=probe_media(source).duration_sec)
    request = EditBatchRequest.model_validate(batch_data())
    request.profile_ref.hash = profile_hash(profile)
    manifests = [resolve_manifest(profile, r) for _, r in variant_requests(request, inputs)]
    return build_batch_plan(request, inputs, manifests)


def publish_test_plan(plan: EditBatchPlan, work_root: Path) -> None:
    path = (work_root / "campaigns" / plan.campaign_id / "editing" / "plans"
            / plan.video_id / plan.plan_hash / "plan.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan.model_dump_json(by_alias=True), encoding="utf-8")


def refresh_plan(plan: EditBatchPlan) -> None:
    for output in plan.outputs:
        m = output.manifest
        m.manifest_hash = content_hash(m.model_dump(mode="json", by_alias=True, exclude={"manifest_hash"}))
        output.manifest_hash = m.manifest_hash
        output.output_hash = content_hash({"manifest": m.model_dump(mode="json", by_alias=True),
            "captionInput": plan.caption_input.model_dump(mode="json", by_alias=True), "plannerVersion": "1.0"})
        output.filename = f"{output.output_variant_id}-{output.output_hash}.mp4"
        output.caption_state = ("disabled" if not m.captions.enabled else
                                "timing_provided" if plan.caption_input.timing_status == "provided" else "timing_missing")
    plan.plan_hash = content_hash(plan.model_dump(mode="json", by_alias=True, exclude={"plan_hash"}))
