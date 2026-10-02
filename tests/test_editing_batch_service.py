from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from auraly_pipeline.editing.batch_domain import EditBatchRequest
from auraly_pipeline.editing.batch_service import EditBatchService
from auraly_pipeline.editing.domain import EditProfile, EditingError
from auraly_pipeline.editing.resolver import content_hash, profile_hash
from auraly_pipeline.editing.service import EditingService
from auraly_pipeline.heygen.fake_provider import FakeHeyGenProvider
from auraly_pipeline.heygen.video_domain import HeyGenVideoConfig
from auraly_pipeline.heygen.video_service import HeyGenVideoService
from tests.editing_batch_helpers import batch_data
from tests.editing_helpers import profile_data, file_sha
from tests.heygen_video_support import ready_video_campaign

pytest_plugins = ["tests.test_heygen_video_media"]


def setup_batch(root: Path, mp4: bytes, db_name: str = "test.db") -> tuple[Path, Path, EditBatchRequest]:
    database, work = root / db_name, root / "work"
    provider = FakeHeyGenProvider()
    ready_video_campaign(database, work, provider, canonical_copy=True)
    service = HeyGenVideoService.for_database(
        database, work_root=work, provider=provider,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=mp4))),
    )
    service.submit_videos("campaign-one", HeyGenVideoConfig(resolution="720p"),
                          max_paid_renders=3, approved_by="tester")
    renders = service.run_videos("campaign-one").renders
    service.close()
    profile = EditProfile.model_validate(profile_data())
    EditingService(project_root=root, work_root=work).create_profile(profile)
    data = batch_data()
    data["renderId"] = renders[0].render_id
    data["profileRef"]["hash"] = profile_hash(profile)
    return database, work, EditBatchRequest.model_validate(data)


