from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from auraly_pipeline.editing.schema import export_editing_schemas


def test_schemas_validate_examples_and_do_not_change_legacy(tmp_path: Path) -> None:
    legacy = Path("schemas/edit.schema.json").read_bytes()
    paths = export_editing_schemas(tmp_path)
    for path, example in zip(paths, ["edit-profile.json", "edit-resolve.json", "edit-manifest.v2.json",
                                   "edit-batch-request.json", "caption-timing.json", "edit-batch-plan.json"], strict=True):
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(json.loads((Path("examples") / example).read_text()))
        assert path.read_bytes() == (Path("schemas") / path.name).read_bytes()
    assert Path("schemas/edit.schema.json").read_bytes() == legacy


def test_batch_schemas_do_not_change_d3a(tmp_path: Path) -> None:
    original = {name: (Path("schemas") / name).read_bytes() for name in (
        "edit-profile.schema.json", "edit-resolve.schema.json", "edit-manifest.v2.schema.json")}
    paths = export_editing_schemas(tmp_path)
    assert len(paths) == 6
    assert all((tmp_path / name).read_bytes() == content for name, content in original.items())


def test_schema_rejects_spoken_and_unknown_override() -> None:
    manifest = json.loads(Path("examples/edit-manifest.v2.json").read_text())
    manifest["headline"]["spoken"] = True
    schema = json.loads(Path("schemas/edit-manifest.v2.schema.json").read_text())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(manifest)
    request = json.loads(Path("examples/edit-resolve.json").read_text())
    request["video"] = {"headline": {"unknown": 1}}
    schema = json.loads(Path("schemas/edit-resolve.schema.json").read_text())
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(request)
