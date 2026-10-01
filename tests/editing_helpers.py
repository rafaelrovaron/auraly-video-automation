from __future__ import annotations

from typing import Any


def profile_data() -> dict[str, Any]:
    return {
        "profileId": "plain", "name": "Plain", "version": 1,
        "createdAt": "2026-10-01T00:00:00Z", "defaults": {},
    }


def request_data(profile_sha: str = "a" * 64) -> dict[str, Any]:
    return {
        "profileRef": {"profileId": "plain", "version": 1, "hash": profile_sha},
        "campaignId": "campaign-1", "videoId": "video-1", "outputVariantId": "a",
        "source": {"id": "source-1", "path": "source.mp4", "sha256": "b" * 64,
                   "durationSec": 7.224},
        "headlineText": "Headline A",
    }
