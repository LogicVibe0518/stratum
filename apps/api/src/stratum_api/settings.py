"""Configuration, read once at startup so a missing value fails loudly and early."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    # The publishable key. Deliberately NOT the service-role key: this process never
    # holds a credential that can read across agencies, so a leaked log line or a
    # screenshotted environment cannot expose another client's photos. Every query
    # runs as the signed-in user and row-level security does the rest.
    supabase_key: str = Field(default="", alias="SUPABASE_ANON_KEY")

    bucket: str = "stratum"
    repo_root: Path = Path.cwd()
    asset_cache: Path = Path(".stratum/assets")
    templates_dir: Path = Path("templates")
    agency_id: str = Field(default="", alias="STRATUM_AGENCY_ID")

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def storage_url(self) -> str:
        return f"{self.supabase_url}/storage/v1"

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


@lru_cache
def settings() -> Settings:
    return Settings()
