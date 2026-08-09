import pytest

from auraly_pipeline.probe import ProbeError, parse_ffprobe_payload


def ffprobe_payload() -> dict:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "r_frame_rate": "30/1",
                "avg_frame_rate": "30000/1001",
                "duration": "12.500000",
                "tags": {"rotate": "0"},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "12.480000",
            },
        ],
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.500000",
            "size": "1234567",
        },
    }


def test_parse_ffprobe_payload_returns_typed_media_facts() -> None:
    probe = parse_ffprobe_payload(ffprobe_payload())

    assert probe.duration_sec == pytest.approx(12.5)
    assert probe.video.codec == "h264"
    assert probe.video.width == 1080
    assert probe.video.height == 1920
    assert probe.video.fps == pytest.approx(29.97002997)
    assert probe.video.is_vfr is True
    assert probe.video.rotation == 0
    assert probe.has_audio is True
    assert probe.audio is not None
    assert probe.audio.sample_rate == 48000
    assert probe.audio.channels == 2


def test_parse_ffprobe_payload_rejects_missing_video_stream() -> None:
    payload = ffprobe_payload()
    payload["streams"] = [payload["streams"][1]]

    with pytest.raises(ProbeError, match="video stream"):
        parse_ffprobe_payload(payload)
