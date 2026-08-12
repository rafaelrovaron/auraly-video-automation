from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auraly_pipeline.voices.domain import (
    TranscriptMatchStatus,
    VoiceMaster,
    VoiceMasterStatus,
    normalize_transcript,
    transcript_comparison,
)


def _voice_data() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "voiceMasterId": "11111111-1111-4111-8111-111111111111",
        "campaignId": "voice-pilot",
        "copyMasterId": "22222222-2222-4222-8222-222222222222",
        "copyMasterVersion": 1,
        "generation": 1,
        "status": "review_required",
        "provider": "elevenlabs",
        "voicePreset": "soul-constellation",
        "voiceId": "voice_123",
        "modelId": "eleven_multilingual_v2",
        "settingsFingerprint": "a" * 64,
        "rawAudioPath": "work/voice-pilot/voice/11111111-1111-4111-8111-111111111111/raw/provider.mp3",
        "processedAudioPath": "work/voice-pilot/voice/11111111-1111-4111-8111-111111111111/processed/voice-master.wav",
        "transcriptPath": "work/voice-pilot/voice/11111111-1111-4111-8111-111111111111/inspection/transcript.json",
        "rawSha256": "b" * 64,
        "processedSha256": "c" * 64,
        "rawSizeBytes": 123,
        "rawFormat": "mp3",
        "durationSeconds": 10.0,
        "wordCount": 25,
        "wpm": 150.0,
        "sampleRate": 48000,
        "channels": 1,
        "loudnessLufs": -16.2,
        "truePeakDbfs": -1.3,
        "leadingSilenceSeconds": 0.1,
        "trailingSilenceSeconds": 0.2,
        "longInternalPauses": [],
        "transcriptSource": "elevenlabs_alignment",
        "transcriptMatchStatus": "matched",
        "transcriptMatchScore": 1.0,
        "headlineSpoken": False,
        "qcFindings": [],
        "createdAt": now,
        "updatedAt": now,
    }


def test_voice_master_contract_is_campaign_level_and_uses_relative_paths() -> None:
    voice = VoiceMaster.model_validate(_voice_data())
    assert voice.status is VoiceMasterStatus.REVIEW_REQUIRED
    assert voice.transcript_match_status is TranscriptMatchStatus.MATCHED
    assert voice.scene_variant_id if hasattr(voice, "scene_variant_id") else None is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("rawAudioPath", "../escape.mp3"),
        ("processedAudioPath", r"C:\\private\\voice.wav"),
        ("transcriptPath", "/home/private/transcript.json"),
    ],
)
def test_voice_master_rejects_untrusted_or_absolute_paths(field: str, value: str) -> None:
    data = _voice_data()
    data[field] = value
    with pytest.raises(ValidationError):
        VoiceMaster.model_validate(data)


def test_transcript_comparison_normalizes_punctuation_but_preserves_words() -> None:
    report = transcript_comparison(
        expected="He is coming back. Take the one-minute reading.",
        recognized="He is coming back take the one minute reading",
        headline="THE SIGN YOU NEEDED",
    )
    assert normalize_transcript("One-minute!") == "one minute"
    assert report.status is TranscriptMatchStatus.MATCHED
    assert report.score == 1.0
    assert report.headline_spoken is False
    assert report.missing_tokens == []
    assert report.unexpected_tokens == []


def test_transcript_mismatch_and_headline_are_explicit_review_failures() -> None:
    report = transcript_comparison(
        expected="Your person still thinks about you every night",
        recognized="The sign you needed your person forgot about you",
        headline="The sign you needed",
    )
    assert report.status is TranscriptMatchStatus.MISMATCHED
    assert report.headline_spoken is True
    assert report.missing_tokens
    assert report.unexpected_tokens
