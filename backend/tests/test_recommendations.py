from types import SimpleNamespace

import pytest

from app.desk_analysis import PROMPT_VERSION
from app.models import (
    MarketEvidencePacket,
    MarketMove,
    OptionChainSummary,
    RecommendationNarrativeRequest,
    RecommendationRequest,
    TechnicalIndicators,
)
from app.recommendations import GeminiIdea, GeminiRecommendation, RecommendationService
from test_strategies import chain


def evidence(test_chain):
    return MarketEvidencePacket(
        report_date="2026-06-07",
        chain_timestamp=test_chain.timestamp,
        technical_indicators=TechnicalIndicators(
            last=test_chain.underlying_value,
            timestamp="2026-06-07T00:00:00+00:00",
        ),
        option_chain_summary=OptionChainSummary(
            spot=test_chain.underlying_value,
            timestamp=test_chain.timestamp,
        ),
        global_markets=[
            MarketMove(
                symbol="^GSPC",
                name="S&P 500",
                last=100,
                timestamp="2026-06-07T00:00:00+00:00",
            )
        ],
        news=[],
        market_events=[],
        short_term_trend="Mixed",
        medium_term_trend="Sideways",
        momentum_strength="Neutral",
        volatility_regime="Normal",
        iv_premium_regime="Near realized volatility",
        option_chain_bias="Balanced",
        event_risk="No verified nearby event",
    )


def setup_service(monkeypatch):
    service = RecommendationService()
    test_chain = chain()
    monkeypatch.setattr(
        "app.recommendations.provider.get_chain",
        lambda expiry, force=False: test_chain.model_copy(update={"expiry": expiry}),
    )
    monkeypatch.setattr(service, "_cached_evidence", lambda chain, date: evidence(chain))
    return service, test_chain


def test_preview_returns_cards_before_narrative(monkeypatch):
    service, _ = setup_service(monkeypatch)
    result = service.preview(
        RecommendationRequest(
            expiry="09-Jun-2026",
            far_expiry="16-Jun-2026",
            analysis_date="2026-06-07",
        )
    )
    assert result.analysis_id
    assert result.narrative_pending
    assert result.validation_status == "preview-ready"
    assert len(result.ideas) == 5
    assert len(result.high_risk_ideas) == 2
    assert result.ideas[0].candidate
    assert result.ideas[0].chart_points
    assert result.ideas[0].desk_analysis is None


def test_missing_key_returns_concise_rules_narrative(monkeypatch):
    service, _ = setup_service(monkeypatch)
    monkeypatch.setattr(
        "app.recommendations.settings",
        SimpleNamespace(gemini_api_key=None),
    )
    preview = service.preview(
        RecommendationRequest(
            expiry="09-Jun-2026",
            far_expiry="16-Jun-2026",
            analysis_date="2026-06-07",
        )
    )
    result = service.narrative(
        RecommendationNarrativeRequest(analysis_id=preview.analysis_id),
        "127.0.0.1",
    )
    assert result.generated_by == "rules"
    assert result.validation_status == "rules-fallback"
    assert result.fallback_reason == (
        "AI commentary was unavailable, so a concise desk view is shown."
    )
    analysis = result.ideas[0].desk_analysis
    assert analysis
    assert service._sentence_count(analysis.thesis) in {2, 3}
    assert service._sentence_count(analysis.entry) == 1
    assert service._sentence_count(analysis.risk_exit) == 1
    assert not hasattr(analysis, "monitoring_checklist")
    assert not hasattr(analysis, "word_count")


def test_concise_validation_allows_missing_global_references(monkeypatch):
    service, test_chain = setup_service(monkeypatch)
    candidates = (
        service._candidate_pool(test_chain, test_chain.model_copy(update={"expiry": "16-Jun-2026"}))[:5]
        + service._high_risk_pool(test_chain)[:2]
    )
    report = GeminiRecommendation(
        ideas=[
            GeminiIdea(
                candidate_id=candidate.id,
                thesis=(
                    f"The setup has a clear edge in the current market regime. "
                    f"It remains useful while the original thesis holds for this structure."
                ),
                entry="Enter the complete package only when the quoted spread remains orderly.",
                risk_exit="Exit when price behavior no longer supports the intended payoff.",
            )
            for candidate in candidates
        ]
    )
    errors = service._validate_concise(report, candidates, evidence(test_chain))
    assert not any("global" in error.lower() for error in errors)


def test_concise_validation_rejects_numbers_and_unknown_references(monkeypatch):
    service, test_chain = setup_service(monkeypatch)
    candidates = (
        service._candidate_pool(test_chain, test_chain.model_copy(update={"expiry": "16-Jun-2026"}))[:5]
        + service._high_risk_pool(test_chain)[:2]
    )
    report = GeminiRecommendation(
        ideas=[
            GeminiIdea(
                candidate_id=candidate.id,
                thesis=(
                    "The setup looks constructive with RSI at 55. "
                    "It remains useful while the original thesis holds."
                ),
                entry="Enter the complete package when execution remains orderly.",
                risk_exit="Exit when price behavior no longer supports the payoff.",
                headline_ids=["missing-news"] if index == 0 else [],
            )
            for index, candidate in enumerate(candidates)
        ]
    )
    errors = service._validate_concise(report, candidates, evidence(test_chain))
    assert any("raw numeric data" in error for error in errors)
    assert any("unknown headline" in error for error in errors)


def test_narrative_cache_reuses_completed_response(monkeypatch):
    service, _ = setup_service(monkeypatch)
    monkeypatch.setattr("app.recommendations.settings", SimpleNamespace(gemini_api_key=None))
    preview = service.preview(
        RecommendationRequest(
            expiry="09-Jun-2026",
            far_expiry="16-Jun-2026",
            analysis_date="2026-06-07",
        )
    )
    first = service.narrative(
        RecommendationNarrativeRequest(analysis_id=preview.analysis_id), "127.0.0.1"
    )
    monkeypatch.setattr(
        service,
        "_cached_evidence",
        lambda *_: (_ for _ in ()).throw(AssertionError("cache miss")),
    )
    second = service.narrative(
        RecommendationNarrativeRequest(analysis_id=preview.analysis_id), "127.0.0.1"
    )
    assert second == first


def test_expired_preview_returns_lookup_error():
    service = RecommendationService()
    with pytest.raises(LookupError, match="expired"):
        service.narrative(
            RecommendationNarrativeRequest(analysis_id="missing"), "127.0.0.1"
        )


def test_prompt_version_invalidates_old_cache_contract():
    assert PROMPT_VERSION == "desk-v5-concise"


def test_trade_validity_guard_rejects_weak_defined_payoff():
    service = RecommendationService()
    candidate = service._candidate_pool(chain(), None)[0].model_copy(
        update={"max_profit": 400, "max_loss": 1000, "metric_mode": "fixed"}
    )
    valid, reason, ratio = service._validity(candidate)
    assert not valid
    assert "best-case profit" in reason
    assert ratio == 0.4


def test_high_risk_pool_requires_three_to_one_defined_reward():
    service = RecommendationService()
    candidates = service._high_risk_pool(chain())
    assert len(candidates) >= 2
    for candidate in candidates:
        valid, _, ratio = service._validity(candidate)
        assert valid
        assert ratio is not None and ratio >= 3
