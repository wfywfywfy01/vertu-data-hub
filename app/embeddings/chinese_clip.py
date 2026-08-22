"""Pinned, local-only Chinese CLIP for text-to-image retrieval."""
from __future__ import annotations

from io import BytesIO
import threading

from PIL import Image, ImageFilter, ImageStat


MODEL_ID = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_REVISION = "36e679e65c2a2fead755ae21162091293ad37834"
SAFE_WEIGHTS_REVISION = "f4a64596bbcf9a2a94591b74b9dc39b2e4e77e3e"


def prepare_model(*, download: bool) -> tuple[str, str]:
    """Resolve pinned config and safetensors weights, optionally downloading them."""
    from huggingface_hub import hf_hub_download, snapshot_download

    local_only = not download
    model_dir = snapshot_download(
        MODEL_ID,
        revision=MODEL_REVISION,
        allow_patterns=["config.json", "preprocessor_config.json", "vocab.txt"],
        local_files_only=local_only,
    )
    weights = hf_hub_download(
        MODEL_ID,
        "model.safetensors",
        revision=SAFE_WEIGHTS_REVISION,
        local_files_only=local_only,
    )
    return model_dir, weights


class ChineseClipEmbedder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processor = None
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from safetensors.torch import load_file
            from transformers import ChineseCLIPConfig, ChineseCLIPModel, ChineseCLIPProcessor

            model_dir, weights = prepare_model(download=False)
            processor = ChineseCLIPProcessor.from_pretrained(model_dir, local_files_only=True)
            model = ChineseCLIPModel(
                ChineseCLIPConfig.from_pretrained(model_dir, local_files_only=True)
            )
            missing, unexpected = model.load_state_dict(load_file(weights), strict=False)
            unsupported = [key for key in unexpected if not key.endswith("position_ids")]
            if missing or unsupported:
                raise RuntimeError(
                    f"semantic image model state mismatch: missing={missing}, unexpected={unsupported}"
                )
            model.eval()
            self._processor = processor
            self._model = model

    @staticmethod
    def _normalized(rows) -> list[list[float]]:
        import torch

        rows = torch.nn.functional.normalize(rows, p=2, dim=-1)
        return rows.detach().cpu().tolist()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        import torch

        self._load()
        with self._lock, torch.inference_mode():
            inputs = self._processor(text=texts, padding=True, return_tensors="pt")
            output = self._model.get_text_features(**inputs).pooler_output
            return self._normalized(output)

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        import torch

        self._load()
        decoded = []
        try:
            for data in images:
                decoded.append(Image.open(BytesIO(data)).convert("RGB"))
            with self._lock, torch.inference_mode():
                inputs = self._processor(images=decoded, return_tensors="pt")
                output = self._model.get_image_features(**inputs).pooler_output
                return self._normalized(output)
        finally:
            for image in decoded:
                image.close()


def image_quality(data: bytes) -> float:
    """Cheap local quality signal for resolution, sharpness, and exposure."""
    with Image.open(BytesIO(data)) as source:
        image = source.convert("L")
        width, height = image.size
        resolution = min(1.0, min(width, height) / 1080.0)
        thumbnail = image.copy()
        thumbnail.thumbnail((512, 512))
        mean = ImageStat.Stat(thumbnail).mean[0]
        exposure = max(0.0, 1.0 - abs(mean - 127.5) / 127.5)
        edges = thumbnail.filter(ImageFilter.FIND_EDGES)
        sharpness = min(1.0, ImageStat.Stat(edges).stddev[0] / 45.0)
    return round(0.35 * resolution + 0.35 * sharpness + 0.30 * exposure, 6)


_embedder: ChineseClipEmbedder | None = None


def get_chinese_clip() -> ChineseClipEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = ChineseClipEmbedder()
    return _embedder
