"""Runtime configuration, loaded from the environment and ``.env``."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Project settings; ``APIKEY`` comes from ``.env``."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    apikey: str
    base_url: str = "https://emono.unibas.ch/stadtgeschichtebasel/api/v1"
    pdf_dir: Path = Path("pdf")
    html_dir: Path = Path("html")
