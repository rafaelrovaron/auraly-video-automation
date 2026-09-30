from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

from auraly_pipeline.campaigns.persistence import create_sqlite_engine
from auraly_pipeline.cli import app
from auraly_pipeline.voices.domain import VoiceMaster
from auraly_pipeline.voices.service import VoiceMasterReviewError, VoiceMasterService
from tests.test_campaign_domain import valid_campaign_data
from tests.test_voice_external_migration import config_for
from tests import test_voice_external_import as imports


REASON = "Audio accepted for this technical test."


def test_voice_model_approval_constraint_is_valid_sql() -> None:
    from typing import cast
    from sqlalchemy import Table, create_engine
    from auraly_pipeline.voices.db_models import VoiceMasterRow

    engine = create_engine("sqlite:///:memory:")
    try:
        cast(Table, VoiceMasterRow.__table__).create(engine)
    finally:
        engine.dispose()


@pytest.fixture
def review_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[VoiceMasterService, VoiceMaster, Path]]:
    data = valid_campaign_data()
    data["copyMaster"].update(
        hook="A very wealthy person",
        body="has been secretly watching you",
        cta="and I'm about to give you the first letter of their name.",
    )
    monkeypatch.setattr(imports, "valid_campaign_data", lambda: data)
    importer, voices, source, request = imports.setup_import(
        tmp_path,
        transcript="very wealthy person has been secretly watching you and I'm about to give you the first letter of their name",
    )
    try:
        submission = importer.import_audio(request, source=source)
        job = importer.worker_once("review-test", campaign_id=request.campaign_id)
        assert job is not None and job.status == "completed"
        before = voices.get(submission.voice_master.voice_master_id)
        assert before.transcript_match_status == "review_required"
        yield voices, before, source.parent / "work"
    finally:
        importer.close()
        voices.close()


def test_imported_transcript_review_approval_preserves_evidence(
    review_voice, tmp_path: Path
) -> None:
    voices, before, root = review_voice
    paths = [before.processed_audio_path, before.transcript_path, before.manifest_path]
    originals = [(root / path).read_bytes() for path in paths]
    with pytest.raises(VoiceMasterReviewError):
        voices.approve(before.voice_master_id, approved_by="rafael")
    approved = voices.approve(
        before.voice_master_id, approved_by="rafael", approval_review_reason=REASON
    )
    assert approved.status == "approved"
    assert approved.approval_review_reason == REASON
    assert approved.approved_by == "rafael" and approved.approved_at is not None
    for field in (
        "transcript_match_status",
        "transcript_match_score",
        "qc_findings",
        "processed_sha256",
        "transcript_sha256",
        "manifest_sha256",
    ):
        assert getattr(approved, field) == getattr(before, field)
    assert [(root / path).read_bytes() for path in paths] == originals
    restarted = VoiceMasterService.for_database(tmp_path / "test.db", work_root=root)
    try:
        assert restarted.get(before.voice_master_id).approval_review_reason == REASON
        assert (
            restarted.approved_for_campaign(before.campaign_id).voice_master_id
            == before.voice_master_id
        )
    finally:
        restarted.close()
    engine = create_sqlite_engine(tmp_path / "test.db")
    try:
        for update in ("approval_review_reason='changed'", "qc_findings_json='[]'"):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(f"UPDATE voice_masters SET {update}"))
    finally:
        engine.dispose()
    with pytest.raises(RuntimeError, match="review"):
        command.downgrade(config_for(tmp_path / "test.db"), "0009_external_voice_import")


@pytest.mark.parametrize(
    "change",
    [
        {"transcriptMatchStatus": "mismatched"},
        {"headlineSpoken": True},
        {"qcFindings": ["Another QC failure."]},
        {"provider": "elevenlabs", "voiceId": "voice", "modelId": "model"},
    ],
)
def test_review_reason_cannot_approve_other_failures(
    review_voice, change: dict[str, object]
) -> None:
    _, before, _ = review_voice
    data = before.model_dump(by_alias=True)
    data.update(
        status="approved",
        approvedBy="rafael",
        approvedAt=datetime.now(UTC),
        approvalReviewReason=REASON,
    )
    data.update(change)
    with pytest.raises(ValidationError):
        VoiceMaster.model_validate(data)


@pytest.mark.parametrize("reason", ["", "   ", "secret=value", "x" * 513, "two\nlines"])
def test_invalid_review_reason_never_unlocks_approval(review_voice, reason: str) -> None:
    voices, before, _ = review_voice
    with pytest.raises((ValueError, VoiceMasterReviewError)):
        voices.approve(before.voice_master_id, approved_by="rafael", approval_review_reason=reason)
    assert voices.get(before.voice_master_id).status == "review_required"


@pytest.mark.parametrize(
    "change",
    [
        "transcript_match_status='mismatched'",
        "headline_spoken=1",
        "headline_spoken=NULL",
        "qc_findings_json='[\"Another QC failure.\"]'",
        "manifest_sha256=NULL",
    ],
)
def test_sql_and_service_review_gate_reject_failures(
    review_voice, tmp_path: Path, change: str
) -> None:
    voices, before, _ = review_voice
    engine = create_sqlite_engine(tmp_path / "test.db")
    try:
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE voice_masters SET {change}"))
        with pytest.raises(VoiceMasterReviewError):
            voices.approve(
                before.voice_master_id, approved_by="rafael", approval_review_reason=REASON
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE voice_masters SET status='approved', approved_by='rafael', approved_at=:now, approval_review_reason=:reason"
                ),
                {"now": datetime.now(UTC), "reason": REASON},
            )
    finally:
        engine.dispose()


def test_review_reason_does_not_bypass_artifact_hashes(review_voice) -> None:
    voices, before, root = review_voice
    (root / before.processed_audio_path).write_bytes(b"tampered")
    with pytest.raises(VoiceMasterReviewError):
        voices.approve(before.voice_master_id, approved_by="rafael", approval_review_reason=REASON)


def test_cli_records_explicit_review_reason(
    review_voice, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import auraly_pipeline.cli as cli

    voices, before, _ = review_voice
    monkeypatch.setattr(cli, "_voice_service", lambda database: voices)
    result = CliRunner().invoke(
        app,
        [
            "voice",
            "approve",
            before.voice_master_id,
            "--approved-by",
            "rafael",
            "--review-reason",
            REASON,
            "--database",
            str(tmp_path / "test.db"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(result.stdout)["voiceMaster"]["approvalReviewReason"] == REASON
