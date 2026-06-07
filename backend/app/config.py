import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "60"))
    stale_cache_seconds: int = int(os.getenv("STALE_CACHE_SECONDS", "900"))
    nifty_lot_size: int = int(os.getenv("NIFTY_LOT_SIZE", "65"))
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    cors_origins: tuple[str, ...] = tuple(
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,https://options.trading-simplified.com",
        ).split(",")
        if origin.strip()
    )


settings = Settings()
