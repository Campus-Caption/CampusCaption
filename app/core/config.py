"""Application settings, loaded from environment variables / .env."""
import socket
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

_ANY_ADDRESS = ("0.0.0.0", "::")


def is_loopback(host: str) -> bool:
    """True for addresses only this computer can reach (127.x, ::1, localhost)."""
    h = (host or "").strip().strip("[]").lower()
    return h == "localhost" or h == "::1" or h.startswith("127.")


def lan_ip() -> str | None:
    """This computer's address on the local network. Connecting a UDP socket sends no packet; it only picks the
    interface the OS would route through."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if is_loopback(ip) or ip in _ANY_ADDRESS else ip


def url_is_loopback(url: str) -> bool:
    return not url or is_loopback(urlsplit(url).hostname or "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "CampusCaption"
    environment: str = "development"  # development | production

    # Shared secret for the lecturer controls (start / settings / clear / end / export / microphone stream).
    # Empty = no protection, fine on 127.0.0.1 only. Set it whenever other people can reach the server.
    lecturer_secret: str = ""

    # --- Network (python main.py) -----------------------------------------------
    # 127.0.0.1 = only this computer. 0.0.0.0 = students' phones on the same network can join (needs LECTURER_SECRET).
    host: str = "127.0.0.1"
    port: int = 8080
    # Address students open (join link and QR code), e.g. http://192.168.1.20:8080. Empty = detected automatically.
    public_url: str = ""

    # --- Live captions / speech-to-text ---------------------------------------
    stt_local_model: str = "parakeet"          # nemotron | parakeet | whisper-turbo | whisper-small | voxtral
    stt_default_language: str = "de"         # auto | de | en | ...
    stt_model_dir: str = "./data/models"       # downloaded model files live here (mount as a volume in Docker)
    stt_device: str = "auto"                   # Whisper only: auto | cpu | cuda
    stt_threads: int = 0                       # CPU threads for local inference, 0 = automatic
    stt_preload: bool = True                   # load the default local model at startup
    stt_max_sessions: int = 4                  # simultaneously open caption sessions
    stt_session_retention_hours: int = 12      # how long a finished session stays readable
    anthropic_api_key: str = ""                # enables the optional Claude clean-up of captions
    claude_model: str = "claude-haiku-5-5"
    claude_analysis_model: str = "claude-opus-5-5"  # end-of-lecture analysis for the professor

    @property
    def resolved_threads(self) -> int:
        import os

        return self.stt_threads if self.stt_threads > 0 else max(1, min(8, os.cpu_count() or 2))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def bind_error(self) -> str | None:
        """Why the server must not start with these settings: reachable from the network but unprotected."""
        if is_loopback(self.host) or self.lecturer_secret:
            return None
        return (f"HOST={self.host} lets other devices reach this server, but LECTURER_SECRET is empty, so anyone "
                "on the network could start, change and end the captions. Set LECTURER_SECRET in .env, "
                "or use HOST=127.0.0.1.")

    def public_base_url(self) -> str:
        """Base address for the students' join link, without a trailing slash. Empty if only this computer can
        reach the server and nothing was configured (callers then fall back to the address of the request)."""
        if self.public_url.strip():
            return self.public_url.strip().rstrip("/")
        if is_loopback(self.host):
            return ""
        host = lan_ip() if self.host in _ANY_ADDRESS else self.host
        if not host:
            return ""
        return f"http://{f'[{host}]' if ':' in host else host}:{self.port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
