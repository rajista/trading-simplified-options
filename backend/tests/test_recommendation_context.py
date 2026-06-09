import json
import math

from app.models import ChainRow, MarketEvent, OptionChain, OptionQuote
from app.recommendation_context import (
    calculate_chain_summary,
    calculate_indicators,
    correlation,
    merge_nearby_events,
    parse_manual_events,
    parse_nse_holidays,
    parse_rss_events,
)


def option(kind, strike, ltp, oi, change, iv):
    return OptionQuote(
        option_type=kind,
        strike=strike,
        expiry="09-Jun-2026",
        bid=max(0, ltp - 1),
        ask=ltp + 1,
        last_price=ltp,
        open_interest=oi,
        change_in_oi=change,
        implied_volatility=iv,
    )


def sample_chain():
    return OptionChain(
        expiry="09-Jun-2026",
        timestamp="2026-06-09T10:00:00+00:00",
        underlying_value=23360,
        lot_size=65,
        rows=[
            ChainRow(
                strike=23300,
                ce=option("CE", 23300, 120, 100, 20, 14),
                pe=option("PE", 23300, 60, 400, 80, 16),
            ),
            ChainRow(
                strike=23400,
                ce=option("CE", 23400, 70, 500, 100, 15),
                pe=option("PE", 23400, 105, 200, 40, 17),
            ),
            ChainRow(
                strike=23500,
                ce=option("CE", 23500, 35, 300, 60, 16),
                pe=option("PE", 23500, 160, 100, 20, 18),
            ),
        ],
    )


def test_indicators_calculate_price_action_and_volatility():
    closes = [22000 + index * 10 + math.sin(index) * 20 for index in range(220)]
    highs = [value + 50 for value in closes]
    lows = [value - 50 for value in closes]
    result = calculate_indicators(closes, highs, lows, "2026-06-09T00:00:00Z")
    assert result.sma_20 is not None
    assert result.sma_200 is not None
    assert result.ema_9 > result.ema_21
    assert 0 <= result.rsi_14 <= 100
    assert result.atr_14 > 0
    assert result.realized_volatility_20d >= 0
    assert result.support < result.resistance


def test_chain_summary_calculates_pcr_walls_expected_move_and_max_pain():
    result = calculate_chain_summary(sample_chain())
    assert result.atm_strike == 23400
    assert result.strike_interval == 100
    assert result.atm_straddle_premium == 175
    assert result.expected_move_percent == round(175 / 23360 * 100, 2)
    assert result.total_oi_pcr == round(700 / 900, 2)
    assert result.call_oi_wall == 23400
    assert result.put_oi_wall == 23300
    assert result.estimated_max_pain in {23300, 23400, 23500}


def test_chain_summary_rejects_crossed_quote_from_metrics():
    test_chain = sample_chain()
    test_chain.rows[1].ce.bid = 100
    test_chain.rows[1].ce.ask = 90
    result = calculate_chain_summary(test_chain)
    assert result.atm_ce_ltp is None


def test_correlation_uses_aligned_sessions():
    left = {index: 100 + index for index in range(70)}
    right = {index: 200 + index * 2 for index in range(70)}
    assert correlation(left, right, 60) > 0.99


def test_manual_and_rss_events_merge_deduplicate_and_filter():
    manual = parse_manual_events(
        json.dumps(
            [
                {
                    "id": "rbi-mpc",
                    "date": "2026-06-11",
                    "title": "RBI MPC decision",
                    "importance": "high",
                    "source": "RBI calendar",
                    "source_url": "https://www.rbi.org.in/",
                    "verified": True,
                },
                {"date": "2026-07-01", "title": "Outside window"},
            ]
        )
    )
    rss = parse_rss_events(
        """<rss><channel><item><title>Monetary Policy Statement</title>
        <pubDate>Tue, 09 Jun 2026 10:00:00 GMT</pubDate>
        <link>https://www.rbi.org.in/item</link></item></channel></rss>"""
    )
    combined = merge_nearby_events(manual + rss + [manual[0]], "2026-06-09")
    assert len(combined) == 2
    assert all(event.verified for event in combined)
    assert all(abs((__import__("datetime").date.fromisoformat(event.date) - __import__("datetime").date(2026, 6, 9)).days) <= 7 for event in combined)


def test_nse_holiday_parser_ignores_script_fields_and_accepts_holiday_rows():
    html = """
    <script>const values = ['09-Jun-2026', 'marketCapinTRDollars'];</script>
    <table><tr><td>15-Aug-2026</td><td>Independence Day</td></tr></table>
    """
    events = parse_nse_holidays(html)
    assert len(events) == 1
    assert events[0].date == "2026-08-15"
    assert "Independence Day" in events[0].title
