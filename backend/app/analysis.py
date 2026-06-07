from __future__ import annotations

from datetime import datetime, timedelta

from .models import (
    AnalysisPoint,
    AnalysisRequest,
    AnalysisResponse,
    StrategyCandidate,
)
from .strategies import black_scholes


DATE_FORMATS = ("%d-%b-%Y", "%d-%m-%Y", "%d-%b-%Y %H:%M:%S")


def parse_market_date(value: str) -> datetime:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as error:
        raise ValueError(f"Unsupported market date: {value}") from error


def days_to_expiry(chain_timestamp: str, expiry: str) -> int:
    start = parse_market_date(chain_timestamp).date()
    end = parse_market_date(expiry).date()
    return max(0, (end - start).days)


def _leg_value(
    candidate: StrategyCandidate,
    underlying_price: float,
    reference_spot: float,
    chain_timestamp: str,
    evaluation_days: int,
    iv_shift: float,
    lot_size: int,
) -> float:
    total = 0.0
    for leg in candidate.legs:
        remaining_days = max(
            0, days_to_expiry(chain_timestamp, leg.expiry) - evaluation_days
        )
        if candidate.metadata.get("payoff_type") == "expiry" and remaining_days == 0:
            theoretical = (
                max(0, underlying_price - leg.strike)
                if leg.option_type == "CE"
                else max(0, leg.strike - underlying_price)
            )
        else:
            volatility = max(0.01, leg.implied_volatility + iv_shift)
            theoretical = black_scholes(
                underlying_price,
                leg.strike,
                remaining_days / 365,
                volatility,
                leg.option_type,
            )
        direction = 1 if leg.action == "BUY" else -1
        total += direction * (theoretical - leg.price) * lot_size * leg.quantity
    position = candidate.metadata.get("underlying_position")
    if not position and candidate.strategy == "NIFTYBEES Covered Call Proxy":
        position = candidate.metadata
    if position:
        units = float(position.get("units") or 0)
        current_price = float(position.get("current_price") or 0)
        cost_basis = float(position.get("average_cost") or current_price)
        if units > 0 and current_price > 0:
            scenario_etf_price = current_price * underlying_price / reference_spot
            total += (scenario_etf_price - cost_basis) * units
    return total


def _breakevens(points: list[tuple[float, float]]) -> list[float]:
    results: list[float] = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        if y1 == 0:
            results.append(x1)
        elif y1 * y2 < 0:
            ratio = abs(y1) / (abs(y1) + abs(y2))
            results.append(x1 + (x2 - x1) * ratio)
    unique: list[float] = []
    for value in results:
        rounded = round(value, 2)
        if not unique or abs(unique[-1] - rounded) > 0.5:
            unique.append(rounded)
    return unique


def analyze_candidate(request: AnalysisRequest) -> AnalysisResponse:
    candidate = request.candidate
    expiry_days = [
        days_to_expiry(request.chain_timestamp, leg.expiry)
        for leg in candidate.legs
    ]
    max_evaluation_days = min(expiry_days) if expiry_days else 0
    evaluation_days = min(request.evaluation_days, max_evaluation_days)
    lower = request.underlying_value * (1 - request.price_range_pct / 100)
    upper = request.underlying_value * (1 + request.price_range_pct / 100)
    step = (upper - lower) / 80
    raw_points: list[tuple[float, float, float]] = []
    for index in range(81):
        price = lower + index * step
        today_pnl = _leg_value(
            candidate,
            price,
            request.underlying_value,
            request.chain_timestamp,
            0,
            0,
            request.lot_size,
        )
        evaluation_pnl = _leg_value(
            candidate,
            price,
            request.underlying_value,
            request.chain_timestamp,
            evaluation_days,
            request.iv_shift,
            request.lot_size,
        )
        raw_points.append((price, today_pnl, evaluation_pnl))

    evaluation_values = [(price, pnl) for price, _, pnl in raw_points]
    peak = max(pnl for _, pnl in evaluation_values)
    minimum = min(pnl for _, pnl in evaluation_values)
    worst_loss = abs(min(0, minimum))
    if candidate.metadata.get("bounded_profit") is False:
        peak = request.candidate.max_profit if request.candidate.max_profit is not None else peak
    if candidate.metadata.get("bounded_loss") is False:
        worst_loss = request.candidate.max_loss if request.candidate.max_loss is not None else worst_loss
    return_risk = peak / worst_loss * 100 if peak and peak > 0 and worst_loss and worst_loss > 0 else None
    evaluation_date = (
        parse_market_date(request.chain_timestamp) + timedelta(days=evaluation_days)
    ).date()
    assumptions = [
        "Black-Scholes model with a 6.5% risk-free rate and no dividend yield.",
        "Each leg uses its quoted implied volatility plus the selected IV shift.",
        (
            f"Worst loss is the minimum modeled P&L inside the "
            f"±{request.price_range_pct:g}% chart range, not a guaranteed maximum loss."
        ),
    ]
    if candidate.metric_mode == "modeled":
        assumptions.append(
            "Time-spread profit, loss and breakevens vary with IV, time and the selected evaluation date."
        )
    return AnalysisResponse(
        points=[
            AnalysisPoint(
                underlying_price=round(price, 2),
                today_pnl=round(today, 2),
                evaluation_pnl=round(evaluation, 2),
            )
            for price, today, evaluation in raw_points
        ],
        spot=request.underlying_value,
        evaluation_days=evaluation_days,
        max_evaluation_days=max_evaluation_days,
        evaluation_label=evaluation_date.strftime("%d-%b-%Y"),
        estimated_peak_profit=round(peak, 2),
        modeled_worst_loss=round(worst_loss, 2),
        modeled_return_risk=round(return_risk, 2) if return_risk is not None else None,
        estimated_breakevens=_breakevens(evaluation_values),
        net_debit=candidate.net_debit,
        net_credit=candidate.net_credit,
        assumptions=assumptions,
    )
