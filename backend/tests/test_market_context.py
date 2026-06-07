from app.market_context import MarketContextService


def test_market_context_classifies_trend_momentum_and_volatility():
    closes = [22000 + index * 20 for index in range(80)]
    context = MarketContextService._context(closes, "2026-06-07T10:00:00+00:00")
    assert context.short_term_trend == "Bullish"
    assert context.medium_term_trend == "Bullish"
    assert context.momentum.startswith("Neutral") or context.momentum.startswith("Positive")
    assert context.data_timestamp == "2026-06-07T10:00:00+00:00"
    assert context.stale is False
