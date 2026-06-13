from __future__ import annotations

import hashlib
import math
from datetime import datetime

from .models import (
    OptionChain,
    OptionQuote,
    StrategyCandidate,
    StrategyLeg,
)


def _mid(quote: OptionQuote) -> float:
    if quote.bid > 0 and quote.ask >= quote.bid:
        return (quote.bid + quote.ask) / 2
    return quote.last_price


def _price(quote: OptionQuote, basis: str = "ltp") -> float:
    if basis == "mid":
        return _mid(quote)
    if quote.last_price > 0:
        return quote.last_price
    return _mid(quote)


def _liquidity(quote: OptionQuote) -> float:
    mid = _mid(quote)
    spread_score = 0 if mid <= 0 or quote.ask < quote.bid else max(
        0, 1 - (quote.ask - quote.bid) / max(mid, 0.05)
    )
    volume_score = min(1, math.log10(quote.volume + 1) / 5)
    oi_score = min(1, math.log10(quote.open_interest + 1) / 6)
    return round(100 * (0.45 * spread_score + 0.25 * volume_score + 0.30 * oi_score), 2)


def _tradable(quote: OptionQuote) -> bool:
    if quote.bid < 0 or quote.ask < 0:
        return False
    if quote.bid > 0 and quote.ask > 0 and quote.ask < quote.bid:
        return False
    return _mid(quote) > 0


def _candidate_id(strategy: str, legs: list[StrategyLeg]) -> str:
    raw = strategy + "|" + "|".join(
        f"{leg.quantity}:{leg.action}:{leg.option_type}:{leg.strike}:{leg.expiry}" for leg in legs
    )
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _leg(action: str, quote: OptionQuote, expiry: str, basis: str = "ltp", quantity: int = 1) -> StrategyLeg:
    return StrategyLeg(
        action=action,
        option_type=quote.option_type,
        strike=quote.strike,
        expiry=expiry,
        price=_price(quote, basis),
        implied_volatility=quote.implied_volatility,
        quantity=quantity,
    )


def _position_payoff(
    metadata: dict | None, underlying_price: float, reference_spot: float
) -> float:
    position = (metadata or {}).get("underlying_position")
    if not position:
        return 0.0
    units = float(position.get("units") or 0)
    current_price = float(position.get("current_price") or 0)
    cost_basis = float(position.get("average_cost") or current_price)
    if units <= 0 or current_price <= 0 or reference_spot <= 0:
        return 0.0
    scenario_price = current_price * underlying_price / reference_spot
    return (scenario_price - cost_basis) * units


def _expiry_payoff(
    legs: list[StrategyLeg],
    underlying_price: float,
    lot_size: int,
    reference_spot: float | None = None,
    metadata: dict | None = None,
) -> float:
    total = 0.0
    for leg in legs:
        intrinsic = (
            max(0, underlying_price - leg.strike)
            if leg.option_type == "CE"
            else max(0, leg.strike - underlying_price)
        )
        direction = 1 if leg.action == "BUY" else -1
        total += direction * (intrinsic - leg.price) * lot_size * leg.quantity
    return total + _position_payoff(
        metadata, underlying_price, reference_spot or underlying_price
    )


def _breakevens_from_points(points: list[tuple[float, float]]) -> list[float]:
    breakevens: list[float] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if y1 == 0:
            breakevens.append(x1)
        elif y1 * y2 < 0:
            ratio = abs(y1) / (abs(y1) + abs(y2))
            breakevens.append(x1 + (x2 - x1) * ratio)
    unique: list[float] = []
    for value in breakevens:
        rounded = round(value, 2)
        if not unique or abs(unique[-1] - rounded) > 0.5:
            unique.append(rounded)
    return unique


def _fixed_metrics(
    legs: list[StrategyLeg],
    spot: float,
    lot_size: int,
    range_pct: float = 0.25,
    metadata: dict | None = None,
) -> dict:
    signed_premium = sum(
        (leg.price if leg.action == "BUY" else -leg.price) * leg.quantity
        for leg in legs
    )
    premium_rupees = round(abs(signed_premium) * lot_size, 2)
    low = max(1, spot * (1 - range_pct))
    high = spot * (1 + range_pct)
    strikes = [leg.strike for leg in legs]
    low = min(low, min(strikes) * 0.95)
    high = max(high, max(strikes) * 1.05)
    prices = sorted({low, high, *strikes})
    points = [
        (
            price,
            _expiry_payoff(
                legs,
                price,
                lot_size,
                reference_spot=spot,
                metadata=metadata,
            ),
        )
        for price in prices
    ]
    values = [value for _, value in points]
    left_probe = min(high, low + 1)
    right_probe = max(low, high - 1)
    left_slope = _expiry_payoff(
        legs,
        left_probe,
        lot_size,
        reference_spot=spot,
        metadata=metadata,
    ) - points[0][1]
    right_slope = points[-1][1] - _expiry_payoff(
        legs,
        right_probe,
        lot_size,
        reference_spot=spot,
        metadata=metadata,
    )
    bounded_profit = right_slope <= 0.01 and left_slope >= -0.01
    bounded_loss = right_slope >= -0.01 and left_slope <= 0.01
    max_profit = max(values) if bounded_profit else None
    max_loss_value = abs(min(values)) if bounded_loss else None
    rr = (
        max_profit / max_loss_value * 100
        if max_profit is not None and max_loss_value and max_profit > 0
        else None
    )
    return {
        "net_debit": premium_rupees if signed_premium > 0 else None,
        "net_credit": premium_rupees if signed_premium < 0 else None,
        "max_profit": round(max_profit, 2) if max_profit is not None else None,
        "max_loss": round(max_loss_value, 2) if max_loss_value is not None else None,
        "breakevens": _breakevens_from_points(points),
        "return_on_risk": round(rr, 2) if rr is not None else None,
        "bounded_profit": bounded_profit,
        "bounded_loss": bounded_loss,
    }


