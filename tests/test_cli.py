from pathlib import Path
import json

from typer.testing import CliRunner

from auraly_pipeline.cli import app
from tests.test_campaign_domain import valid_campaign_data


runner = CliRunner()


def test_cli_ingest_passes_explicit_inputs(monkeypatch, tmp_path: Path) -> None:
    captured = {}
    reel_dir = tmp_path / "work/susan-001"

    def fake_ingest(**kwargs):
        captured.update(kwargs)
        return reel_dir

    monkeypatch.setattr("auraly_pipeline.cli.ingest_reel", fake_ingest)
    result = runner.invoke(
        app,
        [
            "ingest",
            "--video",
            str(tmp_path / "input.mp4"),
            "--copy",
            str(tmp_path / "copy.md"),
            "--character",
            "susan-smith",
            "--work-root",
            str(tmp_path / "work"),
            "--reel-id",
            "Susan 001",
        ],
    )

    assert result.exit_code == 0
    assert str(reel_dir) in result.stdout
    assert captured["character"] == "susan-smith"
    assert captured["reel_id"] == "Susan 001"


def test_cli_validate_reports_valid_manifest(tmp_path: Path) -> None:
    from tests.test_models import valid_manifest_data
    import json

    manifest = tmp_path / "edit.json"
    manifest.write_text(json.dumps(valid_manifest_data()), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(manifest)])

    assert result.exit_code == 0
    assert "valid" in result.stdout.casefold()


