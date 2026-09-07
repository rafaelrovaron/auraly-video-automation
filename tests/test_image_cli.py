from __future__ import annotations

import json
import hashlib
from pathlib import Path

from sqlalchemy import select
from typer.testing import CliRunner
import pytest
from PIL import Image

from auraly_pipeline.campaigns.domain import CampaignCreate
from auraly_pipeline.campaigns.service import CampaignService
from auraly_pipeline.cli import app
from auraly_pipeline.images.db_models import FlowGenerationRunRow, ImageGenerationRow
from auraly_pipeline.images.domain import ImageGenerateRequest
from auraly_pipeline.images.service import ImageCandidateNotFoundError
from auraly_pipeline.images.service import ImageService
from auraly_pipeline.jobs.db_models import JobRow
from auraly_pipeline.jobs.service import JobService
from tests.test_campaign_domain import valid_campaign_data


runner = CliRunner()


def _database(tmp_path: Path) -> tuple[Path, Path, str, list[str]]:
    database = tmp_path / "state" / "auraly.db"
    work_root = tmp_path / "work"
    campaign_data = valid_campaign_data()
    campaign_data["campaignId"] = "image-cli"
    campaigns = CampaignService.for_database(database)
    campaign = campaigns.create_campaign(CampaignCreate.model_validate(campaign_data))
    campaigns.close()
    return (
        database,
        work_root,
        campaign.campaign_id,
        [scene.scene_variant_id for scene in campaign.scene_variants],
    )


def _generate_args(
    database: Path,
    work_root: Path,
    campaign_id: str,
    scene_variant_id: str,
    *,
    idempotency_key: str,
) -> list[str]:
    return [
        "image",
        "generate",
        campaign_id,
        "--scene-variant-id",
        scene_variant_id,
        "--idempotency-key",
        idempotency_key,
        "--prompt-snapshot",
        "A moonlit studio with cards on a velvet table",
        "--database",
        str(database),
        "--work-root",
        str(work_root),
    ]


def _complete_locally(database: Path, work_root: Path) -> None:
    service = JobService.for_database(database, work_root=work_root)
    worked = service.worker_once("image-cli-worker")
    service.close()
    assert worked is not None
    assert worked.status == "completed"


def _blocked_flow_generation(
    tmp_path: Path,
    *,
    ambiguous: bool = False,
) -> tuple[Path, Path, str]:
    database, work_root, campaign_id, scenes = _database(tmp_path)
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), color=(16, 32, 64)).save(reference)
    workspace_path = "fx/tools/flow/cli-recovery"
    service = ImageService.for_database(database, work_root=work_root)
    submission = service.generate(
        ImageGenerateRequest(
            campaign_id=campaign_id,
            scene_variant_id=scenes[0],
            idempotency_key="cli-flow-recovery",
            prompt_snapshot="PRIVATE PROMPT must not reach recovery JSON",
            reference_image_path="references/avatar.png",
            reference_image_sha256=hashlib.sha256(reference.read_bytes()).hexdigest(),
            executor="playwright_python",
            generation_contract_version="flow-generation-v1",
            provider_action_confirmed=True,
            provider_action_approved_by="operator-1",
            provider_workspace_path=workspace_path,
            provider_workspace_fingerprint=hashlib.sha256(
                workspace_path.encode()
            ).hexdigest(),
        )
    )
    with service._sessions() as session:
        job = session.get(JobRow, submission.job.job_id)
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id
                == submission.generation.image_generation_id
            )
        )
        generation = session.get(
            ImageGenerationRow,
            submission.generation.image_generation_id,
        )
        assert job is not None and run is not None and generation is not None
        job.status = "blocked"
        if ambiguous:
            run.stage = "ambiguous"
            run.dispatch_intent_at = run.created_at
            generation.provider_state = "blocked"
        session.commit()
    service.close()
    return database, work_root, submission.generation.image_generation_id


