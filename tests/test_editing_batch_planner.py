from __future__ import annotations

from typing import Any

import pytest

from auraly_pipeline.editing.batch_domain import CaptionTimingInput, EditBatchPlan, EditBatchRequest
from auraly_pipeline.editing.batch_planner import build_batch_plan, variant_requests, verify_batch_plan
from auraly_pipeline.editing.domain import EditProfile, EditingError
from auraly_pipeline.editing.resolver import content_hash, profile_hash, resolve_manifest
from tests.editing_batch_helpers import batch_data, batch_inputs, timing_data
from tests.editing_helpers import profile_data


def plan_for(data: dict[str, Any] | None = None, timing: dict[str, Any] | None = None) -> EditBatchPlan:
    profile = EditProfile.model_validate(profile_data())
    data = data or batch_data()
    data["profileRef"]["hash"] = profile_hash(profile)
    inputs = batch_inputs()
    if timing is not None:
        data["timingRef"] = {"path": "timing.json", "sha256": "f" * 64}
    request = EditBatchRequest.model_validate(data)
    manifests = [resolve_manifest(profile, r) for _, r in variant_requests(request, inputs)]
    return build_batch_plan(request, inputs, manifests,
        timing=CaptionTimingInput.model_validate(timing) if timing is not None else None)


def valid_timing() -> dict[str, Any]:
    data = timing_data()
    data["copyHash"] = batch_inputs().copy.sha256
    data["cues"] = [
        {"startSec": 0, "endSec": 1, "tokenStart": 0, "tokenEnd": 2},
        {"startSec": 2, "endSec": 4, "tokenStart": 2, "tokenEnd": 6},
    ]
    return data


def test_three_headlines_reuse_upstream() -> None:
    plan = plan_for()
    assert plan.output_count == 3
    assert [o.manifest.headline.text for o in plan.outputs] == ["Visual a", "Visual b", "Visual c"]
    assert all(o.manifest.source == batch_inputs().source for o in plan.outputs)
    assert len({o.filename for o in plan.outputs}) == 3
    verify_batch_plan(plan)


def test_order_and_replay() -> None:
    data = batch_data()
    data["variants"].reverse()
    assert plan_for(data) == plan_for()


def test_headline_is_not_caption_text() -> None:
    caption = plan_for().caption_input
    assert caption.text == "Visual hook\n\nBody words.\n\nClick now!"
    assert caption.timing_status == "missing" and caption.cues == []
    assert all(o.caption_state == "disabled" for o in plan_for().outputs)


def test_caption_timing_coverage_and_identity() -> None:
    caption = plan_for(timing=valid_timing()).caption_input
    assert [c.text for c in caption.cues] == ["Visual hook", "Body words. Click now!"]
    assert caption.origin == "manual" and caption.accepted_by == "tester"


def test_enabled_caption_states() -> None:
    data = batch_data()
    data["video"] = {"captions": {"enabled": True,
                                 "font": {"path": "font.ttf", "sha256": "a" * 64}}}
    missing = plan_for(data)
    assert all(o.caption_state == "timing_missing" for o in missing.outputs)
    provided = plan_for(data, valid_timing())
    assert all(o.caption_state == "timing_provided" for o in provided.outputs)


@pytest.mark.parametrize("case", ["gap", "overlap", "duplicate", "duration", "copy", "source", "audio"])
def test_invalid_timing_fails(case: str) -> None:
    data = valid_timing()
    if case == "gap":
        data["cues"][1]["tokenStart"] = 3
    elif case == "overlap":
        data["cues"][1]["startSec"] = .5
    elif case == "duplicate":
        data["cues"][1]["tokenStart"] = 0
    elif case == "duration":
        data["cues"][1]["endSec"] = 8
    else:
        data[{"copy": "copyHash", "source": "sourceSha256", "audio": "processedAudioSha256"}[case]] = "0" * 64
    with pytest.raises(ValueError):
        plan_for(timing=data)


def test_timing_and_label_hash_boundaries() -> None:
    timing = valid_timing()
    first = plan_for(timing=timing)
    timing["cues"][1]["startSec"] = 3
    second = plan_for(timing=timing)
    assert first.outputs[0].manifest == second.outputs[0].manifest
    assert first.outputs[0].output_hash != second.outputs[0].output_hash
    data = batch_data()
    data["variants"][0]["label"] = "New label"
    label = plan_for(data)
    base = plan_for()
    assert label.plan_hash != base.plan_hash
    assert label.outputs[0].filename == base.outputs[0].filename


@pytest.mark.parametrize("case", ["count", "id", "filename", "source", "cue", "state", "manifest"])
def test_rehashed_semantically_invalid_plan(case: str) -> None:
    data = plan_for(timing=valid_timing()).model_dump(mode="json", by_alias=True)
    if case == "count":
        data["outputCount"] = 1
    elif case == "id":
        data["outputs"][0]["outputVariantId"] = "wrong"
    elif case == "filename":
        data["outputs"][0]["filename"] = "../bad.mp4"
    elif case == "source":
        data["source"]["sha256"] = "0" * 64
    elif case == "cue":
        data["captionInput"]["cues"][0]["text"] = "Injected headline"
    elif case == "state":
        data["outputs"][0]["captionState"] = "timing_provided"
    else:
        data["outputs"][0]["manifestHash"] = "0" * 64
    data["planHash"] = content_hash({k: v for k, v in data.items() if k != "planHash"})
    with pytest.raises((ValueError, EditingError)):
        verify_batch_plan(EditBatchPlan.model_validate(data))
