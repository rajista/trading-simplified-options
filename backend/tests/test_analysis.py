from app.analysis import analyze_candidate
from app.models import AnalysisRequest
from app.strategies import scan_debit_spreads, scan_time_spreads
from app.strategies import scan_covered_calls
from test_strategies import chain


def test_analysis_returns_today_and_expiry_curves():
    candidate = scan_debit_spreads(chain(), limit=1)[0]
    result = analyze_candidate(
        AnalysisRequest(
            candidate=candidate,
            underlying_value=23366,
            lot_size=65,
            chain_timestamp="07-Jun-2026 15:30:00",
            evaluation_days=3650,
        )
    )
    assert len(result.points) == 81
    assert result.evaluation_days == result.max_evaluation_days == 2
    assert result.estimated_peak_profit > 0
    assert result.modeled_worst_loss > 0
    assert result.modeled_return_risk is not None


def test_time_spread_analysis_changes_with_date_and_iv():
    candidate = scan_time_spreads(
        chain(), chain("16-Jun-2026", premium_shift=40), limit=1
    )[0]
    base = analyze_candidate(
        AnalysisRequest(
            candidate=candidate,
            underlying_value=23366,
            lot_size=65,
            chain_timestamp="07-Jun-2026 15:30:00",
            evaluation_days=1,
        )
    )
    shifted = analyze_candidate(
        AnalysisRequest(
            candidate=candidate,
            underlying_value=23366,
            lot_size=65,
            chain_timestamp="07-Jun-2026 15:30:00",
            evaluation_days=2,
            iv_shift=5,
        )
    )
    assert base.points[40].evaluation_pnl != shifted.points[40].evaluation_pnl
    assert shifted.evaluation_label == "09-Jun-2026"


def test_breakevens_stay_inside_chart_range():
    candidate = scan_time_spreads(
        chain(), chain("16-Jun-2026", premium_shift=40), limit=1
    )[0]
    result = analyze_candidate(
        AnalysisRequest(
            candidate=candidate,
            underlying_value=23366,
            lot_size=65,
            chain_timestamp="07-Jun-2026 15:30:00",
            evaluation_days=2,
            price_range_pct=5,
        )
    )
    assert all(23366 * 0.95 <= value <= 23366 * 1.05 for value in result.estimated_breakevens)


def test_covered_call_analysis_includes_etf_position():
    candidate = scan_covered_calls(
        chain(), units=5000, average_cost=250, current_price=285, limit=1
    )[0]
    result = analyze_candidate(
        AnalysisRequest(
            candidate=candidate,
            underlying_value=23366,
            lot_size=65,
            chain_timestamp="07-Jun-2026 15:30:00",
            evaluation_days=2,
        )
    )
    assert result.points[-1].evaluation_pnl > result.points[0].evaluation_pnl
