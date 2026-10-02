from __future__ import annotations

from typing import Any


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
