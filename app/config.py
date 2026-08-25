"""环境变量配置。全部配置从 .env / 环境变量读取，代码中不出现明文密钥。"""
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class Settings:
    app_env: str = _env("APP_ENV", "development").lower()

    # 数据库
    database_url: str = _env("DATABASE_URL", "postgresql://datahub:datahub@localhost:5434/vertu_data_hub")

    # 文本 embedding
    embedding_provider: str = _env("EMBEDDING_PROVIDER", "hash")
    embedding_base_url: str = _env("EMBEDDING_BASE_URL")
    embedding_api_key: str = _env("EMBEDDING_API_KEY")
    embedding_model: str = _env("EMBEDDING_MODEL", "text-embedding-v3")
    embedding_dim: int = int(_env("EMBEDDING_DIM", "1024"))
    embedding_timeout_seconds: float = float(_env("EMBEDDING_TIMEOUT_SECONDS", "10"))

    # 图片 embedding
    image_embedding_provider: str = _env("IMAGE_EMBEDDING_PROVIDER", "hash")
    image_embedding_base_url: str = _env("IMAGE_EMBEDDING_BASE_URL")
    image_embedding_api_key: str = _env("IMAGE_EMBEDDING_API_KEY")
    image_embedding_model: str = _env("IMAGE_EMBEDDING_MODEL", "multimodal-embedding-v1")
    image_embedding_dim: int = int(_env("IMAGE_EMBEDDING_DIM", "1024"))
    allow_external_image_processing: bool = _env(
        "ALLOW_EXTERNAL_IMAGE_PROCESSING", "false"
    ).lower() in {"1", "true", "yes"}

    # 本地中文图文语义检索。模型版本在实现中固定，原图不离开本机/私有部署环境。
    semantic_image_batch_size: int = int(_env("SEMANTIC_IMAGE_BATCH_SIZE", "4"))
    semantic_image_preload: bool = _env("SEMANTIC_IMAGE_PRELOAD", "true").lower() in {
        "1", "true", "yes"
    }

    # 本地音视频转写与关键帧。
    media_transcription_model: str = _env("MEDIA_TRANSCRIPTION_MODEL", "small")
    media_transcription_model_revision: str = _env(
        "MEDIA_TRANSCRIPTION_MODEL_REVISION", "536b0662742c02347bc0e980a01041f333bce120"
    )
    media_transcription_compute_type: str = _env("MEDIA_TRANSCRIPTION_COMPUTE_TYPE", "int8")
    media_keyframe_interval_seconds: int = int(_env("MEDIA_KEYFRAME_INTERVAL_SECONDS", "30"))
    media_max_keyframes: int = int(_env("MEDIA_MAX_KEYFRAMES", "60"))

    # 有引用回答。默认禁用外发，显式开启后只发送脱敏 internal 证据。
    answer_provider: str = _env("ANSWER_PROVIDER", "disabled").lower()
    allow_external_text_generation: bool = _env(
        "ALLOW_EXTERNAL_TEXT_GENERATION", "false"
    ).lower() in {"1", "true", "yes"}
    openrouter_api_key: str = _env("OPENROUTER_API_KEY")
    openrouter_base_url: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_model: str = _env("OPENROUTER_MODEL", "openai/gpt-4.1-mini")
    openrouter_http_referer: str = _env("OPENROUTER_HTTP_REFERER")
    openrouter_app_title: str = _env("OPENROUTER_APP_TITLE", "Vertu Dealer Knowledge")
    deepseek_api_key: str = _env("DEEPSEEK_API_KEY")
    deepseek_model: str = _env("DEEPSEEK_MODEL", "deepseek-chat")
    answer_min_semantic_similarity: float = float(
        _env("ANSWER_MIN_SEMANTIC_SIMILARITY", "0.35")
    )

    # 文件类数据源监听根目录
    watched_root: str = _env("WATCHED_ROOT", r"D:\vertu-agent-数据待处理")

    # OSS 原文件信箱（生产）
    oss_access_key_id: str = _env("OSS_ACCESS_KEY_ID")
    oss_access_key_secret: str = _env("OSS_ACCESS_KEY_SECRET")
    oss_endpoint: str = _env("OSS_ENDPOINT")
    oss_bucket: str = _env("OSS_BUCKET")
    oss_signed_url_seconds: int = int(_env("OSS_SIGNED_URL_SECONDS", "900"))

    # PDCA 私有 API 服务令牌。生产只允许密钥文件，开发可用内联 secret。
    service_token_issuer: str = _env("SERVICE_TOKEN_ISSUER", "pdca-workbench")
    service_token_audience: str = _env("SERVICE_TOKEN_AUDIENCE", "dealer-knowledge-hub")
    service_token_key_file: str = _env("SERVICE_TOKEN_KEY_FILE")
    service_token_secret: str = _env("SERVICE_TOKEN_SECRET")

    # Redis 只传递任务，PostgreSQL 保存权威状态。
    redis_url: str = _env("REDIS_URL", "redis://localhost:6380/0")

    # vertu-cli（skill 取数）
    vertu_cli_bin: str = _env("VERTU_CLI_BIN", "vertu-cli")

    # Production Compose passes its immutable image reference for fail-closed validation.
    data_hub_image: str = _env("DATA_HUB_IMAGE")


