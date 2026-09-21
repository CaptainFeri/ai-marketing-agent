"""Application settings.

Everything is read from the environment (see ``.env.example``).  Nothing here
reaches outside the server: per decision D1 the platform calls no external
model APIs, so the only outbound endpoints are the channel connectors added in
phase 1 and later.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Obvious placeholder: long enough for HMAC-SHA256 but refused outside local/test.
DEV_SECRET_KEY = "insecure-development-secret-key-do-not-use-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- general -------------------------------------------------------
    environment: Literal["local", "staging", "production", "test"] = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    project_name: str = "AI Marketing Agent"

    # ---- database ------------------------------------------------------
    # ``database_url`` is the *tenant-scoped* connection.  The role behind it
    # must NOT have BYPASSRLS, otherwise row level security is decorative.
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+psycopg://app:app@localhost:5432/ai_marketing"),
    )
    # ``system_database_url`` is used only by cross-tenant machinery (the GPU
    # scheduler, nightly maintenance, migrations).  Its role holds BYPASSRLS.
    system_database_url: PostgresDsn | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # ---- redis / celery ------------------------------------------------
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    celery_broker_url: RedisDsn | None = None
    celery_result_backend: RedisDsn | None = None

    # ---- auth ----------------------------------------------------------
    secret_key: str = Field(default=DEV_SECRET_KEY, min_length=32)
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 14
    jwt_algorithm: str = "HS256"

    # ---- credential encryption (channel tokens, D8) --------------------
    # Fernet key, urlsafe-base64 32 bytes.  Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str | None = None
    credential_key_version: int = 1

    # ---- storage (MinIO, one prefix per tenant) ------------------------
    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ai-marketing"
    s3_region: str = "us-east-1"
    s3_secure: bool = False
    # "memory" runs the platform with no MinIO present — phase 0/1 default,
    # same idea as GPU_RUNTIME=simulated and LLM_CLIENT=simulated.
    storage_backend: str = "memory"

    # ---- GPU capacity (section 6 & 7 of the handoff) -------------------
    # Seconds of GPU time the scheduler may hand out per day.  20h by default;
    # the remaining 4h are head-room for model swaps and maintenance.
    gpu_daily_capacity_seconds: int = 20 * 3600
    # A batch keeps the GPU on one model for at most this long before the
    # scheduler is allowed to reconsider the window.
    gpu_max_batch_seconds: int = 900
    gpu_max_batch_size: int = 8
    # If the idle window has been waiting longer than this, switch to it even
    # though switching costs a model reload.  Prevents starvation.
    gpu_window_starvation_seconds: int = 1800
    # Measured in phase 0 and overwritten by the real number.
    gpu_window_switch_seconds: int = 45
    # Heavy video work only runs inside this window (local server time).
    gpu_night_window_start_hour: int = 1
    gpu_night_window_end_hour: int = 7
    # Turned off when the card has too little VRAM for Wan 2.2, so nothing
    # queues work that can never run.  See app/services/tuning.py.
    gpu_nightly_video_enabled: bool = True
    # How long a leased job may stay silent before the lease is reclaimed.
    gpu_lease_timeout_seconds: int = 3600

    # "simulated" runs the whole platform with no model weights present, which
    # is what phase 0 and phase 1 development use.  Any other value expects a
    # real runtime and fails loudly until phase 0 delivers one.
    gpu_runtime: str = "simulated"
    # How much faster than real time the simulator runs.
    gpu_simulation_speedup: float = 600.0

    # Worker processes on the CPU queues.  The GPU queue is always 1.
    celery_cpu_concurrency: int = 4

    # ---- image overlay (handoff section 5: FLUX cannot render fa/ar script,
    # so text is drawn separately with Playwright and composited on top) -----
    # Left unset in production: the Docker image runs `playwright install
    # chromium` at build time and the bundled browser resolves on its own.
    # Set only to point at a browser in a nonstandard location (a dev sandbox
    # with a pre-installed Chromium at a version-specific path).
    playwright_executable_path: str | None = None
    overlay_render_timeout_seconds: float = 15.0
    # "simulated" produces a deterministic placeholder in place of
    # FLUX.1-schnell; phase 0 delivers the ComfyUI workflow this switches to.
    image_backend: str = "simulated"

    # ---- voice (handoff section 8: video_mode "voice" — Piper for fa,
    # Chatterbox for en/ar) -------------------------------------------------
    # "espeak" is real, offline speech synthesis via the system's espeak-ng —
    # lower fidelity than Piper/Chatterbox but needs no downloaded model
    # weights, so it produces genuine audio today. "production" routes to
    # Piper/Chatterbox and fails loudly until phase 0 delivers their voice
    # models (handoff section 11).
    tts_backend: str = "espeak"
    espeak_binary: str = "espeak-ng"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"

    # ---- language model (decision D1: self-hosted, nothing leaves here) --
    # "vllm" talks to a local vLLM server; "simulated" runs the pipeline with
    # contract-valid output and no weights, which is what phase 0/1 use.
    llm_client: str = "simulated"
    llm_base_url: str = "http://localhost:8001"
    llm_model: str = "Qwen/Qwen3-30B-A3B"
    llm_timeout_seconds: float = 600.0
    # How vLLM is asked to constrain generation. Recent builds take the
    # OpenAI-style "response_format"; older ones want "guided_json". Getting
    # this wrong means unconstrained generation, which fails further away.
    llm_guided_mode: str = "response_format"
    # An agent gets this many tries before the step is marked failed; each
    # retry carries the validation errors back into the prompt.
    agent_max_attempts: int = 3

    # ---- pipeline ------------------------------------------------------
    qa_max_retries: int = 2
    supported_locales: list[str] = ["fa", "en", "ar"]
    default_locale: str = "fa"

    # ---- observability -------------------------------------------------
    langfuse_host: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    log_level: str = "INFO"
    log_json: bool = True

    # ---- backups (handoff section 7, week 9: daily Postgres + MinIO) ---
    backup_dir: str = "./backups"
    backup_retention_days: int = 14
    pg_dump_binary: str = "pg_dump"

    @field_validator("supported_locales", mode="before")
    @classmethod
    def _split_locales(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _refuse_dev_defaults_in_production(self) -> Settings:
        if self.environment in {"staging", "production"}:
            problems = []
            if self.secret_key == DEV_SECRET_KEY:
                problems.append("SECRET_KEY is still the development placeholder")
            if not self.credential_encryption_key:
                problems.append("CREDENTIAL_ENCRYPTION_KEY is not set")
            if self.debug:
                problems.append("DEBUG must be off")
            if problems:
                raise ValueError(
                    f"unsafe configuration for environment={self.environment}: "
                    + "; ".join(problems)
                )
        return self

    @property
    def broker_url(self) -> str:
        return str(self.celery_broker_url or self.redis_url)

    @property
    def result_backend(self) -> str:
        return str(self.celery_result_backend or self.redis_url)

    @property
    def effective_system_database_url(self) -> str:
        return str(self.system_database_url or self.database_url)

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
