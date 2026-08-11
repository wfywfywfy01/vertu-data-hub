"""环境变量配置。全部配置从 .env / 环境变量读取，代码中不出现明文密钥。"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class Settings:
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

    # vertu-cli（skill 取数）
    vertu_cli_bin: str = _env("VERTU_CLI_BIN", "vertu-cli")


settings = Settings()