def _build_fixed_candidate(
    name: str,
    outlook: str,
    legs: list[StrategyLeg],
    spot: float,
    lot_size: int,
    liquidity: float,
    family: str,
    notes: list[str] | None = None,
    metadata_extra: dict | None = None,
) -> StrategyCandidate:
    extra = metadata_extra or {}
    metrics = _fixed_metrics(legs, spot, lot_size, metadata=extra)
    risk = metrics["max_loss"] or metrics["net_debit"] or max(metrics["net_credit"] or 0, 1)
    reward = metrics["max_profit"] or metrics["net_credit"] or 0
    nearest_be = min(metrics["breakevens"], key=lambda value: abs(value - spot)) if metrics["breakevens"] else spot
    score = _balanced_score(
        liquidity,
        reward / max(risk, 1),
        abs(nearest_be - spot) / spot,
        min(abs(leg.strike - spot) for leg in legs) / spot,
        (
            0.65
            if extra.get("underlying_position")
            else 1
            if metrics["bounded_profit"] and metrics["bounded_loss"]
            else 0.35
        ),
    )
    metadata = {
        "premium_basis": "LTP",
        "lot_size": lot_size,
        "payoff_type": "expiry",
        "bounded_profit": metrics["bounded_profit"],
        "bounded_loss": metrics["bounded_loss"],
        "strategy_family": family,
        **extra,
    }
    return StrategyCandidate(
        id=_candidate_id(name, legs),
        strategy=name,
        outlook=outlook,
        score=score,
        legs=legs,
        net_debit=metrics["net_debit"],
        net_credit=metrics["net_credit"],
        max_profit=metrics["max_profit"],
        max_loss=metrics["max_loss"],
        breakevens=metrics["breakevens"],
        return_on_risk=metrics["return_on_risk"],
        liquidity_score=liquidity,
        notes=notes or [],
        metadata=metadata,
    )


def _balanced_score(
    liquidity: float,
    reward_risk: float,
    breakeven_distance: float,
    strike_distance: float,
    width_score: float = 1,
) -> float:
    reward = min(1, max(0, reward_risk) / 3)
    breakeven = max(0, 1 - breakeven_distance / 0.08)
    distance = max(0, 1 - strike_distance / 0.12)
    score = (
        0.35 * liquidity / 100
        + 0.25 * reward
        + 0.20 * breakeven
        + 0.10 * distance
        + 0.10 * width_score
    )
    return round(score * 100, 2)


def scan_debit_spreads(chain: OptionChain, limit: int = 12) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for option_type, outlook in (("CE", "Bullish"), ("PE", "Bearish")):
        quotes = [
            getattr(row, option_type.lower())
            for row in chain.rows
            if getattr(row, option_type.lower())
            and _tradable(getattr(row, option_type.lower()))
        ]
        quotes.sort(key=lambda q: q.strike)
        for index, buy in enumerate(quotes):
            if abs(buy.strike - spot) / spot > 0.08:
                continue
            candidates = quotes[index + 1 : index + 7] if option_type == "CE" else quotes[max(0, index - 6) : index]
            for sell in candidates:
                if option_type == "PE" and sell.strike >= buy.strike:
                    continue
                buy_price, sell_price = _price(buy), _price(sell)
                debit = buy_price - sell_price
                width = abs(sell.strike - buy.strike)
                if debit <= 0 or debit >= width:
                    continue
                max_profit = (width - debit) * chain.lot_size
                max_loss = debit * chain.lot_size
                breakeven = buy.strike + debit if option_type == "CE" else buy.strike - debit
                liquidity = min(_liquidity(buy), _liquidity(sell))
                rr = max_profit / max_loss
                legs = [
                    _leg("BUY", buy, chain.expiry),
                    _leg("SELL", sell, chain.expiry),
                ]
                candidate = _build_fixed_candidate(
                    f"{outlook} {option_type} Debit Spread",
                    outlook,
                    legs,
                    spot,
                    chain.lot_size,
                    liquidity,
                    "debit",
                )
                candidate.metadata.update({"width": width, "premium_points": round(debit, 2)})
                result.append(candidate)
    return sorted(result, key=lambda item: item.score, reverse=True)[:limit]


def _normal_cdf(value: float) -> float:
    return (1 + math.erf(value / math.sqrt(2))) / 2