def test_cli_knowledge_status_reports_ready(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "auraly_pipeline.cli.knowledge_status",
        lambda root: {
            "root": str(root),
            "files": 142,
            "videos": 128,
            "documents": 9,
            "inventoryFailures": 0,
            "videosReviewed": 128,
            "reviewFailures": 0,
            "transcriptsCompleted": 52,
            "transcriptFailures": 0,
            "ready": True,
        },
        raising=False,
    )

    result = runner.invoke(app, ["knowledge-status", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "Ready: yes" in result.stdout
    assert "Files: 142" in result.stdout


def test_cli_knowledge_search_prints_matching_reference(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "auraly_pipeline.cli.search_knowledge",
        lambda root, query, collection=None, limit=10: [
            {
                "assetId": "asset-0001",
                "path": "Cópia de AD 05 V9 - SM-ING-FB.mp4",
                "collection": "validated-ad",
                "hook": "My cards will reveal his face.",
                "cta": "Take the free quiz.",
                "angles": ["soulmate-face-reveal"],
                "claimFlags": [],
            }
        ],
        raising=False,
    )

    result = runner.invoke(
        app,
        ["knowledge-search", "face reveal", "--root", str(tmp_path), "--collection", "validated-ad"],
    )

    assert result.exit_code == 0
    assert "asset-0001" in result.stdout
    assert "My cards will reveal his face." in result.stdout


def test_cli_knowledge_search_prints_document_snippet(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "auraly_pipeline.cli.search_knowledge",
        lambda root, query, collection=None, limit=10: [
            {
                "recordType": "document",
                "documentId": "doc-001",
                "title": "Swipe de formatos",
                "path": "knowledge/documents/raw/swipe.md",
                "collection": "competitor-reference",
                "snippet": "Usar um horóscopo visual na mesa da especialista.",
            }
        ],
    )

    result = runner.invoke(app, ["knowledge-search", "horóscopo visual", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "doc-001" in result.stdout
    assert "horóscopo visual" in result.stdout.casefold()


def test_image_cli_never_emits_raw_exception_details(monkeypatch, tmp_path: Path) -> None:
    from auraly_pipeline.image_generation import ImageGenerationError

    def fail(
        _context: Path,
        _downloads_dir: Path | None = None,
        _project_root: Path | None = None,
    ):
        raise ImageGenerationError(
            "load_context",
            "SENSITIVE MULTI WORD VALUE C:\\Users\\Private\\context.json",
        )

    monkeypatch.setattr("auraly_pipeline.cli.record_download_baseline", fail)
    result = runner.invoke(
        app,
        ["image-download-baseline", "--context", str(tmp_path / "request.json")],
    )

    assert result.exit_code == 1
    assert "SENSITIVE MULTI WORD VALUE" not in result.stdout
    assert "Users" not in result.stdout
    assert "Generation context is invalid or outside the approved job layout." in result.stdout


def test_image_cli_contains_unexpected_exceptions(monkeypatch, tmp_path: Path) -> None:
    def fail(
        _context: Path,
        _downloads_dir: Path | None = None,
        _project_root: Path | None = None,
    ):
        raise OSError(r"SENSITIVE C:\Users\Private\victim.png")

    monkeypatch.setattr("auraly_pipeline.cli.record_download_baseline", fail)
    result = runner.invoke(
        app,
        ["image-download-baseline", "--context", str(tmp_path / "request.json")],
    )

    assert result.exit_code == 1
    assert "SENSITIVE" not in result.stdout
    assert "Users" not in result.stdout
    assert '"failedStep": "image_generation"' in result.stdout
    assert "The image-generation operation failed safely." in result.stdout


def test_campaign_cli_create_get_and_list_survive_new_invocations(tmp_path: Path) -> None:
    request_path = tmp_path / "campaign.json"
    database_path = tmp_path / "state" / "auraly.db"
    request_path.write_text(json.dumps(valid_campaign_data()), encoding="utf-8")

    created = runner.invoke(
        app,
        [
            "campaign",
            "create",
            "--input",
            str(request_path),
            "--database",
            str(database_path),
        ],
    )
    retrieved = runner.invoke(
        app,
        [
            "campaign",
            "get",
            "eight-of-cups-pilot",
            "--database",
            str(database_path),
        ],
    )
    listed = runner.invoke(
        app,
        ["campaign", "list", "--database", str(database_path)],
    )

    assert created.exit_code == 0
    assert retrieved.exit_code == 0
    assert listed.exit_code == 0
    created_payload = json.loads(created.stdout)
    retrieved_payload = json.loads(retrieved.stdout)
    listed_payload = json.loads(listed.stdout)
    assert created_payload["campaign"] == retrieved_payload["campaign"]
    assert listed_payload["count"] == 1
    assert listed_payload["campaigns"] == [created_payload["campaign"]]


def test_campaign_cli_duplicate_and_not_found_errors_are_safe(tmp_path: Path) -> None:
    request_path = tmp_path / "campaign.json"
    database_path = tmp_path / "auraly.db"
    request_path.write_text(json.dumps(valid_campaign_data()), encoding="utf-8")
    arguments = [
        "campaign",
        "create",
        "--input",
        str(request_path),
        "--database",
        str(database_path),
    ]
    assert runner.invoke(app, arguments).exit_code == 0

    duplicate = runner.invoke(app, arguments)
    missing = runner.invoke(
        app,
        ["campaign", "get", "missing-campaign", "--database", str(database_path)],
    )

    assert duplicate.exit_code == 1
    assert json.loads(duplicate.stdout) == {
        "success": False,
        "error": {
            "code": "campaign_conflict",
            "message": "A campaign with this ID already exists.",
        },
    }
    assert missing.exit_code == 1
    assert json.loads(missing.stdout)["error"] == {
        "code": "campaign_not_found",
        "message": "Campaign not found.",
    }
    assert str(database_path) not in duplicate.stdout + missing.stdout


def test_campaign_cli_invalid_input_does_not_echo_sensitive_content(tmp_path: Path) -> None:
    request_path = tmp_path / "private-campaign.json"
    request_path.write_text(
        json.dumps({"campaignId": "../escape", "password": "SENSITIVE VALUE"}),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["campaign", "create", "--input", str(request_path), "--database", str(tmp_path / "db.sqlite")],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {"code": "campaign_invalid", "message": "Campaign input is invalid."},
    }
    assert "SENSITIVE" not in result.stdout
    assert "private-campaign" not in result.stdout


def test_campaign_cli_rejects_non_standard_json_numbers(monkeypatch, tmp_path: Path) -> None:
    request_path = tmp_path / "campaign.json"
    raw_request = json.dumps(valid_campaign_data()).replace('"limitCents": 0', '"limitCents": NaN')
    request_path.write_text(raw_request, encoding="utf-8")
    monkeypatch.setattr(
        "auraly_pipeline.cli.CampaignCreate.model_validate",
        lambda _payload: (_ for _ in ()).throw(AssertionError("validation must not be reached")),
    )

    result = runner.invoke(
        app,
        ["campaign", "create", "--input", str(request_path), "--database", str(tmp_path / "db.sqlite")],
    )

    assert result.exit_code == 1
    assert "NaN" not in result.stdout
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {"code": "campaign_invalid", "message": "Campaign input is invalid."},
    }


def test_campaign_cli_contains_unexpected_database_errors(monkeypatch, tmp_path: Path) -> None:
    def fail(_database: Path):
        raise OSError(r"SENSITIVE C:\\Users\\Private\\auraly.db")

    monkeypatch.setattr("auraly_pipeline.cli.CampaignService.for_database", fail)
    result = runner.invoke(
        app,
        ["campaign", "list", "--database", str(tmp_path / "auraly.db")],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {
            "code": "campaign_operation_failed",
            "message": "The campaign operation failed safely.",
        },
    }
    assert "SENSITIVE" not in result.stdout
    assert "Users" not in result.stdout


def _job_request(job_type: str, key: str) -> dict:
    return {
        "jobType": job_type,
        "idempotencyKey": key,
        "input": {"operation": "deterministic-cli-test"},
        "maxAttempts": 3,
    }


def test_job_cli_submit_get_list_and_worker_once(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "auraly.db"
    request_path = tmp_path / "job.json"
    request_path.write_text(
        json.dumps(_job_request("fake.success", "cli-success")), encoding="utf-8"
    )
    submitted = runner.invoke(
        app,
        ["job", "submit", "--input", str(request_path), "--database", str(database_path)],
    )
    assert submitted.exit_code == 0
    submitted_payload = json.loads(submitted.stdout)
    job_id = submitted_payload["job"]["jobId"]

    retrieved = runner.invoke(app, ["job", "get", job_id, "--database", str(database_path)])
    listed = runner.invoke(
        app,
        ["job", "list", "--status", "queued", "--database", str(database_path)],
    )
    worked = runner.invoke(
        app,
        [
            "job",
            "worker-once",
            "--worker-id",
            "cli-worker",
            "--database",
            str(database_path),
        ],
    )

    assert retrieved.exit_code == listed.exit_code == worked.exit_code == 0
    assert json.loads(retrieved.stdout)["job"] == submitted_payload["job"]
    assert json.loads(listed.stdout)["count"] == 1
    worked_payload = json.loads(worked.stdout)
    assert worked_payload["worked"] is True
    assert worked_payload["job"]["status"] == "completed"
    assert worked_payload["job"]["attempts"][0]["status"] == "completed"


def test_job_cli_cancel_and_resume_blocked_job(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    request_path = tmp_path / "job.json"
    request_path.write_text(
        json.dumps(_job_request("fake.success", "cli-cancel")), encoding="utf-8"
    )
    submitted = runner.invoke(
        app,
        ["job", "submit", "--input", str(request_path), "--database", str(database_path)],
    )
    job_id = json.loads(submitted.stdout)["job"]["jobId"]
    cancelled = runner.invoke(
        app,
        ["job", "cancel", job_id, "--database", str(database_path)],
    )
    assert cancelled.exit_code == 0
    assert json.loads(cancelled.stdout)["job"]["status"] == "cancelled"

    request_path.write_text(
        json.dumps(_job_request("fake.blocked", "cli-blocked")), encoding="utf-8"
    )
    blocked_submit = runner.invoke(
        app,
        ["job", "submit", "--input", str(request_path), "--database", str(database_path)],
    )
    blocked_id = json.loads(blocked_submit.stdout)["job"]["jobId"]
    runner.invoke(
        app,
        ["job", "worker-once", "--worker-id", "cli-worker", "--database", str(database_path)],
    )
    resumed = runner.invoke(
        app,
        ["job", "resume", blocked_id, "--database", str(database_path)],
    )
    assert resumed.exit_code == 0
    assert json.loads(resumed.stdout)["job"]["status"] == "queued"


def test_job_cli_conflicts_and_invalid_sensitive_input_are_sanitized(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    request_path = tmp_path / "private-job.json"
    request_path.write_text(
        json.dumps(_job_request("fake.success", "same-key")), encoding="utf-8"
    )
    arguments = ["job", "submit", "--input", str(request_path), "--database", str(database_path)]
    assert runner.invoke(app, arguments).exit_code == 0
    changed = _job_request("fake.success", "same-key")
    changed["input"] = {"operation": "different"}
    request_path.write_text(json.dumps(changed), encoding="utf-8")
    conflict = runner.invoke(app, arguments)

    unsafe_path = tmp_path / "unsafe.json"
    unsafe_path.write_text(
        json.dumps(
            _job_request("fake.success", "unsafe")
            | {"input": {"accessToken": "SENSITIVE"}}
        ),
        encoding="utf-8",
    )
    invalid = runner.invoke(
        app,
        ["job", "submit", "--input", str(unsafe_path), "--database", str(database_path)],
    )

    assert conflict.exit_code == 1
    assert json.loads(conflict.stdout)["error"]["code"] == "job_conflict"
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout) == {
        "success": False,
        "error": {"code": "job_invalid", "message": "Job input is invalid."},
    }
    assert "SENSITIVE" not in invalid.stdout
    assert "unsafe.json" not in invalid.stdout


def test_job_cli_rejects_non_standard_json_before_validation(monkeypatch, tmp_path: Path) -> None:
    request_path = tmp_path / "job.json"
    request_path.write_text(
        '{"jobType":"fake.success","idempotencyKey":"nan","input":{"value":NaN}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "auraly_pipeline.cli.JobSubmit.model_validate",
        lambda _payload: (_ for _ in ()).throw(AssertionError("validation must not be reached")),
    )

    result = runner.invoke(
        app,
        ["job", "submit", "--input", str(request_path), "--database", str(tmp_path / "db.sqlite")],
    )

    assert result.exit_code == 1
    assert "NaN" not in result.stdout
    assert json.loads(result.stdout)["error"]["code"] == "job_invalid"


def test_job_cli_contains_unexpected_database_errors(monkeypatch, tmp_path: Path) -> None:
    def fail(_database: Path):
        raise OSError("SENSITIVE C:" + r"\\Users\\Private\\jobs.db")

    monkeypatch.setattr("auraly_pipeline.cli.JobService.for_database", fail)
    result = runner.invoke(
        app,
        ["job", "list", "--database", str(tmp_path / "jobs.db")],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "success": False,
        "error": {
            "code": "job_operation_failed",
            "message": "The job operation failed safely.",
        },
    }
    assert "SENSITIVE" not in result.stdout
    assert "Users" not in result.stdout
