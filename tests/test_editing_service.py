from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from auraly_pipeline.editing.domain import EditProfile, EditResolveRequest, EditingError
from auraly_pipeline.editing.resolver import profile_hash
from auraly_pipeline.editing.service import EditingService
from tests.editing_helpers import file_sha, make_source, profile_data, request_data


def setup(root: Path) -> tuple[EditingService, EditResolveRequest]:
    source = make_source(root)
    profile = EditProfile.model_validate(profile_data())
    service = EditingService(project_root=root, work_root=root / "work")
    service.create_profile(profile)
    data = request_data(profile_hash(profile))
    data["source"].update(sha256=file_sha(source), durationSec=1)
    return service, EditResolveRequest.model_validate(data)


def test_profile_version_and_conflict(tmp_path: Path) -> None:
    service = EditingService(project_root=tmp_path, work_root=tmp_path / "work")
    profile = EditProfile.model_validate(profile_data())
    first = service.create_profile(profile)
    original = first.read_bytes()
    assert service.create_profile(profile) == first
    data = profile_data()
    data.update(version=2, name="New")
    second = EditProfile.model_validate(data)
    service.create_profile_version("plain", 1, second)
    assert first.read_bytes() == original
    assert [p.version for p in service.list_profiles()] == [1, 2]
    assert service.get_profile("plain", 1) == profile
    data.update(version=1)
    with pytest.raises(EditingError):
        service.create_profile(EditProfile.model_validate(data))
    assert first.read_bytes() == original


def test_resolve_replay_preserves_source(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    original = (tmp_path / "source.mp4").read_bytes()
    mtime = (tmp_path / "source.mp4").stat().st_mtime_ns
    first = service.resolve(request)
    saved = list((tmp_path / "work").rglob("manifest.json"))[0]
    content = saved.read_bytes()
    assert service.resolve(request).manifest_hash == first.manifest_hash
    assert saved.read_bytes() == content
    assert service.get_manifest("campaign-1", "video-1", "a", first.manifest_hash) == first
    assert (tmp_path / "source.mp4").read_bytes() == original
    assert (tmp_path / "source.mp4").stat().st_mtime_ns == mtime


def test_dry_run_does_not_publish(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    service.resolve(request, persist=False)
    assert not list((tmp_path / "work").rglob("manifest.json"))


def test_tampered_profile_and_manifest_fail(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    result = service.resolve(request)
    saved = list((tmp_path / "work").rglob("manifest.json"))[0]
    payload = json.loads(saved.read_text())
    payload["headline"]["text"] = "Tampered"
    saved.write_text(json.dumps(payload))
    with pytest.raises(EditingError):
        service.resolve(request)
    with pytest.raises(EditingError):
        service.get_manifest("campaign-1", "video-1", "a", result.manifest_hash)
    profile_path = list((tmp_path / "work").rglob("profile.json"))[0]
    payload = json.loads(profile_path.read_text())
    payload["profile"]["name"] = "Changed"
    profile_path.write_text(json.dumps(payload))
    with pytest.raises(EditingError):
        service.get_profile("plain", 1)
    with pytest.raises(EditingError):
        service.list_profiles()


def test_incomplete_publication_is_not_reused(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    service.resolve(request)
    saved = list((tmp_path / "work").rglob("manifest.json"))[0]
    saved.write_text("{")
    with pytest.raises(EditingError):
        service.resolve(request)
    assert saved.read_text() == "{"


def test_wrong_source_duration_fails_before_publication(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    request.source.duration_sec = 2
    with pytest.raises(EditingError) as exc:
        service.resolve(request)
    assert exc.value.field == "source.durationSec"
    assert not list((tmp_path / "work").rglob("manifest.json"))


@pytest.mark.parametrize("exists", [False, True])
def test_font_has_no_system_fallback(tmp_path: Path, exists: bool) -> None:
    service, request = setup(tmp_path)
    if exists:
        (tmp_path / "font.ttf").write_bytes(b"not a font")
    data = request.model_dump(mode="json", by_alias=True)
    data["video"] = {"headline": {"enabled": True, "font": {
        "path": "font.ttf", "sha256": file_sha(tmp_path / "font.ttf") if exists else "c" * 64,
    }}}
    with pytest.raises(EditingError):
        service.resolve(EditResolveRequest.model_validate(data))


def test_music_needs_explicit_acceptance(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    data = request.model_dump(mode="json", by_alias=True)
    data["video"] = {"music": {"enabled": True, "asset": {
        "path": "source.mp4", "sha256": request.source.sha256,
    }}}
    with pytest.raises(EditingError) as exc:
        service.resolve(EditResolveRequest.model_validate(data))
    assert exc.value.field == "musicAccepted"
    data["musicAccepted"] = True
    assert service.resolve(EditResolveRequest.model_validate(data)).music.enabled is True


def test_rejects_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlinks")
    with pytest.raises(EditingError):
        EditingService(project_root=tmp_path, work_root=link / "work")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction")
def test_rejects_junction_ancestor(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)
    with pytest.raises(EditingError):
        EditingService(project_root=tmp_path, work_root=link / "work")


def test_changed_source_hash_is_rejected(tmp_path: Path) -> None:
    service, request = setup(tmp_path)
    (tmp_path / "source.mp4").write_bytes(b"tampered")
    with pytest.raises(EditingError) as exc:
        service.resolve(request)
    assert exc.value.field == "source.sha256"
