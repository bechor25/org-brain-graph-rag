"""Runtime settings. Read from environment / .env; never hardcode secrets elsewhere."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "brainpass"
    neo4j_database: str = "neo4j"

    ollama_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024

    data_dir: Path = Path("data")

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def canonical_dir(self) -> Path:
        return self.data_dir / "canonical"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
