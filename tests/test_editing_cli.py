from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auraly_pipeline.cli import app
from auraly_pipeline.editing.domain import EditManifestV2, EditProfile
from auraly_pipeline.editing.resolver import profile_hash
from tests.editing_helpers import file_sha, make_source, profile_data, request_data


def test_cli_profile_and_resolve_roundtrip(tmp_path: Path) -> None:
    runner = CliRunner()
    roots = ["--project-root", str(tmp_path), "--work-root", str(tmp_path / "work")]
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(profile_data()))
    created = runner.invoke(app, ["edit", "profile-create", "--request", str(profile_file), *roots])
    assert created.exit_code == 0, created.output
    profile = EditProfile.model_validate_json(created.stdout)
    assert profile.version == 1
    got = runner.invoke(app, ["edit", "profile-get", "--profile-id", "plain", "--version", "1", *roots])
    assert got.exit_code == 0
    assert EditProfile.model_validate_json(got.stdout) == profile
    listed = runner.invoke(app, ["edit", "profile-list", *roots])
    assert json.loads(listed.stdout)[0]["version"] == 1
    data = profile_data()
    data["version"] = 2
    profile_file.write_text(json.dumps(data))
    updated = runner.invoke(app, ["edit", "profile-new-version", "--profile-id", "plain", "--base-version", "1", "--request", str(profile_file), *roots])
    assert updated.exit_code == 0
    assert EditProfile.model_validate_json(updated.stdout).version == 2
    source = make_source(tmp_path)
    data = request_data(profile_hash(profile))
    data["source"].update(sha256=file_sha(source), durationSec=1)
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps(data))
    args = ["edit", "resolve", "--request", str(request_file), *roots]
    dry = runner.invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert not list((tmp_path / "work").rglob("manifest.json"))
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)
    assert first.exit_code == second.exit_code == 0
    manifest = EditManifestV2.model_validate_json(first.stdout)
    assert first.stdout == second.stdout == dry.stdout
    got = runner.invoke(app, ["edit", "manifest-get", "--campaign-id", "campaign-1", "--video-id", "video-1", "--output-variant-id", "a", "--manifest-hash", manifest.manifest_hash, *roots])
    assert got.exit_code == 0
    assert EditManifestV2.model_validate_json(got.stdout) == manifest


def test_cli_errors_are_sanitized(tmp_path: Path) -> None:
    runner = CliRunner()
    path = tmp_path / "sensitive.json"
    data = profile_data()
    data["defaults"] = {"output": {"fps": "private value"}}
    path.write_text(json.dumps(data))
    result = runner.invoke(app, ["edit", "profile-create", "--request", str(path), "--project-root", str(tmp_path), "--work-root", str(tmp_path / "work")])
    assert result.exit_code == 1
    assert "defaults.output.fps" in result.output
    assert "private value" not in result.output
    assert str(tmp_path) not in result.output


def test_cli_validate_version_dispatch() -> None:
    runner = CliRunner()
    legacy = runner.invoke(app, ["validate", "examples/susan.edit.json"])
    assert legacy.exit_code == 0
    refused = runner.invoke(app, ["edit", "validate", "--manifest", "examples/susan.edit.json"])
    assert refused.exit_code == 1
    assert "2.0" in refused.output
    valid = runner.invoke(app, ["edit", "validate", "--manifest", "examples/edit-manifest.v2.json"])
    assert valid.exit_code == 0, valid.output
