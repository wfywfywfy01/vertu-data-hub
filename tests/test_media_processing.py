from pathlib import Path

from app.processing.media import extract_keyframes, probe_media
from app.config import settings
from tests.media_fixtures import sample_video


def test_pyav_probes_and_extracts_real_video(tmp_path, monkeypatch):
    path = Path(tmp_path) / "sample.mp4"
    path.write_bytes(sample_video())
    monkeypatch.setattr(settings, "media_keyframe_interval_seconds", 1)
    monkeypatch.setattr(settings, "media_max_keyframes", 3)

    probe = probe_media(path)
    frames = extract_keyframes(path, probe.duration)

    assert probe.has_video is True
    assert probe.has_audio is False
    assert probe.duration >= 3
    assert len(frames) == 3
    assert all(data.startswith(b"\xff\xd8") for _timestamp, data in frames)