def database_dump(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        return list(connection.iterdump())


def test_readonly_plan_and_replay(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    original = database_dump(db)
    media = {p: (file_sha(p), p.stat().st_mtime_ns) for p in work.rglob("*")
             if p.suffix in {".mp4", ".wav"}}
    service = EditBatchService(project_root=tmp_path, work_root=work)
    first = service.plan(request, database_path=db)
    assert service.plan(request, database_path=db) == first
    assert service.get_plan(request.campaign_id, request.video_id, first.plan_hash) == first
    assert first.caption_input.text == "hook\n\nbody\n\ncta"
    assert original == database_dump(db)
    assert all((file_sha(p), p.stat().st_mtime_ns) == facts for p, facts in media.items())
    assert not list(work.rglob("manifest.json"))


def test_pinned_copy_not_latest(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    with sqlite3.connect(db) as connection:
        connection.execute("""
            INSERT INTO copy_masters SELECT '10000000-0000-4000-8000-000000000009',
            campaign_id,2,source_text,headline,hook,body,cta,spoken_text,sha256,
            approval_state,approved_by,approved_at,created_at,updated_at FROM copy_masters
        """)
    plan = EditBatchService(project_root=tmp_path, work_root=work).plan(
        request, database_path=db, persist=False)
    assert plan.copy_ref.version == 1


def test_db_uri_special_characters(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4, "a # space.db")
    plan = EditBatchService(project_root=tmp_path, work_root=work).plan(request, database_path=db)
    assert plan.output_count == 3
    assert sorted(p.name for p in tmp_path.glob("*.db")) == ["a # space.db"]


@pytest.mark.parametrize("case", ["missing", "schema", "not_ready", "campaign", "voice", "hash", "source", "wav"])
def test_missing_or_wrong_upstream(tmp_path: Path, mp4: bytes, case: str) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    if case == "missing":
        db = tmp_path / "absent.db"
    elif case == "schema":
        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()
    elif case == "source":
        with sqlite3.connect(db) as connection:
            row = connection.execute("SELECT source_json FROM heygen_renders WHERE id=?",
                                     (request.render_id,)).fetchone()
        (work / json.loads(row[0])["path"]).write_bytes(b"bad")
    elif case == "wav":
        next(work.rglob("voice.wav")).write_bytes(b"bad")
    else:
        with sqlite3.connect(db) as connection:
            if case == "not_ready":
                connection.execute("UPDATE heygen_renders SET status='failed'")
            elif case == "campaign":
                connection.execute("UPDATE heygen_renders SET campaign_id='wrong'")
            elif case == "voice":
                connection.row_factory = sqlite3.Row
                row = dict(connection.execute("SELECT * FROM voice_masters").fetchone())
                row.update(id="40000000-0000-4000-8000-000000000009",
                           status="pending", generation=2, logical_key="f" * 64, job_id=None)
                connection.execute(
                    f"INSERT INTO voice_masters ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                    tuple(row.values()),
                )
                connection.execute("UPDATE heygen_renders SET voice_master_id=?,item_json=json_set(item_json,'$.voice_master_id',?)",
                                   (row["id"], row["id"]))
            else:
                connection.execute("UPDATE heygen_renders SET item_json=json_set(item_json,'$.audio_sha256',?)", ("0" * 64,))
    with pytest.raises(EditingError):
        EditBatchService(project_root=tmp_path, work_root=work).plan(request, database_path=db)
    assert not list(work.rglob("plan.json"))
    if case == "missing":
        assert not db.exists()


def test_last_invalid_variant_publishes_nothing(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    data = request.model_dump(mode="json", by_alias=True)
    data["variants"][-1]["overrides"]["headline"]["endSec"] = 99
    with pytest.raises(EditingError):
        EditBatchService(project_root=tmp_path, work_root=work).plan(
            EditBatchRequest.model_validate(data), database_path=db)
    assert not list(work.rglob("plan.json")) and not list(work.rglob("manifest.json"))


def test_dry_run_does_not_create_editing_directory(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    before = set(work.rglob("*"))
    EditBatchService(project_root=tmp_path, work_root=work).plan(request, database_path=db, persist=False)
    assert set(work.rglob("*")) == before


@pytest.mark.parametrize("corruption", ["truncate", "hash", "rehashed"])
def test_corrupt_plan_not_overwritten(tmp_path: Path, mp4: bytes, corruption: str) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    service = EditBatchService(project_root=tmp_path, work_root=work)
    plan = service.plan(request, database_path=db)
    path = next(work.rglob("plan.json"))
    payload = plan.model_dump(mode="json", by_alias=True)
    payload["outputCount"] = 1
    if corruption == "rehashed":
        payload["planHash"] = content_hash({k: v for k, v in payload.items() if k != "planHash"})
    content = "{" if corruption == "truncate" else json.dumps(payload)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(EditingError):
        service.get_plan(request.campaign_id, request.video_id, plan.plan_hash)
    with pytest.raises(EditingError):
        service.plan(request, database_path=db)
    assert path.read_text(encoding="utf-8") == content


def test_timing_hash_and_bad_coverage_fail(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    service = EditBatchService(project_root=tmp_path, work_root=work)
    baseline = service.plan(request, database_path=db, persist=False)
    timing = tmp_path / "timing.json"
    timing.write_text(json.dumps({
        "sourceSha256": baseline.source.sha256, "copyMasterId": baseline.copy_ref.id,
        "copyHash": baseline.copy_ref.hash, "processedAudioSha256": baseline.voice_ref.hash,
        "origin": "manual", "acceptedBy": "tester", "timebase": "source_mp4",
        "cues": [{"startSec": 0, "endSec": .5, "tokenStart": 0, "tokenEnd": 2}],
    }), encoding="utf-8")
    data = request.model_dump(mode="json", by_alias=True)
    data["timingRef"] = {"path": "timing.json", "sha256": "0" * 64}
    with pytest.raises(EditingError):
        service.plan(EditBatchRequest.model_validate(data), database_path=db)
    data["timingRef"]["sha256"] = file_sha(timing)
    with pytest.raises(EditingError):
        service.plan(EditBatchRequest.model_validate(data), database_path=db)
    assert not list(work.rglob("plan.json"))


def test_database_link_is_rejected(tmp_path: Path, mp4: bytes) -> None:
    db, work, request = setup_batch(tmp_path, mp4)
    link = tmp_path / "link.db"
    try:
        link.symlink_to(db)
    except OSError:
        pytest.skip("OS does not permit symlinks")
    with pytest.raises(EditingError):
        EditBatchService(project_root=tmp_path, work_root=work).plan(request, database_path=link)


def test_database_missing_columns_is_sanitized(tmp_path: Path, mp4: bytes) -> None:
    _, work, request = setup_batch(tmp_path, mp4)
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE heygen_renders (id TEXT, status TEXT)")
        connection.execute("INSERT INTO heygen_renders VALUES (?, 'ready')", (request.render_id,))
    with pytest.raises(EditingError, match="cannot verify local campaign inputs"):
        EditBatchService(project_root=tmp_path, work_root=work).plan(request, database_path=db)
    assert not list(work.rglob("plan.json"))
