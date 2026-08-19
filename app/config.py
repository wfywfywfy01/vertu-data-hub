"""环境变量配置。全部配置从 .env / 环境变量读取，代码中不出现明文密钥。"""
import os
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

    # 图片 embedding
    image_embedding_provider: str = _env("IMAGE_EMBEDDING_PROVIDER", "hash")
    image_embedding_base_url: str = _env("IMAGE_EMBEDDING_BASE_URL")
    image_embedding_api_key: str = _env("IMAGE_EMBEDDING_API_KEY")
    image_embedding_model: str = _env("IMAGE_EMBEDDING_MODEL", "multimodal-embedding-v1")
    image_embedding_dim: int = int(_env("IMAGE_EMBEDDING_DIM", "1024"))

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


settings = Settings()


def validate_production_settings(value: Settings = settings) -> None:
    if value.app_env not in {"development", "staging", "production"}:
        raise RuntimeError("APP_ENV must be development, staging, or production")
    signed_seconds = getattr(value, "oss_signed_url_seconds", 900)
    if not 60 <= signed_seconds <= 3600:
        raise RuntimeError("OSS_SIGNED_URL_SECONDS must be between 60 and 3600")
    if value.app_env != "production":
        return

    errors = []
    db_host = (urlsplit(value.database_url).hostname or "").lower()
    if db_host in {"", "localhost", "127.0.0.1", "db"}:
        errors.append("DATABASE_URL must point to the production PostgreSQL host")
    for name in ("oss_access_key_id", "oss_access_key_secret", "oss_endpoint", "oss_bucket"):
        if not getattr(value, name):
            errors.append(f"{name.upper()} is required")
    if not getattr(value, "service_token_key_file", ""):
        errors.append("SERVICE_TOKEN_KEY_FILE is required")
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
    if (
        value.image_embedding_provider != "api"
        or not value.image_embedding_base_url
        or not value.image_embedding_api_key
    ):
        errors.append("production image embedding API is required")
    if errors:
        raise RuntimeError("invalid production configuration: " + "; ".join(errors))
