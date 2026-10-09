from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_domain import RenderReceipt, RenderRuntime, render_key
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.probe import MediaProbe
from tests.render_helpers import make_render_plan
from tests.test_qc_domain import report_data


@pytest.fixture
def artifact_case(tmp_path: Path) -> tuple[Any, Path, Path, str]:
    plan = make_render_plan(tmp_path / "project")
    root = RenderService(project_root=tmp_path / "project", work_root=tmp_path / "project" / "work").work_root
    output = plan.outputs[0]
    runtime = RenderRuntime(fingerprint="e" * 64, ffmpeg_version="old build", libass_version="old", encoding={})
    key = render_key(output.output_hash, runtime)
    relative = f"campaigns/{plan.campaign_id}/editing/renders/{output.output_variant_id}/{key}/{output.filename}"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic master identity fixture")
    assert output.manifest.headline.font is not None
    receipt = RenderReceipt(campaign_id=plan.campaign_id, video_id=plan.video_id,
        output_variant_id=output.output_variant_id, plan_hash="0" * 64,
        manifest_hash=output.manifest_hash, output_hash=output.output_hash, render_key=key,
        runtime=runtime, inputs={"source": plan.source.sha256,
                                "headlineFont": output.manifest.headline.font.sha256},
        mix_policy="source_copy", path=relative, size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        probe=MediaProbe.model_validate(report_data()["probe"]))
    path.with_name("render.json").write_text(receipt.model_dump_json(by_alias=True, exclude_computed_fields=True))
    return plan, root, path, key


@pytest.mark.parametrize("field,value", [
    ("inputs", {"source": "0" * 64}), ("mixPolicy", "fixed_duck_mix"),
    ("outputVariantId", "foreign"), ("path", "elsewhere/master.mp4"),
    ("sizeBytes", 1), ("sha256", "0" * 64), ("renderKey", "0" * 64),
])
def test_verified_render_rejects_receipt_input_identity_size_and_hash_conflicts(
        artifact_case: tuple[Any, Path, Path, str], field: str, value: Any) -> None:
    from auraly_pipeline.editing.render_artifacts import open_verified_render
    plan, root, path, key = artifact_case
    receipt = path.with_name("render.json")
    data = json.loads(receipt.read_bytes())
    data[field] = value
    receipt.write_text(json.dumps(data))
    with pytest.raises(EditingError):
        open_verified_render(work_root=root, plan=plan,
                             output_variant_id=plan.outputs[0].output_variant_id, render_key=key)


def test_receipt_producer_plan_can_differ_and_render_key_uses_receipt_runtime(
        artifact_case: tuple[Any, Path, Path, str]) -> None:
    from auraly_pipeline.editing.render_artifacts import open_verified_render
    plan, root, path, key = artifact_case
    before = path.with_name("render.json").read_bytes()
    artifact = open_verified_render(work_root=root, plan=plan,
                                   output_variant_id=plan.outputs[0].output_variant_id, render_key=key)
    with artifact.stream:
        assert artifact.receipt.plan_hash != plan.plan_hash
        assert artifact.stream.read() == b"synthetic master identity fixture"
    assert artifact.receipt_sha256 == hashlib.sha256(before).hexdigest()
    assert path.with_name("render.json").read_bytes() == before


def test_validation_failure_closes_open_handle(artifact_case: tuple[Any, Path, Path, str],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    from auraly_pipeline.editing.render_artifacts import open_verified_render
    plan, root, _, key = artifact_case
    streams: list[Any] = []

    def bad_digest(stream: Any, _: str) -> Any:
        streams.append(stream)
        return hashlib.sha256(b"wrong")

    monkeypatch.setattr(hashlib, "file_digest", bad_digest)
    with pytest.raises(EditingError):
        open_verified_render(work_root=root, plan=plan,
                             output_variant_id=plan.outputs[0].output_variant_id, render_key=key)
    assert len(streams) == 1 and streams[0].closed
