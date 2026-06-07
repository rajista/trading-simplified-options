import pytest

from app.provider import MarketDataError, NSEMarketDataProvider, TimedCache


def test_cache_respects_age(monkeypatch):
    cache = TimedCache()
    monkeypatch.setattr("app.provider.time.time", lambda: 100)
    cache.set("key", "value")
    monkeypatch.setattr("app.provider.time.time", lambda: 120)
    assert cache.get("key", 60) == "value"
    assert cache.get("key", 10) is None


def test_invalid_expiry_is_rejected(monkeypatch):
    provider = NSEMarketDataProvider()
    monkeypatch.setattr(
        provider,
        "get_expiries",
        lambda force=False: type("Result", (), {"expiries": ["09-Jun-2026"]})(),
    )
    with pytest.raises(MarketDataError, match="Invalid expiry"):
        provider.get_chain("01-Jan-2000")


def test_normalizer_handles_missing_leg():
    row = NSEMarketDataProvider._normalize_row(
        {
            "strikePrice": 23000,
            "CE": {
                "strikePrice": 23000,
                "buyPrice1": 100,
                "sellPrice1": 102,
            },
        },
        "09-Jun-2026",
    )
    assert row.ce is not None
    assert row.pe is None
    assert row.ce.bid == 100
