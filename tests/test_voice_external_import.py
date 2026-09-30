from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
import subprocess
from typing import Any

import pytest
from sqlalchemy import text

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.voices.domain import VoiceImportRequest
from auraly_pipeline.voices.service import VoiceMasterService, VoiceMasterReviewError
from tests.test_campaign_domain import valid_campaign_data


class Transcript:
    def __init__(self, text: str | None) -> None:
        self.text = text

    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.is_file()
        if self.text is None:
            raise ImportError("private-runtime-path")
        return self.text


def make_audio(path: Path, *, no_xing: bool = False) -> None:
    argv = [
        "ffmpeg",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=mono:d=0.75",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=880:duration=1",
        "-filter_complex",
        "[0:a][1:a][2:a]concat=n=3:v=0:a=1",
        "-ac",
        "1",
    ]
    if no_xing:
        argv.extend(["-write_xing", "0", "-b:a", "128k"])
    subprocess.run([*argv, str(path)], capture_output=True, check=True)


def setup_import(
    tmp_path: Path, *, transcript: str | None = "matched", no_xing: bool = False
) -> tuple[Any, VoiceMasterService, Path, VoiceImportRequest]:
    module = importlib.import_module("auraly_pipeline.voices.import_audio")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    source = project / ("hook.mp3" if no_xing else "hook.wav")
    make_audio(source, no_xing=no_xing)
    database = tmp_path / "test.db"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(valid_campaign_data()))
    campaigns.close()
    expected = campaign.copy_masters[0].spoken_text
    service = module.VoiceImportService.for_database(
        database,
        project_root=project,
        work_root=project / "work",
        transcriber=Transcript(expected if transcript == "matched" else transcript),
    )
    voices = VoiceMasterService.for_database(database, work_root=project / "work")
    return service, voices, source, VoiceImportRequest(campaign_id=campaign.campaign_id)


@pytest.mark.parametrize("no_xing", [False, True])
def test_external_voice_becomes_reviewable_without_regeneration(
    tmp_path: Path, no_xing: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auraly_pipeline.voices.provider import ElevenLabsAdapter

    def no_paid_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected paid voice generation")

    monkeypatch.setattr(ElevenLabsAdapter, "generate_speech", no_paid_call)
    service, voices, source, request = setup_import(tmp_path, no_xing=no_xing)
    original = source.read_bytes()
    try:
        submission = service.import_audio(request, source=source)
        assert submission.voice_master.status == "pending"
        job = service.worker_once("import-test", campaign_id=request.campaign_id)
        assert job is not None and job.status == "completed"
        voice = voices.get(submission.voice_master.voice_master_id)
        assert (voice.provider, voice.status, voice.sample_rate, voice.channels) == (
            "imported",
            "review_required",
            48000,
            1,
        )
        assert voice.provider_request_id is None
        assert voice.duration_seconds is not None and voice.duration_seconds > 2.5
        assert (
            voice.long_internal_pauses
            and voice.long_internal_pauses[0][1] - voice.long_internal_pauses[0][0] > 0.6
        )
        assert source.read_bytes() == original
        approved = voices.approve(voice.voice_master_id, approved_by="rafael")
        assert approved.status == "approved"
        replay = service.import_audio(request, source=source)
        assert replay.voice_master.voice_master_id == voice.voice_master_id
        assert replay.job.job_id == submission.job.job_id
    finally:
        service.close()
        voices.close()


@pytest.mark.parametrize("transcript", [None, "wrong words", "HE RETURNS WHEN YOU WALK AWAY"])
def test_external_voice_failure_or_mismatch_never_bypasses_approval(
    tmp_path: Path, transcript: str | None
) -> None:
    service, voices, source, request = setup_import(tmp_path, transcript=transcript)
    try:
        submission = service.import_audio(request, source=source)
        job = service.worker_once("import-test", campaign_id=request.campaign_id)
        assert job is not None
        voice = voices.get(submission.voice_master.voice_master_id)
        assert voice.status == ("failed" if transcript is None else "review_required")
        with pytest.raises(VoiceMasterReviewError):
            voices.approve(voice.voice_master_id, approved_by="rafael")
        assert source.exists()
        assert voice.raw_audio_path and (source.parent / "work" / voice.raw_audio_path).exists()
        assert "private-runtime-path" not in job.model_dump_json()
    finally:
        service.close()
        voices.close()


@pytest.mark.parametrize("kind", ["outside", "empty", "invalid", "oversized", "symlink"])
def test_import_refuses_unsafe_sources_before_submission(tmp_path: Path, kind: str) -> None:
    service, voices, source, request = setup_import(tmp_path)
    try:
        if kind == "outside":
            source = tmp_path / "outside.wav"
            make_audio(source)
        elif kind == "empty":
            source.write_bytes(b"")
        elif kind == "invalid":
            source.write_bytes(b"not an audio")
        elif kind == "oversized":
            with source.open("r+b") as stream:
                stream.truncate(100 * 1024 * 1024 + 1)
        else:
            link = source.with_name("link.wav")
            try:
                link.symlink_to(source)
            except OSError:
                pytest.skip("symlink creation unavailable")
            source = link
        with pytest.raises(Exception):
            service.import_audio(request, source=source)
        assert voices.list(campaign_id=request.campaign_id) == []
    finally:
        service.close()
        voices.close()


def test_concurrent_identical_imports_share_one_voice_and_job(tmp_path: Path) -> None:
    service, voices, source, request = setup_import(tmp_path)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(lambda _: service.import_audio(request, source=source), range(2))
            )
        assert len({result.job.job_id for result in results}) == 1
        assert len(voices.list(campaign_id=request.campaign_id)) == 1
    finally:
        service.close()
        voices.close()


