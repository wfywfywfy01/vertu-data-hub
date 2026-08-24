"""Download pinned local Chinese-CLIP model files during an online build stage."""
from app.embeddings.chinese_clip import MODEL_ID, MODEL_REVISION, prepare_model


def main() -> None:
    prepare_model(download=True)
    print(f"semantic image model ready: {MODEL_ID}@{MODEL_REVISION}")


if __name__ == "__main__":
    main()
