from __future__ import annotations

import hashlib
import html
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from statistics import pstdev
from typing import Any
from xml.etree import ElementTree

from .models import (
    MarketEvent,
    OILevel,
    OptionChain,
    OptionChainSummary,
    TechnicalIndicators,
)


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def _ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * multiplier + output[-1] * (1 - multiplier))
    return output


def _return(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions or values[-sessions - 1] <= 0:
        return None
    return (values[-1] / values[-sessions - 1] - 1) * 100


def _realized_volatility(values: list[float], sessions: int) -> float | None:
    if len(values) <= sessions:
        return None
    returns = [
        values[index] / values[index - 1] - 1
        for index in range(len(values) - sessions, len(values))
        if values[index - 1] > 0
    ]
    return pstdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None


def calculate_indicators(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    timestamp: str | None,
    stale: bool = False,
) -> TechnicalIndicators:
    if not closes:
        return TechnicalIndicators(timestamp=timestamp, stale=True)
    last = closes[-1]
    ema_9 = _ema_series(closes, 9)
    ema_21 = _ema_series(closes, 21)
    ema_12 = _ema_series(closes, 12)
    ema_26 = _ema_series(closes, 26)
    macd_series = [fast - slow for fast, slow in zip(ema_12, ema_26)]
    signal_series = _ema_series(macd_series, 9)

    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes[-15:-1], closes[-14:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    average_gain = sum(gains) / 14 if len(gains) == 14 else None
    average_loss = sum(losses) / 14 if len(losses) == 14 else None
    if average_gain is None or average_loss is None:
        rsi = None
    elif average_loss == 0:
        rsi = 100.0
    else:
        rsi = 100 - 100 / (1 + average_gain / average_loss)

    true_ranges: list[float] = []
    usable = min(len(closes), len(highs), len(lows))
    for index in range(max(1, usable - 14), usable):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    atr = sum(true_ranges) / len(true_ranges) if true_ranges else None
    sma20 = _sma(closes, 20)
    std20 = pstdev(closes[-20:]) if len(closes) >= 20 else None
    bollinger = None
    if sma20 is not None and std20 and std20 > 0:
        bollinger = (last - (sma20 - 2 * std20)) / (4 * std20)

    lookback_highs = highs[-20:] if len(highs) >= 20 else highs
    lookback_lows = lows[-20:] if len(lows) >= 20 else lows
    resistance = max(lookback_highs) if lookback_highs else None
    support = min(lookback_lows) if lookback_lows else None
    swing_high = max(highs[-5:]) if highs else None
    swing_low = min(lows[-5:]) if lows else None
    return TechnicalIndicators(
        last=_round(last),
        return_1d=_round(_return(closes, 1)),
        return_5d=_round(_return(closes, 5)),
        return_20d=_round(_return(closes, 20)),
        return_3m=_round(_return(closes, 63)),
        sma_20=_round(sma20),
        sma_50=_round(_sma(closes, 50)),
        sma_200=_round(_sma(closes, 200)),
        ema_9=_round(ema_9[-1] if ema_9 else None),
        ema_21=_round(ema_21[-1] if ema_21 else None),
        rsi_14=_round(rsi),
        macd=_round(macd_series[-1] if macd_series else None),
        macd_signal=_round(signal_series[-1] if signal_series else None),
        macd_histogram=_round(
            macd_series[-1] - signal_series[-1]
            if macd_series and signal_series
            else None
        ),
        atr_14=_round(atr),
        atr_percent=_round(atr / last * 100 if atr and last else None),
        bollinger_position=_round(bollinger),
        realized_volatility_10d=_round(_realized_volatility(closes, 10)),
        realized_volatility_20d=_round(_realized_volatility(closes, 20)),
        support=_round(support),
        resistance=_round(resistance),
        swing_low=_round(swing_low),
        swing_high=_round(swing_high),
        distance_to_support_pct=_round(
            (last - support) / last * 100 if support else None
        ),
        distance_to_resistance_pct=_round(
            (resistance - last) / last * 100 if resistance else None
        ),
        timestamp=timestamp,
        stale=stale,
    )


def correlation(left: dict[int, float], right: dict[int, float], sessions: int) -> float | None:
    common = sorted(set(left) & set(right))[-(sessions + 1):]
    if len(common) < max(5, sessions // 2):
        return None
    left_returns = [left[b] / left[a] - 1 for a, b in zip(common, common[1:])]
    right_returns = [right[b] / right[a] - 1 for a, b in zip(common, common[1:])]
    left_mean = sum(left_returns) / len(left_returns)
    right_mean = sum(right_returns) / len(right_returns)
    numerator = sum(
        (x - left_mean) * (y - right_mean)
        for x, y in zip(left_returns, right_returns)
    )
    left_variance = sum((x - left_mean) ** 2 for x in left_returns)
    right_variance = sum((y - right_mean) ** 2 for y in right_returns)
    denominator = math.sqrt(left_variance * right_variance)
    return round(numerator / denominator, 3) if denominator else None


def _valid_quote(quote: Any) -> bool:
    return bool(
        quote
        and quote.last_price >= 0
        and not (quote.bid > 0 and quote.ask > 0 and quote.ask < quote.bid)
    )


def calculate_chain_summary(chain: OptionChain) -> OptionChainSummary:
    rows = sorted(chain.rows, key=lambda row: row.strike)
    strikes = [row.strike for row in rows]
    atm_row = min(rows, key=lambda row: abs(row.strike - chain.underlying_value)) if rows else None
    intervals = [
        current - previous
        for previous, current in zip(strikes, strikes[1:])
        if current > previous
    ]
    interval = min(intervals) if intervals else None
    ce = atm_row.ce if atm_row and _valid_quote(atm_row.ce) else None
    pe = atm_row.pe if atm_row and _valid_quote(atm_row.pe) else None
    straddle = (
        ce.last_price + pe.last_price
        if ce and pe and ce.last_price > 0 and pe.last_price > 0
        else None
    )
    valid_ce = [row.ce for row in rows if _valid_quote(row.ce)]
    valid_pe = [row.pe for row in rows if _valid_quote(row.pe)]
    call_oi = sum(quote.open_interest for quote in valid_ce)
    put_oi = sum(quote.open_interest for quote in valid_pe)
    call_change = sum(max(0, quote.change_in_oi) for quote in valid_ce)
    put_change = sum(max(0, quote.change_in_oi) for quote in valid_pe)
    near_rows = (
        [row for row in rows if abs(row.strike - atm_row.strike) <= (interval or 0) * 5]
        if atm_row
        else []
    )
    near_call = sum(row.ce.open_interest for row in near_rows if _valid_quote(row.ce))
    near_put = sum(row.pe.open_interest for row in near_rows if _valid_quote(row.pe))

    def largest(quotes: list[Any], field: str) -> OILevel | None:
        if not quotes:
            return None
        quote = max(quotes, key=lambda item: getattr(item, field))
        return OILevel(strike=quote.strike, value=int(getattr(quote, field)))

    max_pain = None
    if strikes:
        losses: dict[float, float] = {}
        for settlement in strikes:
            call_loss = sum(
                max(0, settlement - quote.strike) * quote.open_interest
                for quote in valid_ce
            )
            put_loss = sum(
                max(0, quote.strike - settlement) * quote.open_interest
                for quote in valid_pe
            )
            losses[settlement] = call_loss + put_loss
        max_pain = min(losses, key=losses.get)
    largest_call = largest(valid_ce, "open_interest")
    largest_put = largest(valid_pe, "open_interest")
    atm_iv_values = [
        quote.implied_volatility
        for quote in (ce, pe)
        if quote and quote.implied_volatility > 0
    ]
    return OptionChainSummary(
        spot=chain.underlying_value,
        atm_strike=atm_row.strike if atm_row else None,
        strike_interval=interval,
        atm_ce_ltp=ce.last_price if ce else None,
        atm_pe_ltp=pe.last_price if pe else None,
        atm_straddle_premium=_round(straddle),
        expected_move_points=_round(straddle),
        expected_move_percent=_round(
            straddle / chain.underlying_value * 100 if straddle else None
        ),
        atm_iv=_round(sum(atm_iv_values) / len(atm_iv_values) if atm_iv_values else None),
        call_put_iv_skew=_round(
            pe.implied_volatility - ce.implied_volatility
            if ce and pe and ce.implied_volatility and pe.implied_volatility
            else None
        ),
        total_oi_pcr=_round(put_oi / call_oi if call_oi else None),
        near_atm_oi_pcr=_round(near_put / near_call if near_call else None),
        change_oi_pcr=_round(put_change / call_change if call_change else None),
        largest_call_oi=largest_call,
        largest_put_oi=largest_put,
        largest_call_oi_change=largest(valid_ce, "change_in_oi"),
        largest_put_oi_change=largest(valid_pe, "change_in_oi"),
        call_oi_wall=largest_call.strike if largest_call else None,
        put_oi_wall=largest_put.strike if largest_put else None,
        estimated_max_pain=max_pain,
        timestamp=chain.timestamp,
        stale=chain.stale,
    )


def parse_rss_events(xml_text: str) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    root = ElementTree.fromstring(xml_text)
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        published = item.findtext("pubDate")
        if not title or not published:
            continue
        try:
            event_date = datetime.strptime(
                published[:16], "%a, %d %b %Y"
            ).date()
        except ValueError:
            continue
        url = item.findtext("link")
        identifier = hashlib.sha1(f"rbi:{title}:{event_date}".encode()).hexdigest()[:10]
        events.append(
            MarketEvent(
                id=f"event-{identifier}",
                date=event_date.isoformat(),
                title=title,
                importance="high" if re.search(r"MPC|policy|rate", title, re.I) else "medium",
                source="Reserve Bank of India",
                source_url=url,
                verified=True,
            )
        )
    return events


def parse_nse_holidays(text: str) -> list[MarketEvent]:
    events: list[MarketEvent] = []
    holiday_terms = re.compile(
        r"\b(holiday|republic day|holi|eid|good friday|ambedkar|maharashtra day|"
        r"independence day|gandhi jayanti|diwali|muhurat|gurunanak|christmas)\b",
        re.I,
    )
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.I | re.S):
        clean = html.unescape(re.sub(r"<[^>]+>", " ", row))
        clean = " ".join(clean.split())
        if not holiday_terms.search(clean):
            continue
        match = re.search(
            r"\b(\d{1,2}[-/ ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"[-/ ]\d{4})\b",
            clean,
            re.I,
        )
        if not match:
            continue
        raw_date = re.sub(r"[/ ]", "-", match.group(1))
        try:
            event_date = datetime.strptime(raw_date.title(), "%d-%b-%Y").date()
        except ValueError:
            continue
        title_match = holiday_terms.search(clean)
        title = title_match.group(0).title() if title_match else "Trading holiday"
        identifier = hashlib.sha1(f"nse:{title}:{event_date}".encode()).hexdigest()[:10]
        events.append(
            MarketEvent(
                id=f"event-{identifier}",
                date=event_date.isoformat(),
                title=f"NSE market holiday: {title}",
                importance="medium",
                source="National Stock Exchange of India",
                source_url="https://www.nseindia.com/resources/exchange-communication-holidays/",
                verified=True,
            )
        )
    return events


def parse_manual_events(raw_json: str) -> list[MarketEvent]:
    try:
        payload = json.loads(raw_json or "[]")
    except json.JSONDecodeError:
        return []
    events: list[MarketEvent] = []
    for index, item in enumerate(payload if isinstance(payload, list) else []):
        try:
            event_date = date.fromisoformat(str(item["date"]))
            title = str(item["title"]).strip()
            if not title:
                continue
            events.append(
                MarketEvent(
                    id=str(item.get("id") or f"manual-{index}-{event_date.isoformat()}"),
                    date=event_date.isoformat(),
                    title=title,
                    importance=item.get("importance", "high"),
                    source=str(item.get("source") or "Administrator verified event"),
                    source_url=item.get("source_url"),
                    verified=bool(item.get("verified", True)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return events


def merge_nearby_events(
    events: list[MarketEvent], report_date: str, window_days: int = 7
) -> list[MarketEvent]:
    center = date.fromisoformat(report_date)
    start = center - timedelta(days=window_days)
    end = center + timedelta(days=window_days)
    unique: dict[tuple[str, str], MarketEvent] = {}
    for event in events:
        event_date = date.fromisoformat(event.date)
        if start <= event_date <= end:
            key = (event.date, re.sub(r"\W+", "", event.title.lower()))
            existing = unique.get(key)
            if existing is None or (event.verified and not existing.verified):
                unique[key] = event
    return sorted(unique.values(), key=lambda item: (item.date, item.title))


def utc_timestamp(timestamp: int | float | None) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
        if timestamp
        else datetime.now(timezone.utc).isoformat()
    )
