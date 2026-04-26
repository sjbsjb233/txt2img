from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    JWT_SECRET: str = "dev-insecure-secret-change-me"
    JWT_EXPIRES_DAYS: int = 3650
    JWT_ALGORITHM: str = "HS256"

    DB_URL: str = "sqlite:///./txt2img.db"

    ADMIN_USERNAME: str | None = None
    ADMIN_PASSWORD: str | None = None

    FORCE_CAPTCHA: bool = False

    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