def test_modified_raw_artifact_is_refused_on_replay_and_processing(tmp_path: Path) -> None:
    service, voices, source, request = setup_import(tmp_path)
    try:
        result = service.import_audio(request, source=source)
        raw = source.parent / "work" / result.voice_master.raw_audio_path
        raw.write_bytes(b"changed")
        with pytest.raises(Exception):
            service.import_audio(request, source=source)
        job = service.worker_once("import-test", campaign_id=request.campaign_id)
        assert job is not None and job.status == "failed"
    finally:
        service.close()
        voices.close()


def test_active_import_blocks_different_source_without_replacing_history(tmp_path: Path) -> None:
    service, voices, source, request = setup_import(tmp_path)
    try:
        result = service.import_audio(request, source=source)
        source.write_bytes(source.read_bytes() + b"extra")
        with pytest.raises(Exception):
            service.import_audio(request, source=source)
        assert voices.get(result.voice_master.voice_master_id).status == "pending"
        engine = create_sqlite_engine(tmp_path / "test.db")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM voice_masters")) == 1
        engine.dispose()
    finally:
        service.close()
        voices.close()


def test_import_detects_source_change_during_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auraly_pipeline.voices import import_audio

    service, voices, source, request = setup_import(tmp_path)
    original_probe = import_audio._probe

    def changed_probe(path: Path) -> Any:
        result = original_probe(path)
        path.write_bytes(path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(import_audio, "_probe", changed_probe)
    try:
        with pytest.raises(import_audio.VoiceImportError):
            service.import_audio(request, source=source)
        assert voices.list(campaign_id=request.campaign_id) == []
    finally:
        service.close()
        voices.close()


def test_decode_refuses_existing_output_and_invalid_container(tmp_path: Path) -> None:
    from auraly_pipeline.voices.audio import decode_external_audio

    source = tmp_path / "source.wav"
    make_audio(source)
    output = tmp_path / "output.wav"
    output.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        decode_external_audio(source, output)
    assert output.read_bytes() == b"keep"
    output.unlink()
    source.write_bytes(b"invalid")
    with pytest.raises(Exception):
        decode_external_audio(source, output)
    assert not output.exists()


def test_import_is_local_ready_only_after_processing(tmp_path: Path) -> None:
    service, voices, source, request = setup_import(tmp_path)
    engine = create_sqlite_engine(tmp_path / "test.db")
    try:
        service.import_audio(request, source=source)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT provider_state FROM voice_masters"))
                == "not_dispatched"
            )
        service.worker_once("import-test", campaign_id=request.campaign_id)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT provider_state FROM voice_masters")) == "local_ready"
            )
    finally:
        engine.dispose()
        service.close()
        voices.close()


def test_import_preserves_preexisting_processing_partial(tmp_path: Path) -> None:
    service, voices, source, request = setup_import(tmp_path)
    try:
        submitted = service.import_audio(request, source=source)
        folder = (
            source.parent
            / "work"
            / "campaigns"
            / request.campaign_id
            / "voice"
            / submitted.voice_master.voice_master_id
        )
        partial = folder / "processed" / "voice-master.wav.partial"
        partial.parent.mkdir()
        partial.write_bytes(b"existing-user-file")
        job = service.worker_once("partial-test", campaign_id=request.campaign_id)
        assert partial.read_bytes() == b"existing-user-file"
        assert job is not None and job.status == "failed"
        assert voices.get(submitted.voice_master.voice_master_id).status == "failed"
    finally:
        service.close()
        voices.close()


def test_import_rejects_preexisting_voice_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID
    from auraly_pipeline.voices import import_audio

    service, voices, source, request = setup_import(tmp_path)
    voice_id = UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(import_audio, "uuid5", lambda namespace, name: voice_id)
    folder = source.parent / "work" / "campaigns" / request.campaign_id / "voice" / str(voice_id)
    partial = folder / "processed" / "voice-master.wav.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"keep-existing")
    engine = create_sqlite_engine(tmp_path / "test.db")
    try:
        with pytest.raises(Exception):
            service.import_audio(request, source=source)
        assert partial.read_bytes() == b"keep-existing"
        assert voices.list(campaign_id=request.campaign_id) == []
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM jobs")) == 0
    finally:
        engine.dispose()
        service.close()
        voices.close()
