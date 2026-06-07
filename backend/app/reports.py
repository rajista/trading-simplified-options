from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque

import httpx

from .config import settings
from .models import ReportRequest, TradeReport


class ReportUnavailable(RuntimeError):
    pass


class ReportRateLimit(RuntimeError):
    pass


class ReportService:
    def __init__(self) -> None:
        self.cache: dict[str, TradeReport] = {}
        self.requests: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def _check_limit(self, client_ip: str) -> None:
        now = time.time()
        with self.lock:
            history = self.requests[client_ip]
            while history and now - history[0] > 86400:
                history.popleft()
            if history and now - history[-1] < 60:
                raise ReportRateLimit(
                    "Please wait one minute before generating another Gemini report."
                )
            if len(history) >= 10:
                raise ReportRateLimit(
                    "Daily Gemini report limit reached for this IP address."
                )
            history.append(now)

    @staticmethod
    def _rules_report(request: ReportRequest) -> TradeReport:
        candidate = request.candidate
        analysis = request.analysis or {}
        context = request.market_context
        leg_text = "; ".join(
            f"{leg.action} {leg.quantity}x {leg.option_type} {leg.strike:g} "
            f"expiring {leg.expiry}"
            for leg in candidate.legs
        )
        peak = analysis.get("estimated_peak_profit", candidate.estimated_peak_profit)
        loss = analysis.get("modeled_worst_loss", candidate.modeled_worst_loss)
        breakevens = analysis.get(
            "estimated_breakevens",
            candidate.estimated_breakevens or candidate.breakevens,
        )
        payoff_parts = []
        if candidate.net_debit is not None:
            payoff_parts.append(
                f"Entry requires an estimated net debit of INR {candidate.net_debit:,.0f}."
            )
        if candidate.net_credit is not None:
            payoff_parts.append(
                f"Entry produces an estimated net credit of INR {candidate.net_credit:,.0f}."
            )
        if peak is not None:
            payoff_parts.append(
                f"Peak modeled profit in the selected range is INR {peak:,.0f}."
            )
        if loss is not None:
            payoff_parts.append(
                f"Worst modeled loss in that range is INR {loss:,.0f}."
            )
        risks = [
            "Quoted prices may change before execution and bid/ask slippage can materially alter results.",
            "Implied volatility and time decay can move the position differently from the displayed scenario.",
        ]
        if candidate.metric_mode == "modeled":
            risks.append(
                "Time-spread peak profit, loss and breakevens are estimates, not guaranteed limits."
            )

        short_trend = context.short_term_trend if context else "Unavailable"
        medium_trend = context.medium_term_trend if context else "Unavailable"
        momentum = (
            f"{context.momentum}; volatility regime: {context.volatility_regime}"
            if context
            else "Unavailable"
        )
        alignment = (
            "Trend context is mixed or unavailable; wait for confirming price action "
            "rather than treating the ranking as an entry signal."
        )
        outlook = candidate.outlook.lower()
        if "bull" in outlook and short_trend == "Bullish":
            alignment = "The short-term trend aligns with the strategy's bullish bias."
        elif "bear" in outlook and short_trend == "Bearish":
            alignment = "The short-term trend aligns with the strategy's bearish bias."
        elif (
            ("neutral" in outlook or "range" in outlook)
            and short_trend == "Sideways"
        ):
            alignment = "The sideways short-term trend aligns with the strategy's neutral bias."

        assumptions = list(
            dict.fromkeys(
                request.assumptions
                + candidate.pricing_assumptions
                + list(analysis.get("assumptions", []))
            )
        )
        timestamps = [request.chain_timestamp]
        if context and context.data_timestamp:
            timestamps.append(context.data_timestamp)
        return TradeReport(
            title=f"{candidate.strategy} trade and risk review",
            setup=leg_text,
            rationale=(
                f"This {candidate.outlook.lower()} structure ranked "
                f"{candidate.score:.0f}/100 with liquidity "
                f"{candidate.liquidity_score:.0f}/100."
            ),
            payoff=" ".join(payoff_parts)
            or "Review the interactive payoff chart for the selected scenario.",
            breakevens=[f"NIFTY {value:,.2f}" for value in breakevens]
            or ["No breakeven was found inside the selected chart range."],
            favorable_scenarios=[
                "The underlying moves into the chart's profitable region.",
                "Implied volatility behaves near the selected assumption.",
            ],
            adverse_scenarios=[
                "The underlying moves outside the modeled profitable region.",
                "Bid/ask spreads widen or the volatility term structure shifts adversely.",
            ],
            liquidity_concerns=[
                f"Combined liquidity score is {candidate.liquidity_score:.0f}/100.",
                "Use limit orders and confirm executable prices for every leg.",
            ],
            exit_considerations=[
                "Define a loss limit and profit objective before entry.",
                "Recheck the payoff after material spot, IV or time changes.",
            ],
            risks=risks,
            assumptions=assumptions,
            short_term_trend=short_trend,
            medium_term_trend=medium_trend,
            momentum_and_volatility=momentum,
            global_macro_context=(
                context.global_macro_context
                if context and context.global_macro_context
                else ["No verified global macro context was supplied."]
            ),
            upcoming_events=(
                context.upcoming_events
                if context and context.upcoming_events
                else ["No verified upcoming events were supplied."]
            ),
            strategy_suitability=alignment,
            trade_recommendation=(
                f"Educational trade plan: {alignment} Confirm the current option "
                "quotes, payoff and liquidity before considering entry."
            ),
            entry_conditions=[
                "Confirm every leg has a valid bid/ask market and acceptable slippage.",
                "Enter only while trend and volatility still fit the stated outlook.",
            ],
            adjustment_conditions=[
                "Reassess if spot crosses a breakeven or the directional thesis changes.",
                "Reprice the full structure after a material IV or time change.",
            ],
            position_sizing_cautions=[
                "Size from the displayed worst-case or modeled loss, not premium received.",
                "Reduce size for unbounded, proxy-hedged or weak-liquidity structures.",
            ],
            confidence="low" if not context or context.stale else "medium",
            data_timestamps=list(dict.fromkeys(timestamps)),
            sources=context.sources if context else [],
            disclaimer=(
                "Educational analysis only. This is not personalized investment "
                "advice or a recommendation to trade. Verify calculations, context "
                "and market prices independently."
            ),
            generated_by="rules",
        )

    def _gemini_report(self, request: ReportRequest) -> TradeReport:
        schema = TradeReport.model_json_schema()
        prompt = (
            "Create a concise educational NIFTY options trade analysis using only "
            "the supplied calculated data and market_context. Never invent current "
            "news, macro conditions, events, prices or sources. If context is "
            "unavailable or stale, state that and lower confidence. Recommendations "
            "must be conditional trade-plan guidance, not personalized investment "
            "advice. Treat modeled time-spread values as estimates. Set "
            'generated_by to "gemini".\n\n'
            + json.dumps(request.model_dump(mode="json"), indent=2)
        )
        response = httpx.post(
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{settings.gemini_model}:generateContent"
            ),
            headers={
                "x-goog-api-key": settings.gemini_api_key or "",
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                    "temperature": 0.2,
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        report = TradeReport.model_validate_json(text)
        return report.model_copy(update={"generated_by": "gemini"})

    def generate(self, request: ReportRequest, client_ip: str) -> TradeReport:
        payload = request.model_dump(mode="json")
        cache_key = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        if cache_key in self.cache:
            return self.cache[cache_key]

        report: TradeReport
        if settings.gemini_api_key:
            try:
                self._check_limit(client_ip)
                report = self._gemini_report(request)
            except (ReportRateLimit, httpx.HTTPError, KeyError, ValueError, TypeError):
                report = self._rules_report(request)
        else:
            report = self._rules_report(request)
        self.cache[cache_key] = report
        return report


report_service = ReportService()