def test_image_generate_and_generation_get_emit_structured_json(tmp_path: Path) -> None:
    database, work_root, campaign_id, scenes = _database(tmp_path)
    generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-generate",
        ),
    )

    assert generated.exit_code == 0
    generated_payload = json.loads(generated.stdout)
    assert generated_payload["success"] is True
    generation_id = generated_payload["generation"]["imageGenerationId"]
    assert generated_payload["generation"]["jobId"] == generated_payload["job"]["jobId"]
    assert generated_payload["generation"]["sceneVariantId"] == scenes[0]

    fetched = runner.invoke(
        app,
        [
            "image",
            "generation",
            "get",
            generation_id,
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert fetched.exit_code == 0
    assert json.loads(fetched.stdout)["generation"]["imageGenerationId"] == generation_id

    listed = runner.invoke(
        app,
        [
            "image",
            "generation",
            "list",
            scenes[0],
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert listed.exit_code == 0
    assert [item["generationNumber"] for item in json.loads(listed.stdout)["generations"]] == [1]


def test_image_generate_cli_persists_explicit_flow_authorization_and_workspace(
    tmp_path: Path,
) -> None:
    database, work_root, campaign_id, scenes = _database(tmp_path)
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (4, 4), color=(16, 32, 64)).save(reference)
    workspace_path = "fx/tools/flow/cli-workspace"

    generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-flow-generate",
        )
        + [
            "--reference-image-path",
            "references/avatar.png",
            "--reference-image-sha256",
            hashlib.sha256(reference.read_bytes()).hexdigest(),
            "--executor",
            "playwright_python",
            "--provider-action-approved-by",
            "operator-1",
            "--confirm-provider-action",
            "--provider-workspace-path",
            workspace_path,
        ],
    )

    assert generated.exit_code == 0, generated.stdout
    generation_id = json.loads(generated.stdout)["generation"]["imageGenerationId"]
    service = ImageService.for_database(database, work_root=work_root)
    with service._sessions() as session:
        run = session.scalar(
            select(FlowGenerationRunRow).where(
                FlowGenerationRunRow.image_generation_id == generation_id
            )
        )
        assert run is not None
        assert run.provider_workspace_path == workspace_path
        assert run.provider_workspace_fingerprint == hashlib.sha256(
            workspace_path.encode("utf-8")
        ).hexdigest()
    service.close()


@pytest.mark.parametrize(
    "workspace_path",
    ("https://labs.google/fx/tools/flow/x", "../flow/x", "fx/tools/flow/x?token=y"),
)
def test_image_generate_cli_rejects_untrusted_flow_workspace_route(
    tmp_path: Path,
    workspace_path: str,
) -> None:
    database, work_root, campaign_id, scenes = _database(tmp_path)
    reference = work_root / "references" / "avatar.png"
    reference.parent.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (4, 4)).save(reference)

    generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-flow-invalid-workspace",
        )
        + [
            "--reference-image-path",
            "references/avatar.png",
            "--reference-image-sha256",
            hashlib.sha256(reference.read_bytes()).hexdigest(),
            "--executor",
            "playwright_python",
            "--provider-action-approved-by",
            "operator-1",
            "--confirm-provider-action",
            "--provider-workspace-path",
            workspace_path,
        ],
    )

    assert generated.exit_code != 0


