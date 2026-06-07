from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests

from .config import settings
from .models import ChainRow, ExpiryResponse, OptionChain, OptionQuote


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    @abstractmethod
    def get_expiries(self, force: bool = False) -> ExpiryResponse:
        raise NotImplementedError

    @abstractmethod
    def get_chain(self, expiry: str, force: bool = False) -> OptionChain:
        raise NotImplementedError


class TimedCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, max_age: int) -> Any | None:
        with self._lock:
            item = self._items.get(key)
        if item and time.time() - item[0] <= max_age:
            return item[1]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = (time.time(), value)


class NSEMarketDataProvider(MarketDataProvider):
    base_url = "https://www.nseindia.com"

    def __init__(self) -> None:
        self.cache = TimedCache()
        self.session = requests.Session()
        self.page_url = f"{self.base_url}/option-chain?type=Indices&symbol=NIFTY"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/137.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": self.page_url,
        }

    def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            self.session.get(self.page_url, headers=self.headers, timeout=15).raise_for_status()
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self.headers,
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MarketDataError("NSE returned an unexpected response")
            return payload
        except (requests.RequestException, ValueError) as error:
            raise MarketDataError(f"NSE request failed: {error}") from error

    def get_expiries(self, force: bool = False) -> ExpiryResponse:
        key = "expiries"
        if not force:
            cached = self.cache.get(key, settings.cache_ttl_seconds)
            if cached:
                return cached.model_copy(update={"stale": False})
        try:
            payload = self._request_json(
                "/api/option-chain-contract-info", {"symbol": "NIFTY"}
            )
            expiries = payload.get("expiryDates") or []
            if not expiries:
                raise MarketDataError("NSE returned no NIFTY expiries")
            result = ExpiryResponse(
                expiries=expiries, lot_size=settings.nifty_lot_size
            )
            self.cache.set(key, result)
            return result
        except MarketDataError:
            stale = self.cache.get(key, settings.stale_cache_seconds)
            if stale:
                return stale.model_copy(update={"stale": True})
            raise

    def get_chain(self, expiry: str, force: bool = False) -> OptionChain:
        expiries = self.get_expiries(force=force)
        if expiry not in expiries.expiries:
            raise MarketDataError(f"Invalid expiry: {expiry}")
        key = f"chain:{expiry}"
        if not force:
            cached = self.cache.get(key, settings.cache_ttl_seconds)
            if cached:
                return cached.model_copy(update={"stale": False})
        try:
            payload = self._request_json(
                "/api/option-chain-v3",
                {"type": "Indices", "symbol": "NIFTY", "expiry": expiry},
            )
            records = payload.get("records") or {}
            raw_rows = records.get("data") or []
            if not raw_rows:
                raise MarketDataError("NSE returned an empty option chain")
            rows = [self._normalize_row(row, expiry) for row in raw_rows]
            rows = [row for row in rows if row.ce or row.pe]
            result = OptionChain(
                expiry=expiry,
                timestamp=records.get("timestamp")
                or datetime.now(timezone.utc).isoformat(),
                underlying_value=float(records.get("underlyingValue") or 0),
                lot_size=settings.nifty_lot_size,
                rows=rows,
                stale=expiries.stale,
            )
            self.cache.set(key, result)
            return result
        except MarketDataError:
            stale = self.cache.get(key, settings.stale_cache_seconds)
            if stale:
                return stale.model_copy(update={"stale": True})
            raise

    @staticmethod
    def _normalize_row(raw: dict[str, Any], expiry: str) -> ChainRow:
        strike = float(raw.get("strikePrice") or 0)

        def quote(option_type: str) -> OptionQuote | None:
            item = raw.get(option_type)
            if not item:
                return None
            return OptionQuote(
                option_type=option_type,
                strike=float(item.get("strikePrice") or strike),
                expiry=item.get("expiryDate") or expiry,
                bid=float(item.get("buyPrice1") or 0),
                ask=float(item.get("sellPrice1") or 0),
                last_price=float(item.get("lastPrice") or 0),
                volume=int(item.get("totalTradedVolume") or 0),
                open_interest=int(item.get("openInterest") or 0),
                change_in_oi=int(item.get("changeinOpenInterest") or 0),
                implied_volatility=float(item.get("impliedVolatility") or 0),
            )

        return ChainRow(strike=strike, ce=quote("CE"), pe=quote("PE"))


provider = NSEMarketDataProvider()
