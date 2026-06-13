from collections import deque
from types import SimpleNamespace

import pytest

from app.reports import ReportRateLimit, ReportService
from app.models import MarketContext, ReportRequest
from app.strategies import scan_debit_spreads
from test_strategies import chain


def test_report_rate_limits_one_per_minute(monkeypatch):
    service = ReportService()
    service.requests["127.0.0.1"] = deque([100])
    monkeypatch.setattr("app.reports.time.time", lambda: 120)
    with pytest.raises(ReportRateLimit, match="one minute"):
        service._check_limit("127.0.0.1")


def test_report_rate_limits_daily_total(monkeypatch):
    service = ReportService()
    service.requests["127.0.0.1"] = deque(range(10, 110, 10))
    monkeypatch.setattr("app.reports.time.time", lambda: 200)
    with pytest.raises(ReportRateLimit, match="Daily"):
        service._check_limit("127.0.0.1")


def test_missing_gemini_key_returns_rules_report(monkeypatch):
    service = ReportService()
    monkeypatch.setattr(
        "app.reports.settings",
        SimpleNamespace(gemini_api_key=None, gemini_model="gemini-2.5-flash-lite"),
    )
    candidate = scan_debit_spreads(chain(), limit=1)[0]
    report = service.generate(
        ReportRequest(
            candidate=candidate,
            chain_timestamp="07-Jun-2026 15:30:00",
            underlying_value=23366,
        ),
        "127.0.0.1",
    )
    assert report.generated_by == "rules"
    assert candidate.strategy in report.title
    assert report.short_term_trend == "Unavailable"
    assert report.confidence == "low"
    assert report.desk_analysis is not None
    assert len(report.desk_analysis.monitoring_checklist) == 5
    assert report.desk_analysis.entry_execution_plan
    assert report.fallback_reason == "The AI provider API key is not configured."


def test_gemini_failure_falls_back_to_rules(monkeypatch):
    service = ReportService()
    monkeypatch.setattr(
        "app.reports.settings",
        SimpleNamespace(
            gemini_api_key="test-key", gemini_model="gemini-2.5-flash-lite"
        ),
    )
    monkeypatch.setattr(
        service,
        "_gemini_report",
        lambda request, evidence: (_ for _ in ()).throw(ValueError("bad output")),
    )
    candidate = scan_debit_spreads(chain(), limit=1)[0]
    report = service.generate(
        ReportRequest(
            candidate=candidate,
            chain_timestamp="07-Jun-2026 15:30:00",
            underlying_value=23366,
        ),
        "127.0.0.2",
    )
    assert report.generated_by == "rules"
    assert report.validation_status == "rules-fallback"


def test_rules_report_uses_only_supplied_market_context(monkeypatch):
    service = ReportService()
    monkeypatch.setattr(
        "app.reports.settings",
        SimpleNamespace(gemini_api_key=None, gemini_model="gemini-2.5-flash-lite"),
    )
    candidate = scan_debit_spreads(chain(), limit=1)[0]
    report = service.generate(
        ReportRequest(
            candidate=candidate,
            chain_timestamp="07-Jun-2026 15:30:00",
            underlying_value=23366,
            market_context=MarketContext(
                short_term_trend="Bullish",
                medium_term_trend="Sideways",
                momentum="Positive",
                volatility_regime="Normal",
                global_macro_context=["Verified macro note"],
                upcoming_events=["10-Jun-2026: Verified event"],
                sources=["Test source"],
                data_timestamp="07-Jun-2026 15:35:00",
                stale=False,
            ),
        ),
        "127.0.0.3",
    )
    assert report.short_term_trend == "Bullish"
    assert "Verified macro note" in report.global_macro_context
    assert report.upcoming_events == ["10-Jun-2026: Verified event"]
    assert "Test source" in report.sources
    assert report.confidence == "medium"
    assert report.desk_analysis.monitoring_checklist
