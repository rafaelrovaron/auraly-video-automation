from __future__ import annotations

from typing import Any

from auraly_pipeline.campaigns.domain import CopyMaster
from auraly_pipeline.editing.batch_domain import BatchInputs
from auraly_pipeline.editing.domain import IdentityRef, SourceVideoRef


def batch_data() -> dict[str, Any]:
    return {
        "campaignId": "campaign-one", "renderId": "11111111-1111-4111-8111-111111111111",
        "videoId": "video-one",
        "profileRef": {"profileId": "plain", "version": 1, "hash": "a" * 64},
        "headlineText": "Visual only",
        "variants": [{"key": key, "label": key.upper(), "overrides": {
            "headline": {"text": "Visual " + key}
        }} for key in ("a", "b", "c")],
    }


def timing_data() -> dict[str, Any]:
    return {
        "sourceSha256": "b" * 64, "copyMasterId": "copy-one", "copyHash": "c" * 64,
        "processedAudioSha256": "d" * 64, "timebase": "source_mp4",
        "origin": "manual", "acceptedBy": "tester",
        "cues": [{"startSec": 0, "endSec": 1, "tokenStart": 0, "tokenEnd": 3}],
    }


def batch_inputs() -> BatchInputs:
    copy = CopyMaster.model_validate({
        "copyMasterId": "copy-one", "campaignId": "campaign-one", "version": 1,
        "sourceText": "Original", "headline": "Visual only", "hook": "Visual hook",
        "body": "Body words.", "cta": "Click now!", "approvalState": "approved",
        "approvedBy": "tester", "approvedAt": "2026-10-02T00:00:00Z",
        "createdAt": "2026-10-02T00:00:00Z", "updatedAt": "2026-10-02T00:00:00Z",
    })
    return BatchInputs(
        source=SourceVideoRef(id=batch_data()["renderId"], path="source.mp4",
                              sha256="b" * 64, duration_sec=7.224),
        copy=copy, voice_ref=IdentityRef(id="voice-one", hash="d" * 64),
        image_ref=IdentityRef(id="image-one", hash="e" * 64),
    )
