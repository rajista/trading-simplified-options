from __future__ import annotations

import re
from typing import Literal

from .models import (
    DeskAnalysis,
    DetailedReportAnalysis,
    MarketEvidencePacket,
    StrategyCandidate,
)


PROMPT_VERSION = "desk-v5-concise"


def word_count(analysis: DetailedReportAnalysis) -> int:
    text = " ".join(
        [
            analysis.executive_summary,
            analysis.price_action_analysis,
            analysis.option_chain_analysis,
            *analysis.global_cues,
            analysis.news_event_risk,
            analysis.score_liquidity_analysis,
            analysis.strategy_rationale,
            analysis.entry_execution_plan,
            analysis.risk_analysis,
            analysis.adjustment_exit_plan,
            *analysis.monitoring_checklist,
            *analysis.supporting_evidence,
            *analysis.conflicting_evidence,
        ]
    )
    return len(re.findall(r"\b[\w%.-]+\b", text))


def strategy_family(candidate: StrategyCandidate) -> str:
    configured = candidate.metadata.get("strategy_family")
    if configured:
        return str(configured)
    name = candidate.strategy.lower()
    for family, marker in (
        ("calendar", "calendar"),
        ("diagonal", "diagonal"),
        ("iron-condor", "iron condor"),
        ("broken-wing-butterfly", "broken-wing"),
        ("butterfly", "butterfly"),
        ("risk-reversal", "risk reversal"),
        ("straddle", "straddle"),
        ("strangle", "strangle"),
        ("credit", "credit spread"),
        ("debit", "debit spread"),
    ):
        if marker in name:
            return family
    return name


def risk_label(candidate: StrategyCandidate) -> str:
    if candidate.metric_mode == "modeled":
        if candidate.modeled_worst_loss is None:
            return "Modeled range loss is unavailable."
        return (
            "The modeled worst loss inside the displayed scenario range is INR "
            f"{candidate.modeled_worst_loss:,.0f}; this is not a guaranteed maximum loss."
        )
    if not candidate.metadata.get("bounded_loss", candidate.max_loss is not None):
        return "Maximum loss is Unlimited and must not be converted into a fixed rupee estimate."
    if candidate.max_loss is not None:
        return f"The exact expiry maximum loss is INR {candidate.max_loss:,.0f} per configured lot."
    return "The server could not establish a reliable maximum-loss figure."


def classify_evidence(packet: MarketEvidencePacket) -> dict[str, str]:
    indicators = packet.technical_indicators
    summary = packet.option_chain_summary
    short = "Unavailable"
    if indicators.last and indicators.ema_9 and indicators.ema_21:
        if indicators.last > indicators.ema_9 > indicators.ema_21:
            short = "Bullish"
        elif indicators.last < indicators.ema_9 < indicators.ema_21:
            short = "Bearish"
        else:
            short = "Mixed"
    medium = "Unavailable"
    if indicators.last and indicators.sma_50:
        medium = "Bullish" if indicators.last > indicators.sma_50 else "Bearish"
        if abs(indicators.last / indicators.sma_50 - 1) < 0.005:
            medium = "Sideways"
    rsi = indicators.rsi_14
    histogram = indicators.macd_histogram or 0
    if rsi is None:
        momentum = "Unavailable"
    elif rsi >= 60 and histogram > 0:
        momentum = "Strong positive"
    elif rsi <= 40 and histogram < 0:
        momentum = "Strong negative"
    elif rsi >= 52:
        momentum = "Moderately positive"
    elif rsi <= 48:
        momentum = "Moderately negative"
    else:
        momentum = "Neutral"
    realized = indicators.realized_volatility_20d
    volatility = (
        "Unavailable"
        if realized is None
        else "High"
        if realized >= 22
        else "Low"
        if realized <= 12
        else "Normal"
    )
    if summary.atm_iv is None or realized is None:
        iv_regime = "Unavailable"
    else:
        spread = summary.atm_iv - realized
        iv_regime = (
            f"Rich by {spread:.1f} volatility points"
            if spread >= 2
            else f"Cheap by {abs(spread):.1f} volatility points"
            if spread <= -2
            else "Near realized volatility"
        )
    pcr = summary.near_atm_oi_pcr or summary.total_oi_pcr
    chain_bias = (
        "Unavailable"
        if pcr is None
        else "Put-heavy support bias"
        if pcr >= 1.2
        else "Call-heavy resistance bias"
        if pcr <= 0.8
        else "Balanced"
    )
    high_events = sum(event.importance == "high" for event in packet.market_events)
    event_risk = (
        "High"
        if high_events
        else "Moderate"
        if packet.market_events
        else "No verified nearby event"
    )
    return {
        "short_term_trend": short,
        "medium_term_trend": medium,
        "momentum_strength": momentum,
        "volatility_regime": volatility,
        "iv_premium_regime": iv_regime,
        "option_chain_bias": chain_bias,
        "event_risk": event_risk,
    }


def _aligned(candidate: StrategyCandidate, packet: MarketEvidencePacket) -> bool:
    outlook = candidate.outlook.lower()
    trend = packet.short_term_trend
    if "bull" in outlook:
        return trend == "Bullish"
    if "bear" in outlook:
        return trend == "Bearish"
    if "neutral" in outlook or "range" in outlook:
        return trend in {"Mixed", "Sideways"}
    return False


def decision_for(
    candidate: StrategyCandidate, packet: MarketEvidencePacket
) -> Literal["Consider", "Wait", "Avoid"]:
    aligned = _aligned(candidate, packet)
    if candidate.liquidity_score < 50 or (
        not candidate.metadata.get("bounded_loss", True) and not aligned
    ):
        return "Avoid"
    if (
        candidate.score >= 80
        and candidate.liquidity_score >= 75
        and aligned
        and packet.event_risk != "High"
    ):
        return "Consider"
    return "Wait"


