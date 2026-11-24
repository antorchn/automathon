"""Configuration centralisée de l'arène en ligne (paramètres V0 et secrets)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Réseau, proxy et secrets
    public_url: str = Field(default="http://localhost:8001/arene", alias="PUBLIC_URL")
    arene_base_path: str = Field(default="/arene", alias="ARENE_BASE_PATH")
    arene_port: int = Field(default=8001, alias="ARENE_PORT")
    trusted_proxy: str = Field(default="127.0.0.1", alias="TRUSTED_PROXY")
    secret_key: str = Field(default="dev-insecure-secret-change-me", alias="SECRET_KEY")

    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")
    auth_dev_mode: bool = Field(default=False, alias="AUTH_DEV_MODE")
    admin_github_logins: str = Field(default="", alias="ADMIN_GITHUB_LOGINS")

    worker_token: str = Field(default="dev-worker-token-change-me", alias="WORKER_TOKEN")
    database_url: str = Field(default="sqlite:////data/arene.db", alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("/data"), alias="DATA_DIR")
    webgl_dir: Path = Field(default=Path("/app/webgl"), alias="ARENE_WEBGL_DIR")
    reference_agents_dir: Path = Field(default=Path("/app/reference_agents"), alias="ARENE_REF_AGENTS_DIR")

    # Paramètres V0 (vr-4v-4g par défaut)
    placement_opponents: int = Field(default=5, alias="PLACEMENT_OPPONENTS")
    placement_maps_per_opponent: int = Field(default=10, alias="PLACEMENT_MAPS_PER_OPPONENT")
    validation_games: int = Field(default=2, alias="VALIDATION_GAMES")
    background_enabled: bool = Field(default=True, alias="BACKGROUND_ENABLED")
    background_maps_per_job: int = Field(default=1, alias="BACKGROUND_MAPS_PER_JOB")
    background_duty: int = Field(default=50, alias="BACKGROUND_DUTY")
    background_target_margin: float = Field(default=50.0, alias="BACKGROUND_TARGET_MARGIN")
    max_games_per_pair: int = Field(default=100, alias="MAX_GAMES_PER_PAIR")
    max_placements_in_progress: int = Field(default=1, alias="MAX_PLACEMENTS_IN_PROGRESS")

    decision_mean_ms_limit: float = Field(default=5.0, alias="DECISION_MEAN_MS_LIMIT")
    decision_timeout_ms: int = Field(default=500, alias="DECISION_TIMEOUT_MS")
    max_exceptions_per_game: int = Field(default=50, alias="MAX_EXCEPTIONS_PER_GAME")
    agent_load_timeout_s: int = Field(default=60, alias="AGENT_LOAD_TIMEOUT_S")
    agent_memory_mb: int = Field(default=768, alias="AGENT_MEMORY_MB")
    max_frames: int = Field(default=4200, alias="MAX_FRAMES")

    submission_max_mb: int = Field(default=50, alias="SUBMISSION_MAX_MB")
    cooldown_minutes: int = Field(default=30, alias="COOLDOWN_MINUTES")
    queue_max: int = Field(default=20, alias="QUEUE_MAX")
    job_lease_seconds: int = Field(default=600, alias="JOB_LEASE_SECONDS")
    provisional_margin: float = Field(default=75.0, alias="PROVISIONAL_MARGIN")
    anchor_agent_name: str = Field(default="rush_bot", alias="ANCHOR_AGENT_NAME")
    anchor_elo: float = Field(default=1000.0, alias="ANCHOR_ELO")
    replays_kept_per_pair: int = Field(default=2, alias="REPLAYS_KEPT_PER_PAIR")
    replay_retention_days: int = Field(default=14, alias="REPLAY_RETENTION_DAYS")
    accounts_per_ip_per_day: int = Field(default=3, alias="ACCOUNTS_PER_IP_PER_DAY")
    game_version: str = Field(default="1.0.0", alias="GAME_VERSION")

    @field_validator("arene_base_path", mode="before")
    @classmethod
    def _normalize_base_path(cls, value: str) -> str:
        s = (value or "").strip()
        if not s or s == "/":
            return ""
        if not s.startswith("/"):
            s = "/" + s
        return s.rstrip("/")

    @field_validator("public_url", mode="before")
    @classmethod
    def _normalize_public_url(cls, value: str) -> str:
        return (value or "").strip().rstrip("/")

    @model_validator(mode="after")
    def _validate_auth_dev_mode(self) -> "Settings":
        # Spec : "L'arène refuse de démarrer avec true si PUBLIC_URL est en HTTPS."
        if self.auth_dev_mode and self.public_url.lower().startswith("https://"):
            raise ValueError("AUTH_DEV_MODE=true est interdit lorsque PUBLIC_URL utilise HTTPS.")
        return self

    @property
    def base_path(self) -> str:
        return self.arene_base_path

    @property
    def cookie_path(self) -> str:
        return self.arene_base_path if self.arene_base_path else "/"

    @property
    def cookie_secure(self) -> bool:
        return self.public_url.lower().startswith("https://")

    @property
    def trusted_proxies(self) -> set[str]:
        return {item.strip() for item in self.trusted_proxy.split(",") if item.strip()}

    @property
    def admin_logins_set(self) -> set[str]:
        return {item.strip().lower() for item in self.admin_github_logins.split(",") if item.strip()}

    def url(self, path: str) -> str:
        """Construit un chemin interne préfixé par ARENE_BASE_PATH."""
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_path}{path}"