def black_scholes(
    spot: float, strike: float, years: float, volatility: float, option_type: str, rate: float = 0.065
) -> float:
    if years <= 0 or volatility <= 0:
        return max(0, spot - strike) if option_type == "CE" else max(0, strike - spot)
    sigma = volatility / 100
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    if option_type == "CE":
        return spot * _normal_cdf(d1) - strike * math.exp(-rate * years) * _normal_cdf(d2)
    return strike * math.exp(-rate * years) * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def _days_between(first: str, second: str) -> int:
    formats = ("%d-%b-%Y", "%d-%m-%Y")
    def parse(value: str) -> datetime:
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                pass
        raise ValueError(f"Unsupported expiry format: {value}")
    return max(1, (parse(second) - parse(first)).days)


def scan_time_spreads(
    near: OptionChain,
    far: OptionChain,
    diagonal: bool = False,
    limit: int = 12,
) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = near.underlying_value
    far_by_type = {
        kind: {getattr(row, kind.lower()).strike: getattr(row, kind.lower()) for row in far.rows if getattr(row, kind.lower())}
        for kind in ("CE", "PE")
    }
    remaining_years = _days_between(near.expiry, far.expiry) / 365
    for kind, outlook in (("CE", "Neutral to bullish"), ("PE", "Neutral to bearish")):
        near_quotes = [
            getattr(row, kind.lower())
            for row in near.rows
            if getattr(row, kind.lower())
            and _tradable(getattr(row, kind.lower()))
        ]
        far_quotes = [
            quote for quote in far_by_type[kind].values() if _tradable(quote)
        ]
        for short in near_quotes:
            if abs(short.strike - spot) / spot > 0.06:
                continue
            possible = [
                quote for quote in far_quotes
                if (quote.strike == short.strike if not diagonal else 0 < abs(quote.strike - short.strike) <= 300)
            ]
            for long in possible:
                debit_points = _price(long) - _price(short)
                if not diagonal and debit_points <= 0:
                    continue
                if diagonal and abs(debit_points) < 0.01:
                    continue
                vol = long.implied_volatility or short.implied_volatility
                far_value_at_near = black_scholes(
                    short.strike, long.strike, remaining_years, vol, kind
                )
                short_intrinsic = (
                    max(0, long.strike - short.strike)
                    if kind == "PE"
                    else max(0, short.strike - long.strike)
                )
                center_profit = (far_value_at_near - short_intrinsic - debit_points) * near.lot_size
                move = max(near.rows[1].strike - near.rows[0].strike if len(near.rows) > 1 else 50, 100)
                scenario_values = []
                for scenario_spot in (short.strike - 2 * move, short.strike + 2 * move):
                    far_mark = black_scholes(scenario_spot, long.strike, remaining_years, vol, kind)
                    short_value = max(0, scenario_spot - short.strike) if kind == "CE" else max(0, short.strike - scenario_spot)
                    scenario_values.append((far_mark - short_value - debit_points) * near.lot_size)
                liquidity = min(_liquidity(short), _liquidity(long))
                legs = [
                    StrategyLeg(
                        action="SELL",
                        option_type=kind,
                        strike=short.strike,
                        expiry=near.expiry,
                        price=_price(short),
                        implied_volatility=short.implied_volatility,
                    ),
                    StrategyLeg(
                        action="BUY",
                        option_type=kind,
                        strike=long.strike,
                        expiry=far.expiry,
                        price=_price(long),
                        implied_volatility=long.implied_volatility,
                    ),
                ]
                name = "Diagonal Spread" if diagonal else "Calendar Spread"
                premium = abs(debit_points) * near.lot_size
                preliminary_risk = premium if debit_points > 0 else max(
                    premium, abs(min(scenario_values))
                )
                candidate = StrategyCandidate(
                    id=_candidate_id(name, legs),
                    strategy=f"{kind} {name}",
                    outlook=outlook,
                    score=_balanced_score(
                        liquidity,
                        max(0, center_profit) / max(preliminary_risk, 1),
                        abs(short.strike - spot) / spot,
                        abs(long.strike - spot) / spot,
                    ),
                    legs=legs,
                    net_debit=round(premium, 2) if debit_points > 0 else None,
                    net_credit=round(premium, 2) if debit_points < 0 else None,
                    metric_mode="modeled",
                    scenario_profit=round(center_profit, 2),
                    scenario_loss=round(min(scenario_values), 2),
                    liquidity_score=liquidity,
                    notes=[
                        "Profit, loss and breakevens are modeled at the near expiry, change with IV and time, and are not guaranteed limits."
                    ],
                    pricing_assumptions=[
                        "Black-Scholes valuation, 6.5% risk-free rate, no dividend yield.",
                        "Default analysis uses current quoted IV and a ±10% underlying-price range.",
                    ],
                    metadata={
                        "near_expiry": near.expiry,
                        "far_expiry": far.expiry,
                        "iv_term_difference": round(
                            long.implied_volatility - short.implied_volatility, 2
                        ),
                        "remaining_days": round(remaining_years * 365),
                    },
                )
                result.append(candidate)

    finalists = sorted(result, key=lambda item: item.score, reverse=True)[:limit]
    from .analysis import analyze_candidate
    from .models import AnalysisRequest

    for candidate in finalists:
        modeled = analyze_candidate(
                    AnalysisRequest(
                        candidate=candidate,
                        underlying_value=spot,
                        lot_size=near.lot_size,
                        chain_timestamp=near.timestamp,
                        evaluation_days=3650,
                    )
                )
        candidate.estimated_peak_profit = modeled.estimated_peak_profit
        candidate.modeled_worst_loss = modeled.modeled_worst_loss
        candidate.modeled_return_risk = modeled.modeled_return_risk
        candidate.estimated_breakevens = modeled.estimated_breakevens
        candidate.scenario_profit = modeled.estimated_peak_profit
        candidate.scenario_loss = -modeled.modeled_worst_loss
    return finalists


