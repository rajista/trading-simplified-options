import pytest
from fastapi import HTTPException

from app import main
from app.models import (
    RecommendationNarrativeRequest,
    RecommendationRequest,
    RecommendationResponse,
)
from test_strategies import chain


def test_group_aliases_do_not_return_unknown_strategy(monkeypatch):
    monkeypatch.setattr(main.provider, "get_chain", lambda expiry: chain(expiry))

    others_one = main.strategies(
        strategy="others-1",
        expiry="09-Jun-2026",
        far_expiry=None,
        limit=2,
    )
    others_two = main.strategies(
        strategy="others-2",
        expiry="09-Jun-2026",
        far_expiry=None,
        limit=2,
    )

    assert others_one.strategy == "jade-lizard"
    assert others_one.candidates
    assert others_two.strategy == "strip"
    assert others_two.candidates


def test_recommendations_endpoint_shape(monkeypatch):
    def fake_generate(request, client_ip):
        return RecommendationResponse(
            analysis_date=request.analysis_date,
            generated_by="rules",
            chain_timestamp="07-Jun-2026 15:30:00",
            underlying_value=23366,
            market_context={},
            global_markets=[],
            news=[],
            ideas=[],
            assumptions=[],
            disclaimer="Educational only",
        )

    monkeypatch.setattr(main.recommendation_service, "generate", fake_generate)
    result = main.recommendations(
        request=RecommendationRequest(expiry="09-Jun-2026", analysis_date="2026-06-07"),
        raw_request=type("Request", (), {"client": type("Client", (), {"host": "test"})()})(),
    )
    assert result.generated_by == "rules"


def test_expired_recommendation_preview_returns_410(monkeypatch):
    monkeypatch.setattr(
        main.recommendation_service,
        "narrative",
        lambda request, client_ip: (_ for _ in ()).throw(LookupError("expired")),
    )
    with pytest.raises(HTTPException) as error:
        main.recommendation_narrative(
            request=RecommendationNarrativeRequest(analysis_id="expired"),
            raw_request=type(
                "Request",
                (),
                {"client": type("Client", (), {"host": "test"})()},
            )(),
        )
    assert error.value.status_code == 410
