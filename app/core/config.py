"""Application settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CampusCaption"
    environment: str = "development"  # development | production

    # --- Live captions / speech-to-text ---------------------------------------
    stt_default_engine: str = "local"          # local | openai | mistral
    stt_local_model: str = "parakeet"          # parakeet | whisper-turbo | whisper-small
    stt_default_language: str = "de"         # auto | de | en | ...
    stt_model_dir: str = "./data/models"       # downloaded model files live here (mount as a volume in Docker)
    stt_device: str = "auto"                   # Whisper only: auto | cpu | cuda
    stt_threads: int = 0                       # CPU threads for local inference, 0 = automatic
    stt_preload: bool = True                   # load the default local model at startup
    stt_max_sessions: int = 4                  # simultaneously open caption sessions
    stt_session_retention_hours: int = 12      # how long a finished session stays readable
    openai_api_key: str = ""                   # enables "Cloud Whisper (OpenAI)"
    openai_stt_model: str = "gpt-realtime-whisper"
    mistral_api_key: str = ""                  # enables "Cloud Voxtral (Mistral)"
    mistral_stt_model: str = "voxtral-mini-transcribe-realtime-3"
    mistral_stt_delay_ms: int = 480            # 80-1200 or 2400: lower = faster, higher = more accurate
    anthropic_api_key: str = ""                # enables the optional Claude clean-up of captions
    claude_model: str = "claude-haiku-5-5"

    @property
    def resolved_threads(self) -> int:
        import os

        return self.stt_threads if self.stt_threads > 0 else max(1, min(8, os.cpu_count() or 2))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
