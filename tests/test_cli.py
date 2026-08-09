from pathlib import Path

from typer.testing import CliRunner

from auraly_pipeline.cli import app


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
