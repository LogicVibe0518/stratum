"""Configuration, read once at startup so a missing value fails loudly and early."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Read .env into the real environment, not just into this Settings object.
#
# Settings picks up SUPABASE_* from the file either way, but the AI gateway reads
# os.environ directly - so starting with `uvicorn` instead of `make live` gave a
# server that signed people in, drafted nothing, and only admitted there were no
# model providers when somebody typed a tweak. Loading here fixes it for everyone.
load_dotenv(override=False)


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
    # A shared demo account anyone can open from the sign-in page (POST /api/demo), so a
    # link on a CV works without a password. Off unless both are set. The password stays
    # on the server: a visitor's browser only ever gets that account's session.
    demo_email: str = Field(default="", alias="DEMO_EMAIL")
    demo_password: SecretStr = Field(default=SecretStr(""), alias="DEMO_PASSWORD")

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url}/rest/v1"

    @property
    def storage_url(self) -> str:
        return f"{self.supabase_url}/storage/v1"

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)

    @property
    def demo_on(self) -> bool:
        return bool(self.configured and self.demo_email and self.demo_password.get_secret_value())


@lru_cache
def settings() -> Settings:
    return Settings()
