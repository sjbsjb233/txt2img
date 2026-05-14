"""Process-level settings loaded from environment variables.

These values are read once at startup. Anything that needs to be hot-reloaded
at runtime (tier weights, scheduler thresholds, soft-penalty knobs, emergency
switches, etc.) lives in the ``config`` table and is owned by
``app.domain.config_center`` instead of this module.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed view of the process environment.

    Required values (``JWT_SECRET``, ``ADMIN_PASSWORD``) have no default; if
    they are missing pydantic-settings raises ``ValidationError`` at startup,
    which makes misconfiguration loud instead of silently booting with weak
    defaults.
    """

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",
    )

    # ===== App =====
    APP_VERSION: str = "v1.0.0"

    # ===== Auth =====
    JWT_SECRET: str
    JWT_EXPIRES_DAYS: int = 3650

    # ===== Database =====
    DB_URL: str = "sqlite+aiosqlite:////app/data/txt2img.db"

    # ===== Bootstrap admin (created on first startup if missing) =====
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str

    # ===== Login / captcha =====
    FORCE_CAPTCHA: bool = False

    # ===== CORS =====
    CORS_ORIGINS: str = "*"

    # ===== Storage =====
    DATA_ROOT: str = "/app/data"

    # ===== Scheduler =====
    SCHEDULER_GLOBAL_MAX_WORKERS: int = 32

    # ===== Turnstile =====
    TURNSTILE_SITE_KEY: str = ""
    TURNSTILE_SECRET: str = ""

    # ===== Task retention / image processing =====
    JOB_RETENTION_DAYS: int = 30
    THUMBNAIL_MAX_LONG_EDGE: int = 720
    THUMBNAIL_QUALITY: int = Field(default=78, ge=1, le=100)

    # ===== SSE =====
    SSE_HEARTBEAT_SECONDS: int = Field(default=15, ge=1, le=300)
    SSE_MAX_CONNECTIONS_PER_USER: int = Field(default=4, ge=1, le=64)

    # ===== Logging =====
    # Root logger level. DEBUG / INFO / WARNING / ERROR.
    LOG_LEVEL: str = "INFO"
    # "json" for structured (grep-friendly) output, "text" for plain.
    LOG_FORMAT: str = "json"
    # Filesystem root where log files are written. Falls under the
    # bind-mounted ``/app/data`` so logs persist across container restarts.
    LOG_DIR: str = "/app/data/logs/backend"
    # Whether to also stream to stdout — keep ``docker compose logs`` useful.
    LOG_TO_STDOUT: bool = True
    # Number of rotated backups to keep for the main app.log.
    LOG_RETAIN_DAYS: int = 30
    # Rotation cadence — passed straight to TimedRotatingFileHandler.
    LOG_ROTATE_WHEN: str = "midnight"
    # Comma-separated list of dict / kwarg field names that must be
    # redacted before they reach disk.
    LOG_REDACT_FIELDS: str = (
        "password,api_key,token,authorization,captcha_token,jwt,secret,"
        "refresh_token,access_token,client_secret"
    )
    # 0 ⇒ do not log request bodies. >0 ⇒ truncate to N bytes.
    LOG_REQUEST_BODY_MAX_BYTES: int = 0
    # Master switches per file.
    LOG_ACCESS_ENABLED: bool = True
    LOG_ADAPTER_ENABLED: bool = True
    LOG_CLIENT_LOGS_ENABLED: bool = True
    # /api/client-logs hard caps to keep the channel from being abused.
    LOG_CLIENT_LOGS_RATE_LIMIT: int = 60       # per user, per minute
    LOG_CLIENT_LOGS_RATE_LIMIT_ANON: int = 30  # per IP, per minute
    LOG_CLIENT_LOGS_BATCH_MAX: int = 50
    LOG_CLIENT_LOGS_ITEM_MAX_BYTES: int = 8 * 1024
    # 0.0 ≤ x ≤ 1.0 — fraction of DEBUG records actually emitted.
    LOG_SAMPLE_DEBUG: float = 0.0
    # When True, the JSON formatter still pretty-prints exception tracebacks.
    LOG_INCLUDE_TRACEBACK: bool = True

    # ===== Batch (frontend / backend doc v0.3) =====
    # Per-submit fan-out parallelism the frontend uses inside one batch.
    BATCH_CONCURRENCY_MAX: int = Field(default=4, ge=1, le=32)
    # Hard ceiling on the user's simultaneous non-terminal batches.
    BATCH_MAX_CONCURRENT_PER_USER: int = Field(default=3, ge=1, le=16)
    # Slot / image guards for ``POST /api/batches`` shape validation.
    BATCH_SLOTS_MAX: int = Field(default=50, ge=1, le=200)
    BATCH_SLOT_IMAGE_COUNT_MAX: int = Field(default=16, ge=1, le=64)
    BATCH_TOTAL_IMAGES_MAX: int = Field(default=400, ge=1, le=4000)
    # Watchdog: a ``submitting`` batch with no Job-bind activity for
    # this many seconds is flipped to ``abandoned`` automatically.
    BATCH_ABANDONED_AFTER_SECONDS: int = Field(default=60, ge=10, le=3600)
    BATCH_WATCHDOG_INTERVAL_SECONDS: int = Field(default=5, ge=1, le=60)
    BATCH_WATCHDOG_ENABLED: bool = True

    @field_validator("JWT_SECRET")
    @classmethod
    def _jwt_secret_long_enough(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError(
                "JWT_SECRET must be at least 16 characters; "
                "generate with `python -c 'import secrets; print(secrets.token_urlsafe(48))'`."
            )
        return v

    @property
    def log_redact_fields_set(self) -> set[str]:
        return {
            s.strip().lower()
            for s in self.LOG_REDACT_FIELDS.split(",")
            if s.strip()
        }

    @property
    def cors_origins_list(self) -> list[str]:
        v = self.CORS_ORIGINS.strip()
        if v == "" or v == "*":
            return ["*"]
        return [s.strip() for s in v.split(",") if s.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide ``Settings`` singleton."""
    return Settings()  # type: ignore[call-arg]