def scan_covered_calls(
    chain: OptionChain, units: int, average_cost: float, current_price: float, limit: int = 12
) -> list[StrategyCandidate]:
    spot = chain.underlying_value
    exposure_ratio = units * current_price / max(spot * chain.lot_size, 1)
    result = []
    for row in chain.rows:
        call = row.ce
        if not call or call.strike < spot or call.strike > spot * 1.08:
            continue
        premium = _price(call) * chain.lot_size
        upside = max(0, call.strike / spot - 1)
        liquidity = _liquidity(call)
        legs = [
            StrategyLeg(
                action="SELL",
                option_type="CE",
                strike=call.strike,
                expiry=chain.expiry,
                price=_price(call),
                implied_volatility=call.implied_volatility,
            )
        ]
        result.append(
            StrategyCandidate(
                id=_candidate_id("NIFTYBEES Covered Call Proxy", legs),
                strategy="NIFTYBEES Covered Call Proxy",
                outlook="Neutral to moderately bullish",
                score=_balanced_score(liquidity, premium / max(units * current_price, 1), upside, upside),
                legs=legs,
                net_credit=round(premium, 2),
                breakevens=[round(average_cost - premium / units, 2)],
                liquidity_score=liquidity,
                notes=[
                    "NIFTYBEES and NIFTY options are not a perfect hedge.",
                    "NIFTY options are cash-settled; ETF tracking, multiplier and basis risk remain.",
                ],
                metadata={
                    "units": units,
                    "average_cost": average_cost,
                    "current_price": current_price,
                    "position_value": round(units * current_price, 2),
                    "option_notional": round(spot * chain.lot_size, 2),
                    "coverage_ratio": round(exposure_ratio, 4),
                    "premium_per_etf_unit": round(premium / units, 2),
                },
            )
        )
    return sorted(result, key=lambda item: item.score, reverse=True)[:limit]


def _quotes(chain: OptionChain, kind: str) -> list[OptionQuote]:
    return sorted(
        [
            getattr(row, kind.lower())
            for row in chain.rows
            if getattr(row, kind.lower()) and _tradable(getattr(row, kind.lower()))
        ],
        key=lambda quote: quote.strike,
    )


def _finish(candidates: list[StrategyCandidate], limit: int) -> list[StrategyCandidate]:
    return sorted(candidates, key=lambda item: item.score, reverse=True)[:limit]


