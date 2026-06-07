import math

from app.models import ChainRow, OptionChain, OptionQuote
from app.strategies import (
    black_scholes,
    scan_broken_wing_butterflies,
    scan_butterflies,
    scan_box_spreads,
    scan_christmas_trees,
    scan_collars,
    scan_covered_calls,
    scan_credit_spreads,
    scan_debit_spreads,
    scan_iron_condors,
    scan_fences,
    scan_guts,
    scan_jade_lizards,
    scan_poor_mans_covered_calls,
    scan_risk_reversals,
    scan_straddles,
    scan_straps,
    scan_strangles,
    scan_strips,
    scan_seagulls,
    scan_time_spreads,
)


def quote(kind, strike, expiry, bid, ask, iv=15, volume=10000, oi=100000):
    return OptionQuote(
        option_type=kind,
        strike=strike,
        expiry=expiry,
        bid=bid,
        ask=ask,
        last_price=(bid + ask) / 2,
        volume=volume,
        open_interest=oi,
        implied_volatility=iv,
    )


def chain(expiry="09-Jun-2026", premium_shift=0):
    rows = []
    premiums = {
        23100: (350, 90),
        23200: (270, 120),
        23300: (200, 160),
        23400: (145, 215),
        23500: (100, 280),
        23600: (65, 360),
    }
    for strike, (call, put) in premiums.items():
        rows.append(
            ChainRow(
                strike=strike,
                ce=quote("CE", strike, expiry, call - 2 + premium_shift, call + 2 + premium_shift),
                pe=quote("PE", strike, expiry, put - 2 + premium_shift, put + 2 + premium_shift),
            )
        )
    return OptionChain(
        expiry=expiry,
        timestamp="07-Jun-2026 15:30:00",
        underlying_value=23366,
        lot_size=65,
        rows=rows,
    )


def test_bull_call_debit_spread_payoff_and_multiplier():
    candidates = scan_debit_spreads(chain(), limit=50)
    candidate = next(
        item
        for item in candidates
        if item.legs[0].option_type == "CE"
        and item.legs[0].strike == 23300
        and item.legs[1].strike == 23400
    )
    assert candidate.net_debit == 3575
    assert candidate.max_loss == 3575
    assert candidate.max_profit == 2925
    assert candidate.breakevens == [23355]
    assert candidate.return_on_risk == round(2925 / 3575 * 100, 2)


def test_screenshot_style_ltp_math_uses_lot_size_65():
    test_chain = chain()
    buy = test_chain.rows[2].pe
    sell = test_chain.rows[0].pe
    buy.last_price = 87.05
    buy.bid = buy.ask = 87.05
    sell.last_price = 19.85
    sell.bid = sell.ask = 19.85
    buy.strike = test_chain.rows[2].strike = 23300
    sell.strike = test_chain.rows[0].strike = 23000
    candidates = scan_debit_spreads(test_chain, limit=50)
    candidate = next(
        item
        for item in candidates
        if item.legs[0].option_type == "PE"
        and item.legs[0].strike == 23300
        and item.legs[1].strike == 23000
    )
    assert candidate.net_debit == 4368
    assert candidate.max_loss == 4368


def test_put_debit_spread_has_correct_leg_direction():
    candidates = scan_debit_spreads(chain(), limit=50)
    put = next(item for item in candidates if item.legs[0].option_type == "PE")
    assert put.legs[0].action == "BUY"
    assert put.legs[1].action == "SELL"
    assert put.legs[1].strike < put.legs[0].strike


def test_calendar_and_diagonal_use_distinct_expiries():
    near = chain()
    far = chain("16-Jun-2026", premium_shift=40)
    calendars = scan_time_spreads(near, far, diagonal=False)
    diagonals = scan_time_spreads(near, far, diagonal=True)
    assert calendars
    assert diagonals
    assert all(item.legs[0].expiry != item.legs[1].expiry for item in calendars + diagonals)
    assert all(item.legs[0].strike == item.legs[1].strike for item in calendars)
    assert all(item.legs[0].strike != item.legs[1].strike for item in diagonals)
    assert all("not guaranteed" in item.notes[0] for item in calendars)
    assert all(item.metric_mode == "modeled" for item in calendars + diagonals)
    assert all(item.estimated_peak_profit is not None for item in calendars)
    assert all(item.modeled_worst_loss is not None for item in calendars)
    assert all(item.modeled_return_risk is not None for item in calendars)


