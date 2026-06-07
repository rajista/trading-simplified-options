from types import SimpleNamespace

from app.models import MarketContext, MarketMove, RecommendationRequest
from app.recommendations import RecommendationService
from test_strategies import chain


def test_recommendation_rules_fallback_returns_five_ideas(monkeypatch):
    service = RecommendationService()
    test_chain = chain()
    monkeypatch.setattr("app.recommendations.settings", SimpleNamespace(gemini_api_key=None, gemini_model="gemini-2.5-flash-lite"))
    monkeypatch.setattr("app.recommendations.provider.get_chain", lambda expiry, force=False: test_chain)
    monkeypatch.setattr("app.recommendations.market_context_service.get", lambda force=False: MarketContext(
        short_term_trend="Bullish",
        medium_term_trend="Sideways",
        momentum="Positive",
        volatility_regime="Normal",
        stale=False,
        data_timestamp="07-Jun-2026 15:30:00",
    ))
    monkeypatch.setattr(service, "_news", lambda: [])
    monkeypatch.setattr(
        service,
        "_market_move",
        lambda symbol, name: MarketMove(
            symbol=symbol,
            name=name,
            last=100,
            one_day_return=1,
            one_week_return=2,
            timestamp="2026-06-07T00:00:00+00:00",
            source="Yahoo Finance",
        ),
    )
    result = service.generate(
        RecommendationRequest(
            expiry="09-Jun-2026",
            far_expiry="16-Jun-2026",
            analysis_date="2026-06-07",
        ),
        "127.0.0.1",
    )
    assert result.generated_by == "rules"
    assert len(result.ideas) == 5
    assert result.ideas[0].chart_points
    assert len(result.global_markets) >= 5
