import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass(frozen=True)
class Settings:
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "60"))
    stale_cache_seconds: int = int(os.getenv("STALE_CACHE_SECONDS", "900"))
    nifty_lot_size: int = int(os.getenv("NIFTY_LOT_SIZE", "65"))
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    gemini_analysis_mode: str = os.getenv("GEMINI_ANALYSIS_MODE", "quality")
    gemini_quality_model: str = os.getenv("GEMINI_QUALITY_MODEL", "gemini-3.5-flash")
    gemini_fast_model: str = os.getenv("GEMINI_FAST_MODEL", "gemini-2.5-flash-lite")
    gemini_timeout_seconds: int = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "180"))
    gemini_thinking_level: str = os.getenv("GEMINI_THINKING_LEVEL", "low")
    market_events_json: str = os.getenv("MARKET_EVENTS_JSON", "[]")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,https://options.trading-simplified.com",
        ).split(",")
        if origin.strip()
    )


settings = Settings()
