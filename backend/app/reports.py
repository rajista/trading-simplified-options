from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import defaultdict, deque

import httpx

from .config import settings
from .desk_analysis import (
    PROMPT_VERSION,
    build_detailed_rules_analysis,
    word_count,
)
from .models import (
    MarketEvidencePacket,
    OptionChainSummary,
    ReportRequest,
    TechnicalIndicators,
    TradeReport,
)
from .provider import provider
from .recommendations import recommendation_service


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
                    "Please wait one minute before generating another AI report."
                )
            if len(history) >= 10:
                raise ReportRateLimit(
                    "Daily AI report limit reached for this IP address."
                )
            history.append(now)

    @staticmethod
    def _evidence(request: ReportRequest) -> MarketEvidencePacket:
        report_date = request.report_date or time.strftime("%Y-%m-%d")
        if request.expiry:
            chain = provider.get_chain(request.expiry)
            return recommendation_service.collect_evidence(chain, report_date)
        context = request.market_context
        packet = MarketEvidencePacket(
            report_date=report_date,
            chain_timestamp=request.chain_timestamp,
            technical_indicators=TechnicalIndicators(
                last=request.underlying_value,
                timestamp=context.data_timestamp if context else request.chain_timestamp,
                stale=not context or context.stale,
            ),
            option_chain_summary=OptionChainSummary(
                spot=request.underlying_value,
                timestamp=request.chain_timestamp,
                stale=True,
            ),
            global_markets=[],
            news=[],
            market_events=[],
            short_term_trend=context.short_term_trend if context else "Unavailable",
            medium_term_trend=context.medium_term_trend if context else "Unavailable",
            momentum_strength=context.momentum if context else "Unavailable",
            volatility_regime=context.volatility_regime if context else "Unavailable",
            iv_premium_regime="Unavailable",
            option_chain_bias="Unavailable",
            event_risk="Unavailable",
            stale_inputs=["enriched market evidence"],
        )
        return packet

    @staticmethod
    def _rules_report(
        request: ReportRequest,
        evidence: MarketEvidencePacket,
        fallback_reason: str | None = None,
    ) -> TradeReport:
        candidate = request.candidate
        analysis = request.analysis or {}
        context = request.market_context
        desk = build_detailed_rules_analysis(candidate, evidence)
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
            rationale=desk.executive_summary,
            payoff=" ".join(payoff_parts)
            or "Review the interactive payoff chart for the selected scenario.",
            breakevens=[f"NIFTY {value:,.2f}" for value in breakevens]
            or ["No breakeven was found inside the selected chart range."],
            favorable_scenarios=desk.supporting_evidence,
            adverse_scenarios=desk.conflicting_evidence,
            liquidity_concerns=[
                f"Combined liquidity score is {candidate.liquidity_score:.0f}/100.",
                "Use limit orders and confirm executable prices for every leg.",
            ],
            exit_considerations=[desk.adjustment_exit_plan],
            risks=risks,
            assumptions=assumptions,
            short_term_trend=evidence.short_term_trend,
            medium_term_trend=evidence.medium_term_trend,
            momentum_and_volatility=(
                f"{evidence.momentum_strength}; volatility {evidence.volatility_regime}; "
                f"IV premium {evidence.iv_premium_regime}."
            ),
            global_macro_context=desk.global_cues + (
                context.global_macro_context
                if context and context.global_macro_context
                else []
            ),
            upcoming_events=(
                [
                    f"{event.date}: {event.title} ({event.source})"
                    for event in evidence.market_events
                ]
                + (
                    context.upcoming_events
                    if context and context.upcoming_events
                    else []
                )
            ) or ["No verified upcoming events were supplied."],
            strategy_suitability=desk.strategy_rationale,
            trade_recommendation=f"{desk.decision}: {desk.executive_summary}",
            entry_conditions=[desk.entry_execution_plan],
            adjustment_conditions=[desk.adjustment_exit_plan],
            position_sizing_cautions=[
                "Size from the displayed worst-case or modeled loss, not premium received.",
                "Reduce size for unbounded, proxy-hedged or weak-liquidity structures.",
            ],
            confidence="low" if not context or context.stale else "medium",
            data_timestamps=list(
                dict.fromkeys(
                    timestamps
                    + [
                        value
                        for value in evidence.input_timestamps.values()
                        if value
                    ]
                )
            ),
            sources=list(
                dict.fromkeys(
                    (context.sources if context else [])
                    + ["Yahoo Finance market history", "NSE option-chain snapshot"]
                    + [item.source for item in evidence.news]
                    + [item.source for item in evidence.market_events]
                )
            ),
            disclaimer=(
                "Educational analysis only. This is not personalized investment "
                "advice or a recommendation to trade. Verify calculations, context "
                "and market prices independently."
            ),
            generated_by="rules",
            desk_analysis=desk,
            validation_status="rules-fallback",
            prompt_version=PROMPT_VERSION,
            fallback_reason=fallback_reason,
        )

    def _gemini_report(
        self, request: ReportRequest, evidence: MarketEvidencePacket
    ) -> TradeReport:
        schema = TradeReport.model_json_schema()
        base = self._rules_report(request, evidence)
        prompt = (
            "Create a detailed 600-900 word educational NIFTY options market-desk "
            "analysis using only the supplied calculated evidence. Complete every "
            "desk_analysis field, use a decision of Consider, Wait or Avoid, include "
            "both supporting and conflicting evidence, at least five monitoring "
            "checks, and strategy-specific execution, invalidation, adjustment and "
            "exit conditions. Never invent current "
            "news, macro conditions, events, prices or sources. If context is "
            "unavailable or stale, state that and lower confidence. Recommendations "
            "must be conditional trade-plan guidance, not personalized investment "
            "advice. Treat modeled time-spread values as estimates. Set "
            f'generated_by to "gemini" and prompt_version to "{PROMPT_VERSION}".\n\n'
            + json.dumps(
                {
                    "request": request.model_dump(mode="json"),
                    "market_evidence": evidence.model_dump(mode="json"),
                    "server_rules_baseline": base.model_dump(mode="json"),
                },
                indent=2,
            )
        )
        model = (
            getattr(settings, "gemini_quality_model", "gemini-2.5-flash")
            if getattr(settings, "gemini_analysis_mode", "quality").lower() == "quality"
            else getattr(settings, "gemini_fast_model", "gemini-2.5-flash-lite")
        )
        generation_config = {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "maxOutputTokens": 16384,
        }
        if model.startswith("gemini-3"):
            generation_config["thinkingConfig"] = {
                "thinkingLevel": settings.gemini_thinking_level
            }
        else:
            generation_config["temperature"] = 0.2
        validation_error = ""
        for _ in range(2):
            current_prompt = (
                prompt
                if not validation_error
                else prompt
                + "\n\nYour previous response failed validation: "
                + validation_error
                + " Correct it and return the complete JSON again."
            )
            response = httpx.post(
                (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent"
                ),
                headers={
                    "x-goog-api-key": settings.gemini_api_key or "",
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": current_prompt}]}],
                    "generationConfig": generation_config,
                },
                timeout=settings.gemini_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            report = TradeReport.model_validate_json(text)
            if report.desk_analysis is None:
                validation_error = "desk_analysis was omitted"
                continue
            count = word_count(report.desk_analysis)
            if count < 600 or count > 900:
                validation_error = f"desk_analysis has {count} words; required range is 600-900"
                continue
            if len(report.desk_analysis.monitoring_checklist) < 5:
                validation_error = "monitoring_checklist needs at least five items"
                continue
            return report.model_copy(
                update={
                    "generated_by": "gemini",
                    "desk_analysis": report.desk_analysis.model_copy(
                        update={"word_count": count}
                    ),
                    "validation_status": "passed",
                    "prompt_version": PROMPT_VERSION,
                    "fallback_reason": None,
                }
            )
        raise ValueError(validation_error or "AI report validation failed")

    def generate(self, request: ReportRequest, client_ip: str) -> TradeReport:
        payload = request.model_dump(mode="json")
        cache_key = hashlib.sha256(
            (
                f"{PROMPT_VERSION}:{getattr(settings, 'gemini_analysis_mode', 'quality')}:"
                + json.dumps(payload, sort_keys=True)
            ).encode()
        ).hexdigest()
        if cache_key in self.cache:
            return self.cache[cache_key]

        evidence = self._evidence(request)
        report: TradeReport
        if settings.gemini_api_key:
            try:
                self._check_limit(client_ip)
                report = self._gemini_report(request, evidence)
            except ReportRateLimit as error:
                report = self._rules_report(
                    request, evidence, fallback_reason=str(error)
                )
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as error:
                report = self._rules_report(
                    request,
                    evidence,
                    fallback_reason=f"AI provider unavailable or invalid: {type(error).__name__}.",
                )
        else:
            report = self._rules_report(
                request,
                evidence,
                fallback_reason="The AI provider API key is not configured.",
            )
        self.cache[cache_key] = report
        return report


report_service = ReportService()
