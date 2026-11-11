"""Environment-based configuration. No secrets live in code; see .env.example."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    app_name: str = "Mystery Shopping AI"

    # Database: SQLite for local dev, PostgreSQL (postgresql+psycopg://...) in production.
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'app.db').as_posix()}"

    # Security
    secret_key: str = Field(default="dev-only-change-me-please-use-32+chars-secret", min_length=16)
    access_token_minutes: int = 480
    cookie_name: str = "msai_session"
    cookie_secure: bool = False  # set true behind HTTPS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    min_password_length: int = 10
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # First-run bootstrap of the Super Admin (used only when no users exist).
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Super Admin"

    # Storage (local adapter by default; shared-drive / cloud adapters need approved credentials)
    storage_backend: Literal["local", "shared_drive", "s3"] = "local"
    storage_root: str = str(BACKEND_DIR / "data" / "storage")
    workspace_name: str = "MysteryShop"
    max_upload_mb: int = 25
    max_pages: int = 300

    # OCR
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    tesseract_cmd: str | None = None
    ocr_dpi: int = 200
    min_chars_per_page_for_text: int = 25

    # AI provider adapter. "none" => deterministic checks only; AI status is reported honestly.
    ai_provider: Literal["none", "anthropic", "openai_compatible"] = "none"
    # Confidential PDFs are never sent to an external provider unless this is explicitly enabled
    # (set only after your organisation has approved the provider).
    ai_allow_external: bool = False
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    openai_base_url: str | None = None  # e.g. http://localhost:11434/v1 for a private model
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_is_internal: bool = False  # true when openai_base_url is an approved in-house endpoint
    ai_max_input_chars: int = 60000
    ai_timeout_seconds: float = 120.0
    ai_confidence_review_threshold: float = 0.7

    # One-click login for the seeded SAMPLE accounts. Only ever active when ENV=development, from loopback addresses.
    demo_login_enabled: bool = True
    demo_login_domains: str = ""            # also offer the working team (accounts at these e-mail domains, e.g. "isngs.com") on the quick-access panel
    demo_login_show_samples: bool = True    # false hides the seeded SAMPLE accounts from the panel

    # AI editor: after a RAW upload, automatically produce the edited PDF (grammar/spelling only) and send it to QC.
    ai_edit_on_upload: bool = True

    # Google Drive integration (read-only). Share the Drive folder with the service account's e-mail address.
    google_service_account_file: str | None = None      # path to the service-account JSON key
    google_service_account_json: str | None = None      # or the JSON itself (e.g. from a secrets manager)
    google_drive_folder_id: str | None = None           # default root folder to sync
    import_local_root: str = str(BACKEND_DIR / "data" / "imports")   # folders here can be imported ("local" source)
    max_import_mb: int = 1024

    # Drive auto-sync: a background scheduler polls the Drive folder and imports only new/changed PDFs.
    # These are the defaults; admins can change them at runtime in the UI (stored in the database).
    drive_scheduler_enabled: bool = True          # run the scheduler thread (set false in tests / on secondary nodes)
    qc_reports_auto: bool = True                # keep an always-current Excel QC report of every project on disk
    qc_reports_dir: str | None = None           # default: backend/data/qc-reports
    auto_assign_by_folder_name: bool = False    # false: new Drive projects wait for a coordinator to assign them (the folder name is only suggested)
    import_grant_roles: str = "admin,manager"   # roles that automatically get access to projects created by a Drive import (QC leads get their own folders)
    drive_auto_sync: bool = False                # default for "enabled" when nothing has been saved yet
    drive_sync_interval_minutes: int = 5

    # Review policy
    allow_self_review: bool = False  # independent-review policy: editors cannot approve own work

    # Jobs
    job_workers: int = 2
    jobs_inline: bool = False  # run jobs synchronously (tests)

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        """Hosts (Render, Railway, Heroku) hand out postgres:// or postgresql:// URLs; this app uses the psycopg 3 driver."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def demo_login_active(self) -> bool:
        return self.demo_login_enabled and self.env == "development"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
