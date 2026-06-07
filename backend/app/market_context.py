from __future__ import annotations

from datetime import datetime, timezone
from statistics import pstdev

import httpx

from .models import MarketContext
from .provider import TimedCache


class MarketContextService:
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/%5ENSEI"
        "?range=6mo&interval=1d&events=history"
    )

    def __init__(self) -> None:
        self.cache = TimedCache()

    @staticmethod
    def _trend(last: float, average: float, tolerance: float = 0.005) -> str:
        difference = last / average - 1
        if difference > tolerance:
            return "Bullish"
        if difference < -tolerance:
            return "Bearish"
        return "Sideways"

    @staticmethod
    def _context(closes: list[float], timestamp: str) -> MarketContext:
        if len(closes) < 50:
            raise ValueError("Insufficient NIFTY history")
        last = closes[-1]
        returns = [
            closes[index] / closes[index - 1] - 1
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]
        short_average = sum(closes[-20:]) / 20
        medium_window = closes[-50:]
        medium_average = sum(medium_window) / len(medium_window)
        five_day_change = last / closes[-6] - 1 if len(closes) >= 6 else 0
        annualized_volatility = pstdev(returns[-20:]) * (252**0.5) * 100
        if five_day_change > 0.01:
            momentum = "Positive"
        elif five_day_change < -0.01:
            momentum = "Negative"
        else:
            momentum = "Neutral"
        if annualized_volatility >= 22:
            volatility = "High"
        elif annualized_volatility <= 12:
            volatility = "Low"
        else:
            volatility = "Normal"
        return MarketContext(
            short_term_trend=MarketContextService._trend(last, short_average),
            medium_term_trend=MarketContextService._trend(last, medium_average),
            momentum=f"{momentum} ({five_day_change * 100:.2f}% over five sessions)",
            volatility_regime=(
                f"{volatility} ({annualized_volatility:.1f}% annualized "
                "20-session realized volatility)"
            ),
            sources=["Yahoo Finance chart data for ^NSEI"],
            data_timestamp=timestamp,
            stale=False,
        )

    def get(self, force: bool = False) -> MarketContext:
        if not force:
            cached = self.cache.get("nifty-context", 900)
            if cached:
                return cached
        try:
            response = httpx.get(
                self.url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            raw_closes = result["indicators"]["quote"][0]["close"]
            closes = [float(value) for value in raw_closes if value is not None]
            market_timestamp = result.get("meta", {}).get("regularMarketTime")
            timestamp = (
                datetime.fromtimestamp(market_timestamp, tz=timezone.utc).isoformat()
                if market_timestamp
                else datetime.now(timezone.utc).isoformat()
            )
            context = self._context(closes, timestamp)
            self.cache.set("nifty-context", context)
            return context
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            stale = self.cache.get("nifty-context", 86400)
            if stale:
                return stale.model_copy(update={"stale": True})
            return MarketContext(
                sources=["NIFTY historical trend source unavailable"],
                data_timestamp=datetime.now(timezone.utc).isoformat(),
                stale=True,
            )


market_context_service = MarketContextService()