def scan_credit_spreads(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls, puts = _quotes(chain, "CE"), _quotes(chain, "PE")
    for quotes, kind, outlook in ((calls, "CE", "Bearish"), (puts, "PE", "Bullish")):
        for index, short in enumerate(quotes):
            if kind == "CE" and not (spot < short.strike <= spot * 1.08):
                continue
            if kind == "PE" and not (spot * 0.92 <= short.strike < spot):
                continue
            wings = quotes[index + 1 : index + 7] if kind == "CE" else quotes[max(0, index - 6) : index]
            for long in wings:
                if kind == "PE" and long.strike >= short.strike:
                    continue
                credit = _price(short) - _price(long)
                width = abs(long.strike - short.strike)
                if credit <= 0 or credit >= width:
                    continue
                legs = [_leg("SELL", short, chain.expiry), _leg("BUY", long, chain.expiry)]
                liquidity = min(_liquidity(short), _liquidity(long))
                name = f"{outlook} {kind} Credit Spread"
                result.append(
                    _build_fixed_candidate(name, outlook, legs, spot, chain.lot_size, liquidity, "credit")
                )
    return _finish(result, limit)


def scan_iron_condors(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls, puts = _quotes(chain, "CE"), _quotes(chain, "PE")
    put_shorts = sorted(
        [quote for quote in puts if spot * 0.92 <= quote.strike < spot],
        key=lambda quote: abs(quote.strike - spot),
    )[:8]
    call_shorts = sorted(
        [quote for quote in calls if spot < quote.strike <= spot * 1.08],
        key=lambda quote: abs(quote.strike - spot),
    )[:8]
    for ps in put_shorts:
        lower_puts = sorted(
            [quote for quote in puts if ps.strike - 500 <= quote.strike < ps.strike],
            key=lambda quote: abs(quote.strike - ps.strike),
        )[:3]
        for cs in call_shorts:
            higher_calls = sorted(
                [quote for quote in calls if cs.strike < quote.strike <= cs.strike + 500],
                key=lambda quote: abs(quote.strike - cs.strike),
            )[:3]
            if abs(ps.strike - spot) / spot < 0.002 or abs(cs.strike - spot) / spot < 0.002:
                continue
            for pl in lower_puts:
                for cl in higher_calls:
                    legs = [
                        _leg("BUY", pl, chain.expiry),
                        _leg("SELL", ps, chain.expiry),
                        _leg("SELL", cs, chain.expiry),
                        _leg("BUY", cl, chain.expiry),
                    ]
                    if sum((leg.price if leg.action == "BUY" else -leg.price) * leg.quantity for leg in legs) >= 0:
                        continue
                    liquidity = min(_liquidity(pl), _liquidity(ps), _liquidity(cs), _liquidity(cl))
                    result.append(_build_fixed_candidate("Iron Condor", "Range-bound", legs, spot, chain.lot_size, liquidity, "iron-condor"))
    return _finish(result, limit)


def scan_butterflies(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for kind, outlook in (("CE", "Neutral"), ("PE", "Neutral")):
        quotes = _quotes(chain, kind)
        by_strike = {quote.strike: quote for quote in quotes}
        for middle in quotes:
            if abs(middle.strike - spot) / spot > 0.05:
                continue
            for width in (50, 100, 150, 200, 250, 300):
                low, high = by_strike.get(middle.strike - width), by_strike.get(middle.strike + width)
                if not low or not high:
                    continue
                for short_body in (False, True):
                    legs = (
                        [_leg("BUY", low, chain.expiry), _leg("SELL", middle, chain.expiry, quantity=2), _leg("BUY", high, chain.expiry)]
                        if not short_body
                        else [_leg("SELL", low, chain.expiry), _leg("BUY", middle, chain.expiry, quantity=2), _leg("SELL", high, chain.expiry)]
                    )
                    liquidity = min(_liquidity(low), _liquidity(middle), _liquidity(high))
                    name = f"{'Short' if short_body else 'Long'} {kind} Butterfly"
                    result.append(_build_fixed_candidate(name, outlook, legs, spot, chain.lot_size, liquidity, "butterfly"))
    return _finish(result, limit)


def scan_broken_wing_butterflies(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for kind, outlook in (("CE", "Directional neutral"), ("PE", "Directional neutral")):
        quotes = _quotes(chain, kind)
        by_strike = {quote.strike: quote for quote in quotes}
        for middle in quotes:
            if abs(middle.strike - spot) / spot > 0.06:
                continue
            for narrow, wide in ((50, 150), (100, 200), (100, 300), (150, 300)):
                for left_width, right_width in ((narrow, wide), (wide, narrow)):
                    low = by_strike.get(middle.strike - left_width)
                    high = by_strike.get(middle.strike + right_width)
                    if not low or not high or left_width == right_width:
                        continue
                    legs = [_leg("BUY", low, chain.expiry), _leg("SELL", middle, chain.expiry, quantity=2), _leg("BUY", high, chain.expiry)]
                    liquidity = min(_liquidity(low), _liquidity(middle), _liquidity(high))
                    candidate = _build_fixed_candidate(
                        f"{kind} Broken-Wing Butterfly",
                        outlook,
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "broken-wing-butterfly",
                    )
                    candidate.metadata.update({"left_width": left_width, "right_width": right_width})
                    result.append(candidate)
    return _finish(result, limit)


def scan_risk_reversals(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls, puts = _quotes(chain, "CE"), _quotes(chain, "PE")
    put_candidates = [quote for quote in puts if spot * 0.92 <= quote.strike < spot]
    call_candidates = [quote for quote in calls if spot < quote.strike <= spot * 1.08]
    for put in put_candidates:
        for call in call_candidates:
            if abs((spot - put.strike) - (call.strike - spot)) > 300:
                continue
            bullish = [_leg("SELL", put, chain.expiry), _leg("BUY", call, chain.expiry)]
            bearish = [_leg("BUY", put, chain.expiry), _leg("SELL", call, chain.expiry)]
            liquidity = min(_liquidity(put), _liquidity(call))
            result.append(_build_fixed_candidate("Bullish Risk Reversal", "Bullish", bullish, spot, chain.lot_size, liquidity, "risk-reversal", ["Risk can be large when the naked short option moves in the money."]))
            result.append(_build_fixed_candidate("Bearish Risk Reversal", "Bearish", bearish, spot, chain.lot_size, liquidity, "risk-reversal", ["Risk can be large when the naked short option moves in the money."]))
    return _finish(result, limit)


def scan_straddles(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    rows = sorted(chain.rows, key=lambda row: abs(row.strike - spot))[:8]
    for row in rows:
        if not row.ce or not row.pe or not _tradable(row.ce) or not _tradable(row.pe):
            continue
        for long_side in (True, False):
            legs = [
                _leg("BUY" if long_side else "SELL", row.ce, chain.expiry),
                _leg("BUY" if long_side else "SELL", row.pe, chain.expiry),
            ]
            liquidity = min(_liquidity(row.ce), _liquidity(row.pe))
            result.append(
                _build_fixed_candidate(
                    f"{'Long' if long_side else 'Short'} Straddle",
                    "Volatility expansion" if long_side else "Volatility contraction",
                    legs,
                    spot,
                    chain.lot_size,
                    liquidity,
                    "straddle",
                )
            )
    return _finish(result, limit)


def scan_strangles(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls = sorted(
        [quote for quote in _quotes(chain, "CE") if spot < quote.strike <= spot * 1.08],
        key=lambda quote: abs(quote.strike - spot),
    )[:14]
    puts = sorted(
        [quote for quote in _quotes(chain, "PE") if spot * 0.92 <= quote.strike < spot],
        key=lambda quote: abs(quote.strike - spot),
    )[:14]
    for put in puts:
        for call in calls:
            if abs((spot - put.strike) - (call.strike - spot)) > 500:
                continue
            for long_side in (True, False):
                legs = [
                    _leg("BUY" if long_side else "SELL", call, chain.expiry),
                    _leg("BUY" if long_side else "SELL", put, chain.expiry),
                ]
                liquidity = min(_liquidity(call), _liquidity(put))
                result.append(
                    _build_fixed_candidate(
                        f"{'Long' if long_side else 'Short'} Strangle",
                        "Volatility expansion" if long_side else "Volatility contraction",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "strangle",
                    )
                )
    return _finish(result, limit)


def scan_jade_lizards(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls, puts = _quotes(chain, "CE"), _quotes(chain, "PE")
    short_puts = [quote for quote in puts if spot * 0.93 <= quote.strike < spot]
    short_calls = [quote for quote in calls if spot < quote.strike <= spot * 1.06]
    for put in short_puts:
        for short_call in short_calls:
            wings = [
                quote
                for quote in calls
                if short_call.strike < quote.strike <= short_call.strike + 400
            ][:4]
            for long_call in wings:
                credit = (
                    _price(put) + _price(short_call) - _price(long_call)
                )
                if credit <= 0:
                    continue
                legs = [
                    _leg("SELL", put, chain.expiry),
                    _leg("SELL", short_call, chain.expiry),
                    _leg("BUY", long_call, chain.expiry),
                ]
                liquidity = min(
                    _liquidity(put),
                    _liquidity(short_call),
                    _liquidity(long_call),
                )
                result.append(
                    _build_fixed_candidate(
                        "Jade Lizard",
                        "Neutral to moderately bullish",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "jade-lizard",
                        [
                            "The naked short put creates substantial downside risk.",
                            "Prefer total credit at least as large as the call-spread width to remove upside loss.",
                        ],
                    )
                )
    return _finish(result, limit)


def scan_box_spreads(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls = {quote.strike: quote for quote in _quotes(chain, "CE")}
    puts = {quote.strike: quote for quote in _quotes(chain, "PE")}
    shared = sorted(set(calls) & set(puts))
    for index, low_strike in enumerate(shared):
        if abs(low_strike - spot) / spot > 0.08:
            continue
        for high_strike in shared[index + 1 : index + 7]:
            width = high_strike - low_strike
            legs = [
                _leg("BUY", calls[low_strike], chain.expiry),
                _leg("SELL", calls[high_strike], chain.expiry),
                _leg("BUY", puts[high_strike], chain.expiry),
                _leg("SELL", puts[low_strike], chain.expiry),
            ]
            cost = sum(
                (leg.price if leg.action == "BUY" else -leg.price) * leg.quantity
                for leg in legs
            )
            if cost <= 0 or cost >= width * 1.25:
                continue
            liquidity = min(
                _liquidity(calls[low_strike]),
                _liquidity(calls[high_strike]),
                _liquidity(puts[low_strike]),
                _liquidity(puts[high_strike]),
            )
            candidate = _build_fixed_candidate(
                "Long Box Spread",
                "Synthetic financing / pricing discrepancy",
                legs,
                spot,
                chain.lot_size,
                liquidity,
                "box-spread",
                [
                    "The displayed edge excludes brokerage, taxes, slippage, assignment mechanics and funding costs."
                ],
            )
            candidate.metadata["box_width"] = width
            candidate.metadata["expiry_value"] = width * chain.lot_size
            result.append(candidate)
    return _finish(result, limit)


def scan_seagulls(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls, puts = _quotes(chain, "CE"), _quotes(chain, "PE")
    near_calls = sorted(calls, key=lambda quote: abs(quote.strike - spot))[:6]
    near_puts = sorted(puts, key=lambda quote: abs(quote.strike - spot))[:6]
    for long_call in near_calls:
        higher_calls = [
            quote
            for quote in calls
            if long_call.strike < quote.strike <= long_call.strike + 400
        ][:3]
        lower_puts = [
            quote for quote in puts if spot * 0.93 <= quote.strike < spot
        ][-5:]
        for short_call in higher_calls:
            for short_put in lower_puts:
                legs = [
                    _leg("BUY", long_call, chain.expiry),
                    _leg("SELL", short_call, chain.expiry),
                    _leg("SELL", short_put, chain.expiry),
                ]
                liquidity = min(
                    _liquidity(long_call),
                    _liquidity(short_call),
                    _liquidity(short_put),
                )
                result.append(
                    _build_fixed_candidate(
                        "Bullish Seagull",
                        "Moderately bullish",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "seagull",
                        ["The short put leaves substantial downside exposure."],
                    )
                )
    for long_put in near_puts:
        lower_puts = [
            quote
            for quote in puts
            if long_put.strike - 400 <= quote.strike < long_put.strike
        ][-3:]
        higher_calls = [
            quote for quote in calls if spot < quote.strike <= spot * 1.07
        ][:5]
        for short_put in lower_puts:
            for short_call in higher_calls:
                legs = [
                    _leg("BUY", long_put, chain.expiry),
                    _leg("SELL", short_put, chain.expiry),
                    _leg("SELL", short_call, chain.expiry),
                ]
                liquidity = min(
                    _liquidity(long_put),
                    _liquidity(short_put),
                    _liquidity(short_call),
                )
                result.append(
                    _build_fixed_candidate(
                        "Bearish Seagull",
                        "Moderately bearish",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "seagull",
                        ["The short call leaves substantial upside exposure."],
                    )
                )
    return _finish(result, limit)


def scan_christmas_trees(
    chain: OptionChain, limit: int = 10
) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for kind, outlook in (("CE", "Controlled bullish move"), ("PE", "Controlled bearish move")):
        quotes = _quotes(chain, kind)
        by_strike = {quote.strike: quote for quote in quotes}
        for first in quotes:
            if abs(first.strike - spot) / spot > 0.05:
                continue
            for width in (50, 100, 150, 200):
                direction = 1 if kind == "CE" else -1
                middle = by_strike.get(first.strike + direction * width)
                far = by_strike.get(first.strike + direction * width * 2)
                if not middle or not far:
                    continue
                legs = [
                    _leg("BUY", first, chain.expiry),
                    _leg("SELL", middle, chain.expiry, quantity=3),
                    _leg("BUY", far, chain.expiry, quantity=2),
                ]
                liquidity = min(
                    _liquidity(first), _liquidity(middle), _liquidity(far)
                )
                result.append(
                    _build_fixed_candidate(
                        f"{kind} Christmas Tree",
                        outlook,
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "christmas-tree",
                    )
                )
    return _finish(result, limit)


def scan_guts(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    calls = [
        quote for quote in _quotes(chain, "CE") if spot * 0.94 <= quote.strike < spot
    ]
    puts = [
        quote for quote in _quotes(chain, "PE") if spot < quote.strike <= spot * 1.06
    ]
    for call in calls:
        for put in puts:
            if put.strike - call.strike > 500:
                continue
            liquidity = min(_liquidity(call), _liquidity(put))
            for long_side in (True, False):
                action = "BUY" if long_side else "SELL"
                legs = [
                    _leg(action, call, chain.expiry),
                    _leg(action, put, chain.expiry),
                ]
                result.append(
                    _build_fixed_candidate(
                        f"{'Long' if long_side else 'Short'} Guts",
                        "Large move" if long_side else "Volatility contraction",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "guts",
                    )
                )
    return _finish(result, limit)


def _portfolio_metadata(
    units: int, average_cost: float, current_price: float
) -> dict:
    return {
        "units": units,
        "average_cost": average_cost,
        "current_price": current_price,
        "underlying_position": {
            "asset": "NIFTYBEES",
            "units": units,
            "average_cost": average_cost,
            "current_price": current_price,
        },
    }


def scan_collars(
    chain: OptionChain,
    units: int,
    average_cost: float,
    current_price: float,
    limit: int = 10,
) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    puts = [quote for quote in _quotes(chain, "PE") if spot * 0.92 <= quote.strike < spot]
    calls = [quote for quote in _quotes(chain, "CE") if spot < quote.strike <= spot * 1.08]
    metadata = _portfolio_metadata(units, average_cost, current_price)
    for put in puts:
        for call in calls:
            if abs((spot - put.strike) - (call.strike - spot)) > 500:
                continue
            legs = [
                _leg("BUY", put, chain.expiry),
                _leg("SELL", call, chain.expiry),
            ]
            liquidity = min(_liquidity(put), _liquidity(call))
            result.append(
                _build_fixed_candidate(
                    "NIFTYBEES Collar Proxy",
                    "Protective / moderately bullish",
                    legs,
                    spot,
                    chain.lot_size,
                    liquidity,
                    "collar",
                    [
                        "NIFTYBEES and cash-settled NIFTY options have tracking, multiplier and basis risk."
                    ],
                    metadata,
                )
            )
    return _finish(result, limit)


def scan_fences(
    chain: OptionChain,
    units: int,
    average_cost: float,
    current_price: float,
    limit: int = 10,
) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    puts = _quotes(chain, "PE")
    calls = _quotes(chain, "CE")
    metadata = _portfolio_metadata(units, average_cost, current_price)
    protective_puts = [quote for quote in puts if spot * 0.94 <= quote.strike < spot]
    for long_put in protective_puts:
        lower_puts = [
            quote for quote in puts if long_put.strike - 400 <= quote.strike < long_put.strike
        ][-3:]
        higher_calls = [
            quote for quote in calls if spot < quote.strike <= spot * 1.08
        ][:6]
        for short_put in lower_puts:
            for short_call in higher_calls:
                legs = [
                    _leg("BUY", long_put, chain.expiry),
                    _leg("SELL", short_put, chain.expiry),
                    _leg("SELL", short_call, chain.expiry),
                ]
                liquidity = min(
                    _liquidity(long_put),
                    _liquidity(short_put),
                    _liquidity(short_call),
                )
                result.append(
                    _build_fixed_candidate(
                        "NIFTYBEES Fence Proxy",
                        "Downside hedge with capped upside",
                        legs,
                        spot,
                        chain.lot_size,
                        liquidity,
                        "fence",
                        [
                            "Protection below the lower short put is reduced.",
                            "NIFTYBEES and cash-settled NIFTY options have tracking, multiplier and basis risk.",
                        ],
                        metadata,
                    )
                )
    return _finish(result, limit)


def scan_poor_mans_covered_calls(
    near: OptionChain, far: OptionChain, limit: int = 10
) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = near.underlying_value
    long_calls = [
        quote
        for quote in _quotes(far, "CE")
        if spot * 0.8 <= quote.strike <= spot * 0.95
    ]
    short_calls = [
        quote
        for quote in _quotes(near, "CE")
        if spot < quote.strike <= spot * 1.08
    ]
    for long_call in long_calls:
        for short_call in short_calls:
            debit = _price(long_call) - _price(short_call)
            if debit <= 0:
                continue
            legs = [
                _leg("BUY", long_call, far.expiry),
                _leg("SELL", short_call, near.expiry),
            ]
            liquidity = min(_liquidity(long_call), _liquidity(short_call))
            candidate = StrategyCandidate(
                id=_candidate_id("Poor Man's Covered Call", legs),
                strategy="Poor Man's Covered Call",
                outlook="Moderately bullish",
                score=_balanced_score(
                    liquidity,
                    max(_price(short_call), 0) / max(debit, 1),
                    abs(short_call.strike - spot) / spot,
                    abs(long_call.strike - spot) / spot,
                ),
                legs=legs,
                net_debit=round(debit * near.lot_size, 2),
                metric_mode="modeled",
                liquidity_score=liquidity,
                notes=[
                    "This is a time spread, not a fully covered stock position.",
                    "Results depend on IV, time decay and the remaining value of the long-dated call.",
                ],
                pricing_assumptions=[
                    "Black-Scholes valuation, 6.5% risk-free rate, no dividend yield."
                ],
                metadata={
                    "premium_basis": "LTP",
                    "lot_size": near.lot_size,
                    "payoff_type": "modeled",
                    "bounded_profit": True,
                    "bounded_loss": True,
                    "strategy_family": "poor-mans-covered-call",
                    "near_expiry": near.expiry,
                    "far_expiry": far.expiry,
                },
            )
            result.append(candidate)
    finalists = _finish(result, limit)
    from .analysis import analyze_candidate
    from .models import AnalysisRequest

    for candidate in finalists:
        modeled = analyze_candidate(
            AnalysisRequest(
                candidate=candidate,
                underlying_value=spot,
                lot_size=near.lot_size,
                chain_timestamp=near.timestamp,
                evaluation_days=3650,
            )
        )
        candidate.estimated_peak_profit = modeled.estimated_peak_profit
        candidate.modeled_worst_loss = modeled.modeled_worst_loss
        candidate.modeled_return_risk = modeled.modeled_return_risk
        candidate.estimated_breakevens = modeled.estimated_breakevens
    return finalists


def scan_strips(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for row in sorted(chain.rows, key=lambda item: abs(item.strike - spot))[:8]:
        if not row.ce or not row.pe or not _tradable(row.ce) or not _tradable(row.pe):
            continue
        legs = [
            _leg("BUY", row.ce, chain.expiry),
            _leg("BUY", row.pe, chain.expiry, quantity=2),
        ]
        result.append(
            _build_fixed_candidate(
                "Strip",
                "Large move with bearish bias",
                legs,
                spot,
                chain.lot_size,
                min(_liquidity(row.ce), _liquidity(row.pe)),
                "strip",
            )
        )
    return _finish(result, limit)


def scan_straps(chain: OptionChain, limit: int = 10) -> list[StrategyCandidate]:
    result: list[StrategyCandidate] = []
    spot = chain.underlying_value
    for row in sorted(chain.rows, key=lambda item: abs(item.strike - spot))[:8]:
        if not row.ce or not row.pe or not _tradable(row.ce) or not _tradable(row.pe):
            continue
        legs = [
            _leg("BUY", row.ce, chain.expiry, quantity=2),
            _leg("BUY", row.pe, chain.expiry),
        ]
        result.append(
            _build_fixed_candidate(
                "Strap",
                "Large move with bullish bias",
                legs,
                spot,
                chain.lot_size,
                min(_liquidity(row.ce), _liquidity(row.pe)),
                "strap",
            )
        )
    return _finish(result, limit)
