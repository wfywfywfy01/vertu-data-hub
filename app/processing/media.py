"""Local media probing, keyframe extraction, and speech transcription."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class MediaProbe:
    duration: float
    has_audio: bool
    has_video: bool


def probe_media(path: Path) -> MediaProbe:
    import av

    with av.open(str(path)) as container:
        has_audio = bool(container.streams.audio)
        has_video = bool(container.streams.video)
        duration = float(container.duration or 0) / float(av.time_base)
        if duration <= 0:
            streams = [*container.streams.audio, *container.streams.video]
            durations = [
                float(stream.duration * stream.time_base)
                for stream in streams
                if stream.duration is not None and stream.time_base is not None
            ]
            duration = max(durations, default=0.0)
    return MediaProbe(round(duration, 3), has_audio, has_video)


def extract_keyframes(path: Path, duration: float) -> list[tuple[float, bytes]]:
    import av

    if duration <= 0:
        return []
    interval = settings.media_keyframe_interval_seconds
    targets = [
        min(duration, index * interval)
        for index in range(settings.media_max_keyframes)
        if index * interval < duration
    ]
    frames = []
    with av.open(str(path)) as container:
        if not container.streams.video:
            return []
        stream = container.streams.video[0]
        for target in targets:
            offset = int(target / float(stream.time_base)) if stream.time_base else 0
            container.seek(offset, stream=stream, backward=True)
            selected = None
            for frame in container.decode(stream):
                timestamp = float(frame.pts * frame.time_base) if frame.pts is not None else target
                selected = (timestamp, frame)
                if timestamp >= target:
                    break
            if selected is None:
                continue
            timestamp, frame = selected
            image = frame.to_image().convert("RGB")
            image.thumbnail((1280, 1280))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
            frames.append((round(max(0.0, timestamp), 3), output.getvalue()))
    return frames


class LocalTranscriber:
    def __init__(self) -> None:
        self._model = None

    def load(self, *, download: bool) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        model_path = download_model(
            settings.media_transcription_model,
            local_files_only=not download,
            revision=settings.media_transcription_model_revision,
        )
        self._model = WhisperModel(
            model_path,
            device="cpu",
            compute_type=settings.media_transcription_compute_type,
            local_files_only=True,
        )

    def transcribe(
        self, path: Path, language: str | None = None
    ) -> tuple[list[TranscriptSegment], str | None]:
        self.load(download=False)
        segments, info = self._model.transcribe(
            str(path),
            language=(language.split("-", 1)[0].lower() if language else None),
            vad_filter=True,
            beam_size=5,
        )
        rows = [
            TranscriptSegment(float(segment.start), float(segment.end), segment.text.strip())
            for segment in segments
            if segment.text.strip()
        ]
        return rows, getattr(info, "language", None)


_transcriber: LocalTranscriber | None = None


def get_transcriber() -> LocalTranscriber:
    global _transcriber
    if _transcriber is None:
        _transcriber = LocalTranscriber()
    return _transcriber
