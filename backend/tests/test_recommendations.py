from types import SimpleNamespace

from app.models import (
    MarketContext,
    MarketMove,
    RecommendationRequest,
    TechnicalIndicators,
)
from app.recommendations import GeminiIdea, GeminiRecommendation, RecommendationService
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
    monkeypatch.setattr(service, "_events", lambda analysis_date: [])
    monkeypatch.setattr(
        service,
        "_technical_indicators",
        lambda: (
            TechnicalIndicators(
                last=23366,
                ema_9=23400,
                ema_21=23300,
                rsi_14=55,
                timestamp="2026-06-07T00:00:00+00:00",
            ),
            {},
        ),
    )
    monkeypatch.setattr(
        service,
        "_market_move",
        lambda symbol, name, nifty_prices=None: MarketMove(
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
    assert result.option_chain_summary.atm_strike == 23400
    assert result.validation_status == "rules-fallback"


def test_gemini_validation_rejects_unknown_references_and_duplicate_family():
    service = RecommendationService()
    candidates = service._candidate_pool(chain(), chain("16-Jun-2026", premium_shift=40))
    first = candidates[0]
    same_family = next(
        item for item in candidates[1:] if service._family(item) == service._family(first)
    )

    def idea(candidate_id, suffix):
        return GeminiIdea(
            candidate_id=candidate_id,
            title=f"Idea {suffix}",
            outlook="Neutral",
            recommendation=f"Recommendation {suffix}",
            background=f"Distinct background {suffix}",
            analysis=f"Distinct analysis {suffix}",
            entry_plan=f"Distinct entry {suffix}",
            risk_management=f"Distinct risk {suffix}",
            headline_ids=["missing-news"] if suffix == 1 else [],
        )

    report = GeminiRecommendation(
        ideas=[
            idea(first.id, 1),
            idea(same_family.id, 2),
            idea(None, 3),
            idea(None, 4),
            idea(None, 5),
        ]
    )
    errors = service._validate_gemini(report, candidates, [], [], [])
    assert any("unknown headline" in error for error in errors)
    assert any("family is repeated" in error for error in errors)


def test_server_controls_confidence():
    service = RecommendationService()
    candidate = service._candidate_pool(chain(), None)[0].model_copy(
        update={"score": 95, "liquidity_score": 95, "outlook": "Bullish"}
    )
    confidence, _ = service._confidence(
        candidate,
        TechnicalIndicators(ema_9=23500, ema_21=23300, rsi_14=60),
    )
    assert confidence == "high"


def test_call_and_put_time_spreads_share_one_strategy_family():
    service = RecommendationService()
    candidates = service._candidate_pool(
        chain(), chain("16-Jun-2026", premium_shift=40)
    )
    time_spreads = [
        candidate
        for candidate in candidates
        if "Calendar" in candidate.strategy or "Diagonal" in candidate.strategy
    ]
    assert {service._family(item) for item in time_spreads} <= {"calendar", "diagonal"}
