from __future__ import annotations

import pytest
from pydantic import ValidationError

from auraly_pipeline.editing.batch_domain import EditBatchRequest, CaptionTimingInput
from tests.editing_batch_helpers import batch_data, timing_data


def test_default_limit_is_three() -> None:
    request = EditBatchRequest.model_validate(batch_data())
    assert request.max_outputs == 3
    data = batch_data()
    data["variants"].append({"key": "d", "label": "D"})
    with pytest.raises(ValidationError):
        EditBatchRequest.model_validate(data)


@pytest.mark.parametrize("limit", [True, 0, 1.5])
def test_strict_positive_limit(limit: object) -> None:
    data = batch_data()
    data["maxOutputs"] = limit
    with pytest.raises(ValidationError):
        EditBatchRequest.model_validate(data)


@pytest.mark.parametrize("case", ["empty", "duplicate", "reserved", "extra", "path"])
def test_limit_and_keys(case: str) -> None:
    data = batch_data()
    if case == "empty":
        data["variants"] = []
    elif case == "duplicate":
        data["variants"][1]["key"] = "a"
    elif case == "reserved":
        data["variants"][0]["key"] = "con"
    elif case == "extra":
        data["captionsText"] = "not permitted"
    else:
        data["videoId"] = "../escape"
    with pytest.raises(ValidationError):
        EditBatchRequest.model_validate(data)


@pytest.mark.parametrize(("field", "value"), [
    ("origin", "invented"), ("timebase", "voice"), ("acceptedBy", "https://secret.invalid"),
])
def test_timing_contract(field: str, value: str) -> None:
    data = timing_data()
    data[field] = value
    with pytest.raises(ValidationError):
        CaptionTimingInput.model_validate(data)


@pytest.mark.parametrize(("field", "value"), [
    ("startSec", float("nan")), ("endSec", float("inf")), ("endSec", 0),
    ("tokenStart", True), ("tokenEnd", 2.5), ("tokenEnd", 0),
])
def test_invalid_cue(field: str, value: object) -> None:
    data = timing_data()
    data["cues"][0][field] = value
    with pytest.raises(ValidationError):
        CaptionTimingInput.model_validate(data)


def test_zero_start_is_valid() -> None:
    assert CaptionTimingInput.model_validate(timing_data()).cues[0].start_sec == 0