def _fallback_copy(
    candidate: StrategyCandidate, packet: MarketEvidencePacket
) -> tuple[str, str, str]:
    family = strategy_family(candidate)
    event_tail = (
        " A nearby event can still upset the setup, so keep the position smaller than usual."
        if packet.event_risk == "High"
        else ""
    )
    if family == "credit":
        thesis = (
            "The range has been holding and premium is elevated enough to make selling the spread worthwhile. "
            "This works as long as the market stays away from the short strike into expiry."
        )
        entry = "Enter the vertical as one limit-order package and skip it if the credit deteriorates."
        risk_exit = "Exit when price starts accepting beyond the short strike instead of waiting for the hedge to rescue the trade."
    elif family == "debit":
        thesis = (
            "The directional bias is present but not strong enough for an outright option purchase, so the spread keeps the cost controlled. "
            "The idea needs momentum to build soon rather than drift sideways."
        )
        entry = "Enter both legs together only while price action still supports the intended direction."
        risk_exit = "Exit if momentum fades before price begins moving toward the target zone."
    elif family == "iron-condor":
        thesis = (
            "Price is still rotating inside a broad range and neither side has established control, which favors collecting premium on both sides. "
            "The trade loses its edge if the market starts accepting beyond either short strike."
        )
        entry = "Use one four-leg combination order so neither short option is left unhedged."
        risk_exit = "Reduce the threatened side promptly when the range breaks with follow-through."
    elif family in {"butterfly", "broken-wing-butterfly"}:
        thesis = (
            "The market looks more likely to settle near a target zone than make a sustained directional move, making the concentrated payoff attractive. "
            "It needs early adjustment if price moves away from the body strike."
        )
        entry = "Enter the complete butterfly at a limit price that preserves the asymmetric payoff."
        risk_exit = "Leave early if the target zone stops acting like a magnet and momentum expands away from it."
    elif family in {"calendar", "diagonal"}:
        thesis = (
            "Near-term movement looks contained while the later expiry still carries useful time value, which supports selling the near option against the longer-dated leg. "
            "A sharp move or sudden volatility shift can quickly weaken the modeled advantage."
        )
        entry = "Trade both expiries as one package and avoid the setup if the term structure shifts before the fill."
        risk_exit = "Exit or reshape the position when spot leaves the intended zone or the far leg stops retaining value."
    elif family in {"straddle", "strangle"}:
        thesis = (
            "The opportunity depends on whether the next move is likely to be larger or smaller than the market expects. "
            "This is a volatility trade first, so direction alone is not enough to justify it."
        )
        entry = "Enter both options together and avoid chasing after volatility has already repriced."
        risk_exit = "Close when realized movement and implied volatility stop supporting the original volatility view."
    else:
        thesis = (
            "The structure fits the current market bias, but the edge is conditional rather than decisive. "
            "Treat it as a planned payoff with a clear invalidation point, not a prediction."
        )
        entry = "Use a complete combination order and accept the trade only at the intended package price."
        risk_exit = "Exit when the market behavior no longer matches the structure's directional or volatility premise."
    return thesis + event_tail, entry, risk_exit


def build_rules_analysis(
    candidate: StrategyCandidate, packet: MarketEvidencePacket
) -> DeskAnalysis:
    thesis, entry, risk_exit = _fallback_copy(candidate, packet)
    supporting = [
        "The strategy structure is compatible with the current trend and volatility mix."
        if _aligned(candidate, packet)
        else "The payoff remains defined even though directional confirmation is incomplete.",
        "The displayed liquidity is sufficient to review the structure with a combination order.",
    ]
    conflicting = [
        "A fast change in direction or volatility can invalidate the setup before expiry."
    ]
    if packet.event_risk == "High":
        conflicting.append("Nearby event risk can create a gap before an adjustment is possible.")
    return DeskAnalysis(
        decision=decision_for(candidate, packet),
        thesis=thesis,
        entry=entry,
        risk_exit=risk_exit,
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
    )


def build_detailed_rules_analysis(
    candidate: StrategyCandidate, packet: MarketEvidencePacket
) -> DetailedReportAnalysis:
    concise = build_rules_analysis(candidate, packet)
    risk = risk_label(candidate)
    family = strategy_family(candidate)
    return DetailedReportAnalysis(
        decision=concise.decision,
        executive_summary=concise.thesis,
        price_action_analysis=(
            "Use the prevailing trend and momentum classification as confirmation, not as "
            "a standalone entry signal. A change in market character invalidates the setup."
        ),
        option_chain_analysis=(
            "The option-chain structure supports the selected payoff only while liquidity, "
            "volatility and positioning remain broadly consistent with the snapshot."
        ),
        global_cues=[
            "Use fresh cross-market moves as a sizing input when they reinforce or contradict the domestic setup."
        ],
        news_event_risk=(
            "Verified events can cause gaps that bypass ordinary adjustment levels; reduce size "
            "or delay entry when event risk is elevated."
        ),
        score_liquidity_analysis=(
            "The scanner ranking prioritizes this structure for review, while executable package "
            "pricing remains the final test."
        ),
        strategy_rationale=concise.thesis,
        entry_execution_plan=concise.entry,
        risk_analysis=risk,
        adjustment_exit_plan=concise.risk_exit,
        monitoring_checklist=[
            "Confirm the live package price before entry.",
            "Watch whether price remains inside the intended payoff region.",
            "Reassess after a material volatility change.",
            "Exit when the stated invalidation condition appears.",
            "Reduce exposure when evidence or quotes become stale.",
        ],
        supporting_evidence=concise.supporting_evidence,
        conflicting_evidence=concise.conflicting_evidence,
        word_count=0,
    )
