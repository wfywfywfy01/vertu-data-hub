"""Download and validate the configured local speech model."""
from app.config import settings
from app.processing.media import get_transcriber


def main() -> None:
    get_transcriber().load(download=True)
    print(f"media transcription model ready: {settings.media_transcription_model}")


if __name__ == "__main__":
    main()