settings = Settings()


def validate_production_settings(value: Settings = settings) -> None:
    if value.app_env not in {"development", "staging", "production"}:
        raise RuntimeError("APP_ENV must be development, staging, or production")
    signed_seconds = getattr(value, "oss_signed_url_seconds", 900)
    if not 60 <= signed_seconds <= 3600:
        raise RuntimeError("OSS_SIGNED_URL_SECONDS must be between 60 and 3600")
    if not 1 <= getattr(value, "semantic_image_batch_size", 4) <= 32:
        raise RuntimeError("SEMANTIC_IMAGE_BATCH_SIZE must be between 1 and 32")
    if not 1 <= getattr(value, "embedding_timeout_seconds", 10) <= 60:
        raise RuntimeError("EMBEDDING_TIMEOUT_SECONDS must be between 1 and 60")
    if not 5 <= getattr(value, "media_keyframe_interval_seconds", 30) <= 600:
        raise RuntimeError("MEDIA_KEYFRAME_INTERVAL_SECONDS must be between 5 and 600")
    if not 1 <= getattr(value, "media_max_keyframes", 60) <= 300:
        raise RuntimeError("MEDIA_MAX_KEYFRAMES must be between 1 and 300")
    if value.app_env != "production":
        return

    errors = []
    image = getattr(value, "data_hub_image", "")
    digest_image = re.search(r"@sha256:[0-9a-f]{64}$", image)
    commit_tagged_image = re.search(r":[0-9a-f]{7,40}$", image)
    if not (digest_image or commit_tagged_image):
        errors.append("DATA_HUB_IMAGE must use a sha256 digest or commit tag")
    db_host = (urlsplit(value.database_url).hostname or "").lower()
    if db_host in {"", "localhost", "127.0.0.1", "db"}:
        errors.append("DATABASE_URL must point to the production PostgreSQL host")
    for name in ("oss_access_key_id", "oss_access_key_secret", "oss_endpoint", "oss_bucket"):
        if not getattr(value, name):
            errors.append(f"{name.upper()} is required")
    token_key_file = getattr(value, "service_token_key_file", "")
    if not token_key_file:
        errors.append("SERVICE_TOKEN_KEY_FILE is required")
    else:
        try:
            token_key = Path(token_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            errors.append("SERVICE_TOKEN_KEY_FILE must be a readable file")
        else:
            if len(token_key.encode("utf-8")) < 32:
                errors.append("SERVICE_TOKEN_KEY_FILE must contain at least 32 bytes")
    if getattr(value, "service_token_secret", ""):
        errors.append("SERVICE_TOKEN_SECRET is forbidden in production")
    redis_host = (urlsplit(getattr(value, "redis_url", "")).hostname or "").lower()
    if redis_host in {"", "localhost", "127.0.0.1"}:
        errors.append("REDIS_URL must point to the production Redis host")
    if (
        value.embedding_provider != "api"
        or not value.embedding_base_url
        or not value.embedding_api_key
    ):
        errors.append("production text embedding API is required")
    allow_external_images = getattr(value, "allow_external_image_processing", False)
    if allow_external_images:
        if (
            value.image_embedding_provider != "api"
            or not value.image_embedding_base_url
            or not value.image_embedding_api_key
        ):
            errors.append("external image processing requires the image embedding API")
    elif value.image_embedding_provider != "hash":
        errors.append("IMAGE_EMBEDDING_PROVIDER must be hash when external image processing is disabled")
    if getattr(value, "allow_external_text_generation", False):
        provider = getattr(value, "answer_provider", "disabled")
        if provider not in {"openrouter", "deepseek"}:
            errors.append("external text generation requires ANSWER_PROVIDER=openrouter or deepseek")
        elif provider == "openrouter":
            if not getattr(value, "openrouter_api_key", ""):
                errors.append("OPENROUTER_API_KEY is required for external text generation")
            if not getattr(value, "openrouter_model", ""):
                errors.append("OPENROUTER_MODEL is required for external text generation")
        elif provider == "deepseek":
            if not getattr(value, "deepseek_api_key", ""):
                errors.append("DEEPSEEK_API_KEY is required for external text generation")
            if not getattr(value, "deepseek_model", ""):
                errors.append("DEEPSEEK_MODEL is required for external text generation")
    if errors:
        raise RuntimeError("invalid production configuration: " + "; ".join(errors))
