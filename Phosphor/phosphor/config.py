"""Runtime configuration, loaded from environment / .env with the PHOSPHOR_ prefix."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PHOSPHOR_",
        env_file=(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Identity ────────────────────────────────────────────────────────────
    secret_key: str = "change-me-please-use-a-real-random-secret"
    primary_domain: str = "example.com"
    server_hostname: str = "mail.example.com"
    public_url: str = "http://localhost:8000"

    # ── Network ─────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    smtp_inbound_host: str = "0.0.0.0"
    smtp_inbound_port: int = 25
    smtp_submission_port: int = 587

    # ── Sending policy ──────────────────────────────────────────────────────
    outbound_enabled: bool = True
    max_send_per_hour: int = 60
    dkim_selector: str = "phosphor"
    max_message_bytes: int = 25 * 1024 * 1024

    # ── TLS (SMTP listeners) ────────────────────────────────────────────────
    tls_cert_file: str = ""
    tls_key_file: str = ""

    # ── Auth ────────────────────────────────────────────────────────────────
    access_token_ttl_minutes: int = 60 * 24
    jwt_algorithm: str = "HS256"

    # ── CORS ────────────────────────────────────────────────────────────────
    # Comma-separated origins allowed to call the API (the KlickMail client
    # runs from its own origin). "*" allows any — fine because the API is
    # bearer-token only and sets no cookies.
    cors_origins: str = "*"

    # ── Storage ─────────────────────────────────────────────────────────────
    data_dir: Path = Field(default=DEFAULT_DATA_DIR)
    database_url: str = ""

    # ── Derived paths ───────────────────────────────────────────────────────
    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'phosphor.db').as_posix()}"

    @property
    def maildir_root(self) -> Path:
        return self.data_dir / "maildirs"

    @property
    def spool_root(self) -> Path:
        """Where raw outbound .eml files wait for the delivery worker."""
        return self.data_dir / "outbound_spool"

    @property
    def dkim_key_root(self) -> Path:
        return self.data_dir / "dkim"

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert_file and self.tls_key_file
                    and Path(self.tls_cert_file).exists()
                    and Path(self.tls_key_file).exists())

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.maildir_root, self.spool_root, self.dkim_key_root):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