def test_diagonal_can_be_classified_as_credit():
    near = chain()
    far = chain("16-Jun-2026", premium_shift=-100)
    diagonals = scan_time_spreads(near, far, diagonal=True, limit=50)
    assert diagonals
    assert any(item.net_credit is not None for item in diagonals)
    assert all(
        (item.net_debit is None) != (item.net_credit is None)
        for item in diagonals
    )


def test_time_spreads_reject_crossed_quotes():
    near = chain()
    far = chain("16-Jun-2026", premium_shift=40)
    far.rows[0].ce.bid = 500
    far.rows[0].ce.ask = 400
    crossed_strike = far.rows[0].ce.strike
    diagonals = scan_time_spreads(near, far, diagonal=True, limit=50)
    assert all(
        not (
            item.legs[1].option_type == "CE"
            and item.legs[1].strike == crossed_strike
        )
        for item in diagonals
    )


def test_batch_one_scanners_return_top_ten_or_less():
    test_chain = chain()
    scanners = [
        scan_credit_spreads,
        scan_iron_condors,
        scan_butterflies,
        scan_broken_wing_butterflies,
        scan_risk_reversals,
        scan_straddles,
        scan_strangles,
    ]
    for scanner in scanners:
        candidates = scanner(test_chain, limit=10)
        assert candidates
        assert len(candidates) <= 10
        assert all(item.metadata["premium_basis"] == "LTP" for item in candidates)
        assert all(item.metadata["lot_size"] == 65 for item in candidates)


def test_unbounded_strategies_mark_unlimited_side():
    candidates = scan_straddles(chain(), limit=10)
    short = next(item for item in candidates if item.strategy == "Short Straddle")
    long = next(item for item in candidates if item.strategy == "Long Straddle")
    assert short.metadata["bounded_loss"] is False
    assert short.max_loss is None
    assert long.metadata["bounded_profit"] is False
    assert long.max_profit is None


def test_covered_call_reports_proxy_coverage():
    candidates = scan_covered_calls(chain(), units=5000, average_cost=250, current_price=285)
    assert candidates
    candidate = candidates[0]
    expected = 5000 * 285 / (23366 * 65)
    assert math.isclose(candidate.metadata["coverage_ratio"], round(expected, 4))
    assert candidate.net_credit > 0
    assert "not a perfect hedge" in candidate.notes[0]


def test_black_scholes_intrinsic_at_expiry():
    assert black_scholes(110, 100, 0, 20, "CE") == 10
    assert black_scholes(90, 100, 0, 20, "PE") == 10


def test_batch_two_single_expiry_scanners_return_ranked_candidates():
    test_chain = chain()
    scanners = [
        scan_jade_lizards,
        scan_box_spreads,
        scan_seagulls,
        scan_christmas_trees,
        scan_guts,
        scan_strips,
        scan_straps,
    ]
    for scanner in scanners:
        candidates = scanner(test_chain, limit=10)
        assert candidates, scanner.__name__
        assert len(candidates) <= 10
        assert all(item.metadata["premium_basis"] == "LTP" for item in candidates)
        assert all(item.metadata["lot_size"] == 65 for item in candidates)
        assert candidates == sorted(
            candidates, key=lambda item: item.score, reverse=True
        )


def test_fence_and_collar_keep_session_position_in_candidate_metadata():
    for scanner in (scan_fences, scan_collars):
        candidates = scanner(
            chain(), units=5000, average_cost=250, current_price=285, limit=10
        )
        assert candidates
        position = candidates[0].metadata["underlying_position"]
        assert position == {
            "asset": "NIFTYBEES",
            "units": 5000,
            "average_cost": 250,
            "current_price": 285,
        }
        assert "Proxy" in candidates[0].strategy


def test_poor_mans_covered_call_uses_distinct_expiries_and_modeled_metrics():
    near = chain()
    far = chain("16-Jun-2026", premium_shift=40)
    deep_call = quote("CE", 22000, far.expiry, 1400, 1410, iv=18)
    far.rows.append(ChainRow(strike=22000, ce=deep_call))
    candidates = scan_poor_mans_covered_calls(near, far, limit=10)
    assert candidates
    candidate = candidates[0]
    assert candidate.metric_mode == "modeled"
    assert candidate.legs[0].expiry == far.expiry
    assert candidate.legs[1].expiry == near.expiry
    assert candidate.estimated_peak_profit is not None
    assert candidate.modeled_worst_loss is not None