def test_image_candidate_review_commands_emit_sanitized_domain_error_json(tmp_path: Path) -> None:
    database, work_root, campaign_id, scenes = _database(tmp_path)
    generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-review",
        ),
    )
    generation_id = json.loads(generated.stdout)["generation"]["imageGenerationId"]
    _complete_locally(database, work_root)

    listed = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "list",
            generation_id,
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert listed.exit_code == 0
    candidates = json.loads(listed.stdout)["candidates"]
    assert [candidate["candidateIndex"] for candidate in candidates] == [0, 1]
    candidate_id = candidates[0]["imageCandidateId"]

    fetched = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "get",
            candidate_id,
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert fetched.exit_code == 0
    assert json.loads(fetched.stdout)["candidate"]["imageCandidateId"] == candidate_id

    approved = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "approve",
            candidate_id,
            "--approved-by",
            "reviewer-1",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert approved.exit_code == 0
    assert json.loads(approved.stdout)["candidate"]["reviewStatus"] == "approved"

    invalid_review = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "reject",
            candidate_id,
            "--rejected-by",
            "reviewer\\nSENSITIVE",
            "--reason",
            "token=SENSITIVE",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert invalid_review.exit_code == 1
    assert json.loads(invalid_review.stdout) == {
        "success": False,
        "error": {"code": "image_invalid", "message": "Image input is invalid."},
    }
    assert "SENSITIVE" not in invalid_review.stdout
    assert "Traceback" not in invalid_review.stdout

    second_generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-replacement",
        ),
    )
    assert second_generated.exit_code == 0
    second_generation_id = json.loads(second_generated.stdout)["generation"]["imageGenerationId"]
    _complete_locally(database, work_root)
    second_candidates = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "list",
            second_generation_id,
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    replacement_id = json.loads(second_candidates.stdout)["candidates"][0]["imageCandidateId"]
    rejected = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "reject",
            replacement_id,
            "--rejected-by",
            "reviewer-2",
            "--reason",
            "Framing is too tight",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert rejected.exit_code == 0
    assert json.loads(rejected.stdout)["candidate"]["reviewStatus"] == "rejected"
    replaced = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "replace-approved",
            scenes[0],
            replacement_id,
            "--approved-by",
            "reviewer-3",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert replaced.exit_code == 0
    assert json.loads(replaced.stdout)["candidate"]["reviewStatus"] == "approved"

    missing = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "get",
            "11111111-1111-4111-8111-111111111111",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["error"] == {
        "code": "image_candidate_not_found",
        "message": "Image candidate not found.",
    }


def test_image_cli_close_failure_preserves_sanitized_domain_error(monkeypatch, tmp_path: Path) -> None:
    class CloseFailingService:
        def get_candidate(self, _candidate_id: str):
            raise ImageCandidateNotFoundError

        def close(self) -> None:
            raise RuntimeError(r"SENSITIVE token=private SELECT * C:\\Users\\Private\\auraly.db")

    monkeypatch.setattr("auraly_pipeline.cli._image_service", lambda _database, _work_root: CloseFailingService())
    result = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "get",
            "11111111-1111-4111-8111-111111111111",
            "--database",
            str(tmp_path / "auraly.db"),
            "--work-root",
            str(tmp_path / "work"),
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {
            "code": "image_candidate_not_found",
            "message": "Image candidate not found.",
        },
    }
    assert "SENSITIVE" not in result.stdout
    assert "SELECT" not in result.stdout
    assert "Users" not in result.stdout
    assert "Traceback" not in result.stdout


