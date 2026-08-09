import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.test_models import valid_manifest_data


SCHEMA_PATH = Path("schemas/edit.schema.json")


def test_versioned_schema_validates_manifest_contract() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    Draft202012Validator(schema).validate(valid_manifest_data())


def test_schema_rejects_spoken_headline() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    invalid = valid_manifest_data()
    invalid["headline"]["spoken"] = True

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(invalid)
