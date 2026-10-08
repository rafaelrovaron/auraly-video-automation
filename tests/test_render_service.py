from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from auraly_pipeline.editing.domain import EditingError
from auraly_pipeline.editing.render_service import RenderService
from auraly_pipeline.editing import render_service
from auraly_pipeline.probe import MediaProbe
from tests.editing_helpers import file_sha
from tests.render_helpers import make_render_plan, publish_test_plan, refresh_plan


def test_three_headlines_publish_distinct_masters(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    before = {p.name: file_sha(p) for p in (tmp_path / "source.mp4", tmp_path / "font.ttf")}
    service = RenderService(project_root=tmp_path, work_root=work)
    result = service.render(
        plan.campaign_id, plan.video_id, plan.plan_hash)
    assert [o.status for o in result.outputs] == ["rendered"] * 3, result.model_dump_json(by_alias=True)
    paths = [service.work_root / o.path for o in result.outputs if o.path]
    assert len({file_sha(p) for p in paths}) == 3
    for path in paths:
        receipt = json.loads(path.with_name("render.json").read_text())
        assert receipt["fullDecodePassed"] is True and receipt["sha256"] == file_sha(path)
    assert {p.name: file_sha(p) for p in (tmp_path / "source.mp4", tmp_path / "font.ttf")} == before


def test_replay_reuses_without_encode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    service = RenderService(project_root=tmp_path, work_root=work)
    first = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    def forbidden(*args: object, **kwargs: object) -> MediaProbe:
        raise AssertionError("replay must not encode")
    monkeypatch.setattr(render_service, "encode_master", forbidden)
    second = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert [o.status for o in second.outputs] == ["reused"] * 3
    assert [o.path for o in first.outputs] == [o.path for o in second.outputs]


def test_other_plan_reuses_producer_receipt(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    service = RenderService(project_root=tmp_path, work_root=work)
    original_hash = plan.plan_hash
    first = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert not first.has_failures
    receipt_paths = [service.work_root / o.path for o in first.outputs if o.path]
    before = {p: p.with_name("render.json").read_bytes() for p in receipt_paths}
    plan.outputs[0].label = "A new UI label"
    refresh_plan(plan)
    assert plan.plan_hash != original_hash
    publish_test_plan(plan, work)
    second = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert [o.status for o in second.outputs] == ["reused"] * 3
    assert all(p.with_name("render.json").read_bytes() == data for p, data in before.items())


@pytest.mark.parametrize("orphan", [False, True])
def test_corrupt_or_orphan_output_never_overwrites(tmp_path: Path, orphan: bool) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    service = RenderService(project_root=tmp_path, work_root=work)
    first = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    path = first.outputs[0].path
    assert path is not None
    master = service.work_root / path
    if orphan:
        master.with_name("render.json").unlink()
    else:
        master.write_bytes(b"corrupt")
    before = master.read_bytes()
    replay = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert replay.outputs[0].status == "failed"
    assert [o.status for o in replay.outputs[1:]] == ["reused", "reused"]
    assert master.read_bytes() == before


def test_changed_input_prevents_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    encode = render_service.encode_master
    def change(*args: Any, **kwargs: Any) -> MediaProbe:
        probe = encode(*args, **kwargs)
        (tmp_path / "font.ttf").write_bytes(b"changed during encode")
        return probe
    monkeypatch.setattr(render_service, "encode_master", change)
    service = RenderService(project_root=tmp_path, work_root=work)
    result = service.render(
        plan.campaign_id, plan.video_id, plan.plan_hash)
    assert result.has_failures
    assert not list(service.work_root.rglob("render.json"))


def test_variant_failure_preserves_siblings(tmp_path: Path) -> None:
    plan = make_render_plan(tmp_path)
    m = plan.outputs[0].manifest
    m.captions.enabled, m.captions.font = True, m.headline.font
    refresh_plan(plan)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    result = RenderService(project_root=tmp_path, work_root=work).render(
        plan.campaign_id, plan.video_id, plan.plan_hash)
    assert [o.status for o in result.outputs] == ["failed", "rendered", "rendered"]
    assert result.outputs[0].error is not None
    assert result.outputs[0].error.field == "captionInput"


def test_unsafe_work_root_link_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("local Windows symlink privilege unavailable")
    with pytest.raises(EditingError):
        RenderService(project_root=tmp_path, work_root=link)


def test_encode_failure_cleans_staging_and_preserves_siblings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    encode = render_service.encode_master
    count = 0
    def fail_first(*args: Any, **kwargs: Any) -> MediaProbe:
        nonlocal count
        count += 1
        if count == 1:
            raise EditingError("runtime", "local encoder timed out")
        return encode(*args, **kwargs)
    monkeypatch.setattr(render_service, "encode_master", fail_first)
    service = RenderService(project_root=tmp_path, work_root=work)
    result = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert [o.status for o in result.outputs] == ["failed", "rendered", "rendered"]
    assert not list(service.work_root.glob(".render-*"))


def test_publication_conflict_preserves_existing_master(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan = make_render_plan(tmp_path)
    work = tmp_path / "work"
    publish_test_plan(plan, work)
    link = render_service.os.link
    def race(source: Path, target: Path) -> None:
        if target.suffix == ".mp4":
            target.write_bytes(b"other publisher")
        link(source, target)
    monkeypatch.setattr(render_service.os, "link", race)
    service = RenderService(project_root=tmp_path, work_root=work)
    result = service.render(plan.campaign_id, plan.video_id, plan.plan_hash)
    assert all(o.status == "failed" for o in result.outputs)
    assert not list(service.work_root.rglob("render.json"))
    assert all(p.read_bytes() == b"other publisher" for p in service.work_root.rglob("*.mp4"))