def test_cli_does_not_execute_provider_or_browser(monkeypatch, tmp_path: Path) -> None:
    def provider_or_browser_execution_is_forbidden(*_args, **_kwargs):
        raise AssertionError("browser/provider execution must not occur during local image CLI work")

    monkeypatch.setattr(
        "auraly_pipeline.cli.prepare_generation", provider_or_browser_execution_is_forbidden
    )
    database, work_root, campaign_id, scenes = _database(tmp_path)
    generated = runner.invoke(
        app,
        _generate_args(
            database,
            work_root,
            campaign_id,
            scenes[0],
            idempotency_key="image-cli-local-fake",
        ),
    )
    generation_id = json.loads(generated.stdout)["generation"]["imageGenerationId"]
    _complete_locally(database, work_root)

    candidates = runner.invoke(
        app,
        [
            "image",
            "candidate",
            "list",
            generation_id,
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert candidates.exit_code == 0
    payload = json.loads(candidates.stdout)
    assert payload["candidates"][0]["sourcePath"].startswith("campaigns/image-cli/images/")
    assert str(work_root) not in candidates.stdout
    assert "playwright" not in candidates.stdout.casefold()

    regenerated = runner.invoke(
        app,
        [
            "image",
            "regenerate",
            campaign_id,
            "--scene-variant-id",
            scenes[0],
            "--idempotency-key",
            "image-cli-regenerate",
            "--prompt-snapshot",
            "A moonlit studio with cards on a velvet table",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert regenerated.exit_code == 0
    assert json.loads(regenerated.stdout)["generation"]["generationNumber"] == 2

    duplicate_regeneration = runner.invoke(
        app,
        [
            "image",
            "regenerate",
            campaign_id,
            "--scene-variant-id",
            scenes[0],
            "--idempotency-key",
            "image-cli-regenerate",
            "--prompt-snapshot",
            "A moonlit studio with cards on a velvet table",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert duplicate_regeneration.exit_code == 1
    assert json.loads(duplicate_regeneration.stdout)["error"]["code"] == "image_idempotency_conflict"

    generations = runner.invoke(
        app,
        [
            "image",
            "generation",
            "list",
            scenes[0],
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )
    assert [item["generationNumber"] for item in json.loads(generations.stdout)["generations"]] == [
        1,
        2,
    ]


def test_image_generation_recover_emits_exact_sanitized_json(tmp_path: Path) -> None:
    database, work_root, generation_id = _blocked_flow_generation(tmp_path)

    result = runner.invoke(
        app,
        [
            "image",
            "generation",
            "recover",
            generation_id,
            "--reconciled-by",
            "operator-1",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "success",
        "generation",
        "flowRun",
        "slots",
        "job",
        "reconciliationReason",
    }
    assert payload["success"] is True
    assert payload["reconciliationReason"] == "no_dispatch_proven"
    assert payload["job"]["status"] == "queued"
    assert payload["flowRun"]["stage"] == "prepared"
    assert len(payload["slots"]) == 2
    for denied in (
        "PRIVATE PROMPT",
        "avatar.png",
        str(work_root),
        "labs.google",
        "Traceback",
    ):
        assert denied not in result.stdout


def test_image_generation_resolve_no_dispatch_emits_exact_sanitized_json(
    tmp_path: Path,
) -> None:
    database, work_root, generation_id = _blocked_flow_generation(
        tmp_path,
        ambiguous=True,
    )

    result = runner.invoke(
        app,
        [
            "image",
            "generation",
            "resolve-no-dispatch",
            generation_id,
            "--resolved-by",
            "operator-1",
            "--reason",
            "Operator confirmed no generation.",
            "--database",
            str(database),
            "--work-root",
            str(work_root),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "success",
        "generation",
        "flowRun",
        "slots",
        "job",
        "reconciliationReason",
    }
    assert payload["reconciliationReason"] == "no_dispatch_proven"
    assert payload["flowRun"]["dispatchAttemptNumber"] == 2
    assert payload["job"]["status"] == "queued"


def test_image_generation_recovery_failure_is_nonzero_and_sanitized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FailingService:
        def recover_generation(self, *_args, **_kwargs):
            raise RuntimeError(
                r"PRIVATE PROMPT token=SECRET C:\\Users\\Private\\profile labs.google"
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "auraly_pipeline.cli._image_service",
        lambda _database, _work_root: FailingService(),
    )
    result = runner.invoke(
        app,
        [
            "image",
            "generation",
            "recover",
            "00000000-0000-4000-8000-000000000001",
            "--reconciled-by",
            "operator-1",
            "--database",
            str(tmp_path / "private.db"),
            "--work-root",
            str(tmp_path / "PRIVATE-work"),
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {
            "code": "image_operation_failed",
            "message": "The image operation failed safely.",
        },
    }
    for denied in ("PRIVATE", "SECRET", "Users", "labs.google", "Traceback"):
        assert denied not in result.stdout


def test_image_generation_resolve_no_dispatch_unexpected_failure_is_sanitized(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class FailingService:
        def resolve_no_dispatch(self, *_args, **_kwargs):
            raise RuntimeError(
                r"PRIVATE PROMPT token=SECRET C:\\Users\\Private\\profile labs.google"
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "auraly_pipeline.cli._image_service",
        lambda _database, _work_root: FailingService(),
    )
    result = runner.invoke(
        app,
        [
            "image",
            "generation",
            "resolve-no-dispatch",
            "00000000-0000-4000-8000-000000000001",
            "--resolved-by",
            "operator-1",
            "--reason",
            "Operator confirmed no generation.",
            "--database",
            str(tmp_path / "private.db"),
            "--work-root",
            str(tmp_path / "PRIVATE-work"),
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {
            "code": "image_operation_failed",
            "message": "The image operation failed safely.",
        },
    }
    for denied in ("PRIVATE", "SECRET", "Users", "labs.google", "Traceback"):
        assert denied not in result.stdout
