from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel, Field, ValidationError

from .analysis import AnalysisRequest, analyze_candidate
from .config import settings
from .desk_analysis import (
    PROMPT_VERSION,
    build_rules_analysis,
    classify_evidence,
    risk_label,
    strategy_family,
    word_count,
)
from .market_context import market_context_service
from .models import (
    AITradeIdea,
    DeskAnalysis,
    EvidenceReference,
    MarketContext,
    MarketEvent,
    MarketEvidencePacket,
    MarketMove,
    NewsItem,
    OptionChain,
    RecommendationChartPoint,
    RecommendationNarrativeRequest,
    RecommendationNarrativeResponse,
    RecommendationRequest,
    RecommendationResponse,
    RejectedTradeIdea,
    StrategyCandidate,
    TechnicalIndicators,
)
from .provider import provider
from .recommendation_context import (
    calculate_chain_summary,
    calculate_indicators,
    correlation,
    merge_nearby_events,
    parse_manual_events,
    parse_nse_holidays,
    parse_rss_events,
    utc_timestamp,
)
from .strategies import (
    scan_broken_wing_butterflies,
    scan_butterflies,
    scan_credit_spreads,
    scan_debit_spreads,
    scan_iron_condors,
    scan_risk_reversals,
    scan_straddles,
    scan_strangles,
    scan_time_spreads,
)


GLOBAL_SYMBOLS = {
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^DJI": "Dow Jones",
    "CL=F": "WTI Crude Oil",
    "GC=F": "Gold Futures",
    "^N225": "Nikkei 225",
    "^HSI": "Hang Seng",
    "000001.SS": "Shanghai Composite",
    "^INDIAVIX": "India VIX",
    "INR=X": "USD/INR",
    "^VIX": "US VIX",
    "^TNX": "US 10-Year Yield",
    "DX-Y.NYB": "US Dollar Index",
}

NEWS_FEEDS = {
    "Google News NIFTY": "https://news.google.com/rss/search?q=NIFTY%2050%20market&hl=en-IN&gl=IN&ceid=IN:en",
    "Google News Global Markets": "https://news.google.com/rss/search?q=global%20stock%20markets%20oil%20gold&hl=en-IN&gl=IN&ceid=IN:en",
}

RBI_RSS_URL = "https://rbi.org.in/pressreleases_rss.xml"
NSE_HOLIDAYS_URL = "https://www.nseindia.com/resources/exchange-communication-holidays/"
logger = logging.getLogger(__name__)


class GeminiIdea(BaseModel):
    candidate_id: str
    thesis: str
    entry: str
    risk_exit: str
    headline_ids: list[str] = []
    event_ids: list[str] = []
    market_symbols: list[str] = []


class GeminiRecommendation(BaseModel):
    ideas: list[GeminiIdea] = Field(min_length=7, max_length=7)


class RecommendationService:
    def __init__(self) -> None:
        self.cache: dict[str, RecommendationResponse] = {}
        self.preview_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.narrative_cache: dict[str, RecommendationNarrativeResponse] = {}
        self.evidence_cache: dict[str, tuple[float, MarketEvidencePacket]] = {}
        self.scan_cache: dict[str, tuple[list[StrategyCandidate], list[StrategyCandidate]]] = {}
        self.requests: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _check_limit(self, client_ip: str) -> None:
        now = time.time()
        with self.lock:
            history = [item for item in self.requests.get(client_ip, []) if now - item <= 86400]
            if history and now - history[-1] < 60:
                raise ValueError("Please wait one minute before generating another AI recommendation.")
            if len(history) >= 10:
                raise ValueError("Daily AI recommendation limit reached for this IP address.")
            history.append(now)
            self.requests[client_ip] = history

    @staticmethod
    def _provider_error_message(error: httpx.HTTPStatusError) -> str:
        status = error.response.status_code
        if status == 429:
            return "The AI service quota is temporarily exhausted. Showing the rules-based desk analysis instead."
        if status == 503:
            return "The AI service is temporarily busy. Showing the rules-based desk analysis instead; retry after a few minutes."
        if status in {401, 403}:
            return "The AI service rejected the configured credentials. Showing the rules-based desk analysis instead."
        return (
            f"The AI service returned HTTP {status}. "
            "Showing the rules-based desk analysis instead."
        )

    @staticmethod
    def _history(symbol: str, range_name: str = "1y") -> dict[str, Any]:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}?range={range_name}&interval=1d&events=history"
        )
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        timestamps = result.get("timestamp") or []
        quote_data = result["indicators"]["quote"][0]
        rows = [
            (
                int(timestamp),
                float(close),
                float(high if high is not None else close),
                float(low if low is not None else close),
            )
            for timestamp, close, high, low in zip(
                timestamps,
                quote_data.get("close") or [],
                quote_data.get("high") or [],
                quote_data.get("low") or [],
            )
            if close is not None
        ]
        return {
            "timestamps": [row[0] for row in rows],
            "closes": [row[1] for row in rows],
            "highs": [row[2] for row in rows],
            "lows": [row[3] for row in rows],
            "timestamp": utc_timestamp(
                result.get("meta", {}).get("regularMarketTime")
                or (rows[-1][0] if rows else None)
            ),
        }

    @staticmethod
    def _market_move(
        symbol: str, name: str, nifty_prices: dict[int, float] | None = None
    ) -> MarketMove:
        try:
            history = RecommendationService._history(symbol, "6mo")
            closes = history["closes"]
            if len(closes) < 2:
                raise ValueError("insufficient prices")
            prices = dict(zip(history["timestamps"], closes))

            def movement(sessions: int) -> float | None:
                if len(closes) <= sessions:
                    return None
                return round((closes[-1] / closes[-sessions - 1] - 1) * 100, 2)

            return MarketMove(
                symbol=symbol,
                name=name,
                last=round(closes[-1], 2),
                one_day_return=movement(1),
                one_week_return=movement(5),
                one_month_return=movement(20),
                correlation_20d=correlation(nifty_prices or {}, prices, 20),
                correlation_60d=correlation(nifty_prices or {}, prices, 60),
                timestamp=history["timestamp"],
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return MarketMove(symbol=symbol, name=name, stale=True)

    @staticmethod
    def _technical_indicators() -> tuple[TechnicalIndicators, dict[int, float]]:
        try:
            history = RecommendationService._history("^NSEI", "1y")
            indicators = calculate_indicators(
                history["closes"],
                history["highs"],
                history["lows"],
                history["timestamp"],
            )
            return indicators, dict(zip(history["timestamps"], history["closes"]))
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return TechnicalIndicators(stale=True), {}

    @staticmethod
    def _news() -> list[NewsItem]:
        items: list[NewsItem] = []
        for source, url in NEWS_FEEDS.items():
            try:
                response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
                response.raise_for_status()
                root = ElementTree.fromstring(response.text)
                for item in root.findall(".//item")[:6]:
                    published = item.findtext("pubDate")
                    try:
                        published = parsedate_to_datetime(published).isoformat() if published else None
                    except (TypeError, ValueError):
                        pass
                    title = (item.findtext("title") or "").strip()
                    if title:
                        identifier = hashlib.sha1(f"{source}:{title}".encode()).hexdigest()[:10]
                        items.append(
                            NewsItem(
                                id=f"news-{identifier}",
                                title=title,
                                source=source,
                                published=published,
                                url=item.findtext("link"),
                            )
                        )
            except (httpx.HTTPError, ElementTree.ParseError):
                continue
        unique: dict[str, NewsItem] = {}
        for item in items:
            unique.setdefault(item.title.lower(), item)
        return list(unique.values())[:10]

    @staticmethod
    def _events(report_date: str) -> list[MarketEvent]:
        events = parse_manual_events(getattr(settings, "market_events_json", "[]"))
        try:
            response = httpx.get(RBI_RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            response.raise_for_status()
            events.extend(parse_rss_events(response.text))
        except (httpx.HTTPError, ElementTree.ParseError):
            pass
        try:
            response = httpx.get(
                NSE_HOLIDAYS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=12
            )
            response.raise_for_status()
            events.extend(parse_nse_holidays(response.text))
        except httpx.HTTPError:
            pass
        return merge_nearby_events(events, report_date)

    @staticmethod
    def _candidate_pool(near: OptionChain, far: OptionChain | None) -> list[StrategyCandidate]:
        pool: list[StrategyCandidate] = []
        scanners = [
            scan_debit_spreads,
            scan_credit_spreads,
            scan_iron_condors,
            scan_butterflies,
            scan_broken_wing_butterflies,
            scan_risk_reversals,
            scan_straddles,
            scan_strangles,
        ]
        for scanner in scanners:
            pool.extend(scanner(near, limit=5))
        if far:
            pool.extend(scan_time_spreads(near, far, diagonal=False, limit=4))
            pool.extend(scan_time_spreads(near, far, diagonal=True, limit=4))
        pool.sort(key=lambda item: (item.score, item.liquidity_score), reverse=True)
        diversified: list[StrategyCandidate] = []
        family_counts: dict[str, int] = {}
        for candidate in pool:
            family = str(candidate.metadata.get("strategy_family") or candidate.strategy)
            if family_counts.get(family, 0) >= 2:
                continue
            family_counts[family] = family_counts.get(family, 0) + 1
            diversified.append(candidate)
            if len(diversified) >= 15:
                break
        return diversified

    @staticmethod
    def _effective_profit_loss(
        candidate: StrategyCandidate,
    ) -> tuple[float | None, float | None]:
        if candidate.metric_mode == "modeled":
            return candidate.estimated_peak_profit, candidate.modeled_worst_loss
        return candidate.max_profit, candidate.max_loss

    @classmethod
    def _validity(
        cls, candidate: StrategyCandidate
    ) -> tuple[bool, str | None, float | None]:
        max_profit, max_loss = cls._effective_profit_loss(candidate)
        if max_profit is None or max_loss is None:
            return True, None, None
        ratio = max_profit / max_loss if max_loss > 0 else None
        if max_profit <= 0 or max_profit <= max_loss * 0.5:
            return (
                False,
                "This structure's best-case profit does not justify its defined risk. AI has skipped this slot.",
                ratio,
            )
        return True, None, ratio

    @classmethod
    def _high_risk_pool(cls, chain: OptionChain) -> list[StrategyCandidate]:
        spot = chain.underlying_value
        pool = (
            scan_debit_spreads(chain, limit=60)
            + scan_butterflies(chain, limit=60)
            + scan_broken_wing_butterflies(chain, limit=60)
        )
        selected: list[StrategyCandidate] = []
        seen: set[str] = set()
        for candidate in sorted(
            pool,
            key=lambda item: (
                (item.max_profit or 0) / max(item.max_loss or 1, 1),
                item.score,
                item.liquidity_score,
            ),
            reverse=True,
        ):
            valid, _, ratio = cls._validity(candidate)
            max_profit, max_loss = cls._effective_profit_loss(candidate)
            if (
                not valid
                or ratio is None
                or ratio < 3
                or ratio > 20
                or max_loss is None
                or max_loss < 500
                or max_profit is None
                or candidate.id in seen
            ):
                continue
            name = candidate.strategy.lower()
            if "debit spread" in name:
                buy_leg = next((leg for leg in candidate.legs if leg.action == "BUY"), None)
                if not buy_leg:
                    continue
                is_otm = (
                    buy_leg.option_type == "CE" and buy_leg.strike > spot
                ) or (
                    buy_leg.option_type == "PE" and buy_leg.strike < spot
                )
                if not is_otm:
                    continue
            elif "butterfly" not in name:
                continue
            selected.append(candidate)
            seen.add(candidate.id)
            if len(selected) == 8:
                break
        return selected

    @staticmethod
    def _family(candidate: StrategyCandidate) -> str:
        return strategy_family(candidate)

    @staticmethod
    def _risk_label(candidate: StrategyCandidate) -> str:
        return risk_label(candidate)

    def collect_evidence(
        self, chain: OptionChain, report_date: str
    ) -> MarketEvidencePacket:
        with ThreadPoolExecutor(max_workers=3) as executor:
            indicators_future = executor.submit(self._technical_indicators)
            news_future = executor.submit(self._news)
            events_future = executor.submit(self._events, report_date)
            indicators, nifty_prices = indicators_future.result()
            news = news_future.result()
            events = events_future.result()
        with ThreadPoolExecutor(max_workers=8) as executor:
            markets = list(
                executor.map(
                    lambda item: self._market_move(item[0], item[1], nifty_prices),
                    GLOBAL_SYMBOLS.items(),
                )
            )
        summary = calculate_chain_summary(chain)
        packet = MarketEvidencePacket(
            report_date=report_date,
            chain_timestamp=chain.timestamp,
            technical_indicators=indicators,
            option_chain_summary=summary,
            global_markets=markets,
            news=news,
            market_events=events,
            short_term_trend="Unavailable",
            medium_term_trend="Unavailable",
            momentum_strength="Unavailable",
            volatility_regime="Unavailable",
            iv_premium_regime="Unavailable",
            option_chain_bias="Unavailable",
            event_risk="Unavailable",
            input_timestamps={
                "option_chain": chain.timestamp,
                "nifty_indicators": indicators.timestamp,
                **{item.symbol: item.timestamp for item in markets},
            },
            stale_inputs=[
                label
                for label, stale in (
                    ("NIFTY indicators", indicators.stale),
                    ("option chain", chain.stale),
                    ("global markets", any(item.stale for item in markets)),
                )
                if stale
            ],
        )
        return packet.model_copy(update=classify_evidence(packet))

    @staticmethod
    def _trend_alignment(candidate: StrategyCandidate, indicators: TechnicalIndicators) -> bool:
        bullish = (
            indicators.ema_9 is not None
            and indicators.ema_21 is not None
            and indicators.ema_9 > indicators.ema_21
            and (indicators.rsi_14 or 50) >= 50
        )
        bearish = (
            indicators.ema_9 is not None
            and indicators.ema_21 is not None
            and indicators.ema_9 < indicators.ema_21
            and (indicators.rsi_14 or 50) <= 50
        )
        outlook = candidate.outlook.lower()
        return ("bull" in outlook and bullish) or ("bear" in outlook and bearish) or (
            any(word in outlook for word in ("neutral", "range")) and not (bullish or bearish)
        )

    @classmethod
    def _confidence(
        cls, candidate: StrategyCandidate, indicators: TechnicalIndicators
    ) -> tuple[Literal["low", "medium", "high"], str]:
        aligned = cls._trend_alignment(candidate, indicators)
        if candidate.score > 90 and candidate.liquidity_score > 90 and aligned:
            return "high", "Score and liquidity exceed 90 and price-action indicators align."
        if candidate.score > 80 or not aligned:
            return "medium", (
                "Score exceeds 80, but trend alignment or liquidity is not strong enough "
                "for high confidence."
            )
        return "low", "Score, liquidity, or trend alignment does not meet stronger thresholds."

    @staticmethod
    def _chart(candidate: StrategyCandidate, chain: OptionChain) -> list[RecommendationChartPoint]:
        try:
            analysis = analyze_candidate(
                AnalysisRequest(
                    candidate=candidate,
                    underlying_value=chain.underlying_value,
                    lot_size=chain.lot_size,
                    chain_timestamp=chain.timestamp,
                    evaluation_days=3650,
                    price_range_pct=8,
                )
            )
            step = max(1, len(analysis.points) // 20)
            return [
                RecommendationChartPoint(
                    underlying_price=point.underlying_price,
                    pnl=point.evaluation_pnl,
                )
                for point in analysis.points[::step]
            ][:21]
        except ValueError:
            return []

    @staticmethod
    def _candidate_payload(candidate: StrategyCandidate, chain: OptionChain) -> dict[str, Any]:
        spot = chain.underlying_value
        return {
            "candidate_id": candidate.id,
            "strategy": candidate.strategy,
            "strategy_family": RecommendationService._family(candidate),
            "outlook": candidate.outlook,
            "score": candidate.score,
            "liquidity": candidate.liquidity_score,
            "legs": [
                {
                    **leg.model_dump(mode="json"),
                    "distance_from_spot_points": round(leg.strike - spot, 2),
                    "distance_from_spot_percent": round((leg.strike / spot - 1) * 100, 2),
                }
                for leg in candidate.legs
            ],
            "net_debit": candidate.net_debit,
            "net_credit": candidate.net_credit,
            "maximum_profit": (
                candidate.max_profit
                if candidate.metadata.get("bounded_profit", candidate.max_profit is not None)
                else "Unlimited"
            ),
            "maximum_loss": (
                candidate.max_loss
                if candidate.metadata.get("bounded_loss", candidate.max_loss is not None)
                else "Unlimited"
            ),
            "modeled_range_loss": candidate.modeled_worst_loss,
            "breakevens": candidate.breakevens or candidate.estimated_breakevens,
            "breakeven_distances_percent": [
                round((value / spot - 1) * 100, 2)
                for value in (candidate.breakevens or candidate.estimated_breakevens)
            ],
            "risk_label": RecommendationService._risk_label(candidate),
            "premium_basis": candidate.metadata.get("premium_basis", "LTP"),
            "payoff_type": candidate.metadata.get("payoff_type", candidate.metric_mode),
        }

    @staticmethod
    def _evidence(
        idea: GeminiIdea, news: list[NewsItem], events: list[MarketEvent], markets: list[MarketMove]
    ) -> list[EvidenceReference]:
        lookup: dict[str, EvidenceReference] = {}
        for item in news:
            lookup[item.id] = EvidenceReference(kind="headline", id=item.id, label=item.title)
        for item in events:
            lookup[item.id] = EvidenceReference(kind="event", id=item.id, label=item.title)
        for item in markets:
            lookup[item.symbol] = EvidenceReference(kind="market", id=item.symbol, label=item.name)
        return [
            lookup[value]
            for value in idea.headline_ids + idea.event_ids + idea.market_symbols
            if value in lookup
        ]

    @classmethod
    def _idea_from_candidate(
        cls,
        candidate: StrategyCandidate,
        chain: OptionChain,
        packet: MarketEvidencePacket,
        indicators: TechnicalIndicators,
        *,
        speculative: bool = False,
    ) -> AITradeIdea:
        valid, rejection_reason, ratio = cls._validity(candidate)
        confidence, rationale = cls._confidence(candidate, indicators)
        desk = build_rules_analysis(candidate, packet) if valid else None
        high_risk_reason = None
        if speculative:
            confidence = "speculative"
            rationale = (
                "This setup deliberately trades a lower win-rate for at least "
                "three units of potential reward per unit of defined risk."
            )
            breakevens = candidate.breakevens or candidate.estimated_breakevens
            boundary = ", ".join(f"{value:,.0f}" for value in breakevens) or "the displayed breakeven"
            high_risk_reason = (
                f"The thesis fails if NIFTY does not move through {boundary} inside the expiry window. "
                f"Time decay can consume the premium quickly, and the full defined loss of "
                f"{candidate.max_loss or candidate.modeled_worst_loss or 0:,.0f} INR may be realized."
            )
        return AITradeIdea(
            candidate_id=candidate.id,
            title=f"{candidate.strategy} setup",
            strategy=candidate.strategy,
            outlook=candidate.outlook,
            recommendation=(
                f"{desk.decision}: {desk.executive_summary}"
                if desk
                else "Setup rejected by the server-side reward/risk guard."
            ),
            background=desk.price_action_analysis if desk else "",
            analysis=desk.option_chain_analysis if desk else "",
            entry_plan=desk.entry_execution_plan if desk else "",
            risk_management=desk.risk_analysis if desk else "",
            confidence=confidence,
            confidence_rationale=rationale,
            risk_label=cls._risk_label(candidate),
            candidate=candidate if valid else None,
            chart_points=cls._chart(candidate, chain) if valid else [],
            desk_analysis=desk,
            valid_setup=valid,
            rejection_reason=rejection_reason,
            speculative=speculative,
            high_risk_reason=high_risk_reason,
            reward_risk_ratio=ratio,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def _rules_response(
        cls,
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
        high_risk_candidates: list[StrategyCandidate] | None = None,
        evidence_packet: MarketEvidencePacket | None = None,
        fallback_reason: str | None = None,
    ) -> RecommendationResponse:
        context = market_context_service.get(force=request.refresh)
        chain_summary = calculate_chain_summary(chain)
        packet = evidence_packet or MarketEvidencePacket(
            report_date=request.analysis_date,
            chain_timestamp=chain.timestamp,
            technical_indicators=indicators,
            option_chain_summary=chain_summary,
            global_markets=global_markets,
            news=news,
            market_events=events,
            short_term_trend=context.short_term_trend,
            medium_term_trend=context.medium_term_trend,
            momentum_strength=context.momentum,
            volatility_regime=context.volatility_regime,
            iv_premium_regime="Unavailable",
            option_chain_bias="Unavailable",
            event_risk="Unavailable",
        )
        ideas: list[AITradeIdea] = []
        rejected: list[RejectedTradeIdea] = []
        used_families: set[str] = set()
        for candidate in candidates:
            family = cls._family(candidate)
            if family in used_families or candidate.score < 65 or candidate.liquidity_score < 50:
                continue
            used_families.add(family)
            idea = cls._idea_from_candidate(candidate, chain, packet, indicators)
            ideas.append(idea)
            if not idea.valid_setup:
                max_profit, max_loss = cls._effective_profit_loss(candidate)
                rejected.append(
                    RejectedTradeIdea(
                        candidate_id=candidate.id,
                        strategy=candidate.strategy,
                        reason=idea.rejection_reason or "Failed reward/risk validation.",
                        max_profit=max_profit,
                        max_loss=max_loss,
                        reward_risk_ratio=idea.reward_risk_ratio,
                    )
                )
            if len(ideas) == 5:
                break
        while len(ideas) < 5:
            ideas.append(
                AITradeIdea(
                    title="No Trade / Wait",
                    strategy="No trade",
                    outlook="Neutral",
                    recommendation="Wait for a distinct liquid setup with better evidence.",
                    background="The current candidate pool did not support another qualified strategy family.",
                    analysis="A placeholder is safer than manufacturing a fifth trade.",
                    entry_plan="No entry.",
                    risk_management="Keep capital uncommitted until the stated filters improve.",
                    confidence="low",
                    confidence_rationale="No qualified canonical candidate was available.",
                    risk_label="No position risk",
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        high_risk_ideas = [
            cls._idea_from_candidate(
                candidate, chain, packet, indicators, speculative=True
            )
            for candidate in (high_risk_candidates or [])[:2]
        ]
        return RecommendationResponse(
            analysis_date=request.analysis_date,
            generated_by="rules",
            chain_timestamp=chain.timestamp,
            underlying_value=chain.underlying_value,
            market_context=context,
            technical_indicators=indicators,
            option_chain_summary=chain_summary,
            market_events=events,
            global_markets=global_markets,
            news=news,
            ideas=ideas,
            high_risk_ideas=high_risk_ideas,
            rejected_ideas=rejected,
            input_timestamps=packet.input_timestamps,
            stale_inputs=packet.stale_inputs,
            validation_status="rules-fallback",
            fallback_reason=fallback_reason,
            assumptions=[
                "The report date labels this report; all market inputs use their latest available timestamp.",
                "Expected move uses the current ATM straddle premium as an option-implied proxy.",
                "Max pain is an estimate from displayed open interest, not a forecast.",
                "Yahoo Finance and RSS inputs are best-effort and may be delayed.",
                "Educational analysis only; not personalized investment advice.",
            ],
            disclaimer="Verify live prices, events, liquidity and risk independently before trading.",
        )

    @staticmethod
    def _prompt_payload(
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        high_risk_candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
    ) -> dict[str, Any]:
        summary = calculate_chain_summary(chain)
        relevant_strikes = {
            leg.strike
            for candidate in candidates + high_risk_candidates
            for leg in candidate.legs
        }

        def compact_quote(quote: Any) -> dict[str, Any] | None:
            if quote is None:
                return None
            return {
                "bid": quote.bid,
                "ask": quote.ask,
                "ltp": quote.last_price,
                "iv": quote.implied_volatility,
                "oi": quote.open_interest,
                "oi_change": quote.change_in_oi,
                "volume": quote.volume,
            }

        quote_context = []
        for row in chain.rows:
            if any(abs(row.strike - strike) <= (summary.strike_interval or 50) for strike in relevant_strikes):
                quote_context.append(
                    {
                        "strike": row.strike,
                        "ce": compact_quote(row.ce),
                        "pe": compact_quote(row.pe),
                    }
                )
        return {
            "report_date": request.analysis_date,
            "current_data_notice": "This is current/latest data, not historical option-chain analysis.",
            "lot_size": chain.lot_size,
            "technical_indicators": indicators.model_dump(mode="json"),
            "option_chain_summary": summary.model_dump(mode="json"),
            "candidate_pool": [
                RecommendationService._candidate_payload(candidate, chain)
                for candidate in candidates[:10]
            ],
            "high_risk_candidate_pool": [
                RecommendationService._candidate_payload(candidate, chain)
                for candidate in high_risk_candidates[:4]
            ],
            "relevant_option_quotes": quote_context[:40],
            "global_markets": [item.model_dump(mode="json") for item in global_markets],
            "headlines": [
                {
                    "id": item.id,
                    "title": item.title,
                    "source": item.source,
                    "published": item.published,
                }
                for item in news
            ],
            "nearby_events": [
                {
                    "id": item.id,
                    "date": item.date,
                    "title": item.title,
                    "importance": item.importance,
                    "source": item.source,
                    "verified": item.verified,
                }
                for item in events
            ],
            "deterministic_classifications": (
                classify_evidence(
                    MarketEvidencePacket(
                        report_date=request.analysis_date,
                        chain_timestamp=chain.timestamp,
                        technical_indicators=indicators,
                        option_chain_summary=summary,
                        global_markets=global_markets,
                        news=news,
                        market_events=events,
                        short_term_trend="Unavailable",
                        medium_term_trend="Unavailable",
                        momentum_strength="Unavailable",
                        volatility_regime="Unavailable",
                        iv_premium_regime="Unavailable",
                        option_chain_bias="Unavailable",
                        event_risk="Unavailable",
                    )
                )
            ),
        }

    @staticmethod
    def _prompt(payload: dict[str, Any], validation_errors: list[str] | None = None) -> str:
        correction = (
            "\nYour previous response failed validation:\n- "
            + "\n- ".join(validation_errors or [])
            + "\nCorrect every error."
            if validation_errors
            else ""
        )
        return f"""
You are an expert NIFTY options educator writing a structured market-desk report
for report_date {payload["report_date"]}. The report date is a label; all supplied
market data is current/latest and carries its own timestamp.

Generate exactly seven output slots. Ideas 1-5 are standard ideas and must use
distinct strategy_family values spanning at least three families when possible.
Ideas 6-7 must use different candidates from high_risk_candidate_pool, set
speculative=true, and include a plain-language high_risk_reason. If standard
evidence is insufficient, use an empty candidate_id and title "No Trade / Wait".
For standard ideas, use an empty high_risk_reason.

For each trade:
- background: 3-4 substantive sentences. Explain NIFTY versus ATM, support,
  resistance, expected move and OI walls; cite actual indicator values; reference
  only supplied global returns/correlations; cite relevant headline/event IDs or
  explicitly state none is relevant.
- analysis: 3-5 sentences using the exact score, liquidity, strike spacing, IV
  regime, debit/credit, breakevens and server-supplied risk label.
- entry_plan: 2-3 strategy-specific sentences. Explain combo versus legging,
  leg sequence where appropriate, and strike-specific slippage risk.
- risk_management: 2-3 sentences. Repeat exact bounded risk, "Unlimited", or
  modeled range loss exactly as supplied. Give concrete triggers using supplied
  short strikes, breakevens, support/resistance or ATR.
- recommendation: a concise educational action conditional on live confirmation.
- headline_ids, event_ids and market_symbols: include only IDs/symbols supplied.
- Keep every narrative field concise and focused on the trade consequence.
- Every subsection must be inference-led. Lead with the takeaway, cite the
  number as evidence second, then explain the trade consequence. A sentence
  that lists a metric without explaining how it changes entry, sizing,
  adjustment, invalidation or exit is unacceptable.
- Ideas 6-7 deliberately sacrifice win-rate for a large payoff if the
  directional thesis plays out inside the expiry window. Do not alter their
  canonical legs or server-calculated reward/risk.

Do not calculate risk, invent a price, event, headline, source, correlation or
market relationship. Do not repeat background or entry-plan prose. Return only
valid JSON matching the schema, with no Markdown.{correction}

DATA:
{json.dumps(payload, indent=2)}
""".strip()

    @staticmethod
    def _gemini_response_schema() -> dict[str, Any]:
        string = {"type": "string"}
        string_list = {"type": "array", "items": string}
        desk_properties = {
            "decision": {"type": "string", "enum": ["Consider", "Wait", "Avoid"]},
            "executive_summary": string,
            "price_action_analysis": string,
            "option_chain_analysis": string,
            "global_cues": string_list,
            "news_event_risk": string,
            "score_liquidity_analysis": string,
            "strategy_rationale": string,
            "entry_execution_plan": string,
            "risk_analysis": string,
            "adjustment_exit_plan": string,
            "monitoring_checklist": string_list,
            "supporting_evidence": string_list,
            "conflicting_evidence": string_list,
            "word_count": {"type": "integer"},
        }
        desk_schema = {
            "type": "object",
            "properties": desk_properties,
            "required": list(desk_properties),
        }
        idea_properties = {
            "candidate_id": string,
            "title": string,
            "outlook": string,
            "recommendation": string,
            "background": string,
            "analysis": string,
            "entry_plan": string,
            "risk_management": string,
            "headline_ids": string_list,
            "event_ids": string_list,
            "market_symbols": string_list,
            "desk_analysis": desk_schema,
            "speculative": {"type": "boolean"},
            "high_risk_reason": string,
        }
        return {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": idea_properties,
                        "required": list(idea_properties),
                    },
                }
            },
            "required": ["ideas"],
        }

    @staticmethod
    def _call_gemini(prompt: str) -> GeminiRecommendation:
        model = (
            getattr(settings, "gemini_quality_model", "gemini-2.5-flash")
            if getattr(settings, "gemini_analysis_mode", "quality").lower() == "quality"
            else getattr(settings, "gemini_fast_model", "gemini-2.5-flash-lite")
        )
        generation_config = {
            "responseMimeType": "application/json",
            "responseJsonSchema": RecommendationService._gemini_response_schema(),
            "maxOutputTokens": 3072,
            "temperature": 0.75,
        }
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
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            },
            timeout=settings.gemini_timeout_seconds,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return GeminiRecommendation.model_validate_json(text)

    @classmethod
    def _validate_gemini(
        cls,
        report: GeminiRecommendation,
        candidates: list[StrategyCandidate],
        news: list[NewsItem],
        events: list[MarketEvent],
        markets: list[MarketMove],
        allowed_numbers: set[float] | None = None,
    ) -> list[str]:
        errors: list[str] = []
        candidate_lookup = {item.id: item for item in candidates}
        news_ids = {item.id for item in news}
        event_ids = {item.id for item in events}
        symbols = {item.symbol for item in markets}
        families: list[str] = []
        backgrounds: list[str] = []
        entries: list[str] = []
        for index, idea in enumerate(report.ideas, 1):
            candidate = None
            if idea.candidate_id:
                candidate = candidate_lookup.get(idea.candidate_id)
                if not candidate:
                    errors.append(f"Idea {index} uses unknown candidate_id {idea.candidate_id}.")
                elif index <= 5:
                    families.append(cls._family(candidate))
            if index > 5:
                if not idea.speculative:
                    errors.append(f"Idea {index} must be marked speculative.")
                if not idea.high_risk_reason:
                    errors.append(f"Idea {index} needs a high-risk failure explanation.")
                if candidate:
                    _, _, ratio = cls._validity(candidate)
                    if ratio is None or ratio < 3:
                        errors.append(f"Idea {index} does not meet the 3:1 reward/risk floor.")
            if set(idea.headline_ids) - news_ids:
                errors.append(f"Idea {index} cites an unknown headline ID.")
            if set(idea.event_ids) - event_ids:
                errors.append(f"Idea {index} cites an unknown event ID.")
            if set(idea.market_symbols) - symbols:
                errors.append(f"Idea {index} cites an unknown market symbol.")
            for raw_number in re.findall(
                r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d+)?)",
                " ".join(
                    (
                        idea.background,
                        idea.analysis,
                        idea.entry_plan,
                        idea.risk_management,
                        idea.recommendation,
                        json.dumps(idea.desk_analysis.model_dump(mode="json")),
                    )
                ),
                re.I,
            ):
                normalized_number = raw_number.replace(",", "")
                if not normalized_number or not any(char.isdigit() for char in normalized_number):
                    continue
                value = float(normalized_number)
                if allowed_numbers and not any(abs(value - known) <= 1.01 for known in allowed_numbers):
                    errors.append(f"Idea {index} introduces unknown monetary value {value}.")
            backgrounds.append(re.sub(r"\W+", " ", idea.background.lower()).strip())
            entries.append(re.sub(r"\W+", " ", idea.entry_plan.lower()).strip())
        if len(families) != len(set(families)):
            errors.append("A strategy family is repeated.")
        available_families = {cls._family(item) for item in candidates}
        if len(available_families) >= 3 and len(set(families)) < 3:
            errors.append("Fewer than three strategy families were used.")
        if len(report.ideas) != 7:
            errors.append("Exactly seven ideas are required.")
        if len(backgrounds) != len(set(backgrounds)):
            errors.append("Background prose is duplicated.")
        if len(entries) != len(set(entries)):
            errors.append("Entry-plan prose is duplicated.")
        return errors

    @staticmethod
    def _sanitize_monetary_text(text: str, allowed_numbers: set[float]) -> str:
        def replace(match: re.Match[str]) -> str:
            currency, raw_number = match.groups()
            normalized = raw_number.replace(",", "")
            if not normalized or not any(char.isdigit() for char in normalized):
                return match.group(0)
            value = float(normalized)
            if any(abs(value - known) <= 1.01 for known in allowed_numbers):
                return match.group(0)
            return f"{currency} server-calculated amount"

        return re.sub(
            r"(Rs\.?|INR|\u20b9)\s*([\d,]+(?:\.\d+)?)",
            replace,
            text,
            flags=re.I,
        )

    @classmethod
    def _ground_gemini_output(
        cls,
        report: GeminiRecommendation,
        candidates: list[StrategyCandidate],
        evidence: MarketEvidencePacket,
        allowed_numbers: set[float],
    ) -> GeminiRecommendation:
        candidate_lookup = {item.id: item for item in candidates}
        grounded_ideas = []
        for idea in report.ideas:
            data = idea.model_dump(mode="python")

            def sanitize(value: Any) -> Any:
                if isinstance(value, str):
                    return cls._sanitize_monetary_text(value, allowed_numbers)
                if isinstance(value, list):
                    return [sanitize(item) for item in value]
                if isinstance(value, dict):
                    return {key: sanitize(item) for key, item in value.items()}
                return value

            idea = GeminiIdea.model_validate(sanitize(data))
            candidate = candidate_lookup.get(idea.candidate_id or "")
            if not candidate:
                grounded_ideas.append(idea)
                continue
            desk = idea.desk_analysis
            if f"{candidate.score:.1f}" not in (
                desk.score_liquidity_analysis + desk.executive_summary
            ):
                desk = desk.model_copy(
                    update={
                        "score_liquidity_analysis": (
                            f"The server-calculated scanner score is {candidate.score:.1f}/100 "
                            f"and liquidity is {candidate.liquidity_score:.1f}/100. "
                            + desk.score_liquidity_analysis
                        )
                    }
                )
            baseline = build_rules_analysis(candidate, evidence)
            supplements = (
                ("option_chain_analysis", baseline.option_chain_analysis),
                ("price_action_analysis", baseline.price_action_analysis),
                ("strategy_rationale", baseline.strategy_rationale),
                ("adjustment_exit_plan", baseline.adjustment_exit_plan),
                ("score_liquidity_analysis", baseline.score_liquidity_analysis),
            )
            for field, supplement in supplements:
                if word_count(desk) >= 600:
                    break
                desk = desk.model_copy(
                    update={
                        field: (
                            getattr(desk, field)
                            + "\n\nServer-grounded evidence supplement: "
                            + supplement
                        )
                    }
                )
            grounded_ideas.append(idea.model_copy(update={"desk_analysis": desk}))
        return report.model_copy(update={"ideas": grounded_ideas})

    @staticmethod
    def _numeric_facts(payload: Any) -> set[float]:
        facts: set[float] = set()
        if isinstance(payload, dict):
            for value in payload.values():
                facts.update(RecommendationService._numeric_facts(value))
        elif isinstance(payload, list):
            for value in payload:
                facts.update(RecommendationService._numeric_facts(value))
        elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
            facts.add(round(float(payload), 2))
        return facts

    @classmethod
    def _attach_gemini(
        cls,
        report: GeminiRecommendation,
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        high_risk_candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
        evidence_packet: MarketEvidencePacket,
    ) -> RecommendationResponse:
        base = cls._rules_response(
            request,
            chain,
            candidates,
            global_markets,
            news,
            events,
            indicators,
            high_risk_candidates,
            evidence_packet,
        )
        all_candidates = candidates + high_risk_candidates
        lookup = {item.id: item for item in all_candidates}
        ideas: list[AITradeIdea] = []
        high_risk_ideas: list[AITradeIdea] = []
        for index, item in enumerate(report.ideas):
            candidate = lookup.get(item.candidate_id or "")
            if candidate:
                valid, rejection_reason, ratio = cls._validity(candidate)
                confidence, rationale = cls._confidence(candidate, indicators)
                if index >= 5:
                    confidence = "speculative"
                    rationale = (
                        "This idea intentionally accepts a lower win-rate for "
                        "asymmetric reward of at least 3:1."
                    )
                strategy = candidate.strategy
                risk_label = cls._risk_label(candidate)
                chart = cls._chart(candidate, chain) if valid else []
            else:
                valid, rejection_reason, ratio = True, None, None
                confidence, rationale = "low", "No canonical candidate was attached."
                strategy, risk_label, chart = "No trade", "No position risk", []
            idea = AITradeIdea(
                    **item.model_dump(
                        exclude={
                            "headline_ids",
                            "event_ids",
                            "market_symbols",
                            "desk_analysis",
                            "speculative",
                            "high_risk_reason",
                        }
                    ),
                    strategy=strategy,
                    confidence=confidence,
                    confidence_rationale=rationale,
                    risk_label=risk_label,
                    evidence=cls._evidence(item, news, events, global_markets),
                    candidate=candidate if valid else None,
                    chart_points=chart,
                    desk_analysis=item.desk_analysis.model_copy(
                        update={"word_count": word_count(item.desk_analysis)}
                    ) if valid else None,
                    valid_setup=valid,
                    rejection_reason=rejection_reason,
                    speculative=index >= 5,
                    high_risk_reason=item.high_risk_reason if index >= 5 else None,
                    reward_risk_ratio=ratio,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                )
            if index < 5:
                ideas.append(idea)
            else:
                high_risk_ideas.append(idea)
        rejected = []
        for candidate in candidates:
            valid, reason, ratio = cls._validity(candidate)
            if valid:
                continue
            max_profit, max_loss = cls._effective_profit_loss(candidate)
            rejected.append(
                RejectedTradeIdea(
                    candidate_id=candidate.id,
                    strategy=candidate.strategy,
                    reason=reason or "Failed reward/risk validation.",
                    max_profit=max_profit,
                    max_loss=max_loss,
                    reward_risk_ratio=ratio,
                )
            )
        return base.model_copy(
            update={
                "generated_by": "gemini",
                "ideas": ideas,
                "high_risk_ideas": high_risk_ideas,
                "rejected_ideas": rejected,
                "validation_status": "passed-server-grounded",
                "fallback_reason": None,
            }
        )

    @staticmethod
    def _concise_schema() -> dict[str, Any]:
        string = {"type": "string"}
        return {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": string,
                            "thesis": string,
                            "entry": string,
                            "risk_exit": string,
                            "headline_ids": {"type": "array", "items": string},
                            "event_ids": {"type": "array", "items": string},
                            "market_symbols": {"type": "array", "items": string},
                        },
                        "required": [
                            "candidate_id",
                            "thesis",
                            "entry",
                            "risk_exit",
                            "headline_ids",
                            "event_ids",
                            "market_symbols",
                        ],
                    },
                }
            },
            "required": ["ideas"],
        }

    @staticmethod
    def _concise_prompt(
        packet: MarketEvidencePacket,
        candidates: list[StrategyCandidate],
    ) -> str:
        def clean_title(value: str) -> str:
            return re.sub(r"\d+(?:[.,]\d+)*", "", value).strip()

        def market_direction(item: MarketMove) -> str:
            move = item.one_day_return
            return "unavailable" if move is None else "positive" if move > 0 else "negative" if move < 0 else "flat"

        def correlation_strength(item: MarketMove) -> str:
            value = max(
                abs(item.correlation_20d or 0),
                abs(item.correlation_60d or 0),
            )
            return "strong" if value >= 0.6 else "moderate" if value >= 0.3 else "weak"

        payload = {
            "classifications": {
                "short_term_trend": packet.short_term_trend,
                "medium_term_trend": packet.medium_term_trend,
                "momentum": packet.momentum_strength,
                "volatility": packet.volatility_regime,
                "iv_regime": packet.iv_premium_regime,
                "chain_bias": packet.option_chain_bias,
                "event_risk": packet.event_risk,
            },
            "candidates": [
                {
                    "candidate_id": item.id,
                    "strategy": item.strategy,
                    "family": strategy_family(item),
                    "outlook": item.outlook,
                    "premium_style": (
                        "debit"
                        if item.net_debit is not None
                        else "credit"
                        if item.net_credit is not None
                        else "mixed"
                    ),
                    "risk_type": (
                        "modeled"
                        if item.metric_mode == "modeled"
                        else "defined"
                        if item.metadata.get("bounded_loss", item.max_loss is not None)
                        else "unlimited"
                    ),
                }
                for item in candidates
            ],
            "global_markets": [
                {
                    "symbol": item.symbol,
                    "name": item.name,
                    "direction": market_direction(item),
                    "nifty_correlation": correlation_strength(item),
                }
                for item in packet.global_markets
                if not item.stale
            ],
            "headlines": [
                {"id": item.id, "title": clean_title(item.title), "source": item.source}
                for item in packet.news
            ],
            "events": [
                {
                    "id": item.id,
                    "title": clean_title(item.title),
                    "importance": item.importance,
                }
                for item in packet.market_events
            ],
        }
        return f"""
You are a concise options trader writing a quick internal note for a colleague.

Write exactly one note for each supplied candidate, in the same order.
The server decides confidence and Consider/Wait/Avoid; do not calculate them.

Each note must contain:
- candidate_id copied exactly.
- thesis: two or three natural sentences explaining the inference and trade consequence.
- entry: one practical, strategy-specific sentence.
- risk_exit: one practical invalidation or exit sentence.
- optional supplied headline_ids, event_ids and market_symbols when genuinely relevant.

Do not put any number, price, percentage, score, timestamp, currency amount or strike
in thesis, entry or risk_exit. Do not recite indicators. Do not force a global-market
reference. Avoid formal report language, generic warnings and repeated sentence patterns.
NIFTY options are cash-settled, so never mention assignment or physical delivery.

Tone examples:
- "Volatility has compressed enough that selling premium has a real edge here, and the positioning is not fighting the direction. Main risk is the nearby event could gap it past the short strike before you can adjust."
- "The setup works while the market stays orderly, but the nearby event creates a real gap risk."
- "Momentum is fading rather than reversing, so this is better treated as a controlled range trade than a directional bet."

Return only schema-valid JSON.

DATA:
{json.dumps(payload, separators=(",", ":"))}
""".strip()

    @staticmethod
    def _call_concise_gemini(prompt: str) -> GeminiRecommendation:
        model = getattr(settings, "gemini_fast_model", "gemini-2.5-flash-lite")
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
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": RecommendationService._concise_schema(),
                    "maxOutputTokens": 3072,
                    "temperature": 0.75,
                },
            },
            timeout=settings.gemini_timeout_seconds,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return GeminiRecommendation.model_validate_json(text)

    @staticmethod
    def _sentence_count(text: str) -> int:
        return len([item for item in re.split(r"(?<=[.!?])\s+", text.strip()) if item])

    @classmethod
    def _validate_concise(
        cls,
        report: GeminiRecommendation,
        candidates: list[StrategyCandidate],
        packet: MarketEvidencePacket,
    ) -> list[str]:
        errors: list[str] = []
        expected = [item.id for item in candidates]
        actual = [item.candidate_id for item in report.ideas]
        if actual != expected:
            errors.append("Candidate IDs or order do not match the preview.")
        news_ids = {item.id for item in packet.news}
        event_ids = {item.id for item in packet.market_events}
        symbols = {item.symbol for item in packet.global_markets}
        prose_seen: set[str] = set()
        forbidden = re.compile(r"\d|₹|%|\b(?:INR|Rs\.?)\b", re.I)
        formal = re.compile(
            r"\b(?:executive summary|market-desk report|as an ai|provided data indicates|assignment|physical delivery)\b",
            re.I,
        )
        for index, idea in enumerate(report.ideas, 1):
            if cls._sentence_count(idea.thesis) not in {2, 3}:
                errors.append(f"Idea {index} thesis must contain two or three sentences.")
            if cls._sentence_count(idea.entry) != 1:
                errors.append(f"Idea {index} entry must contain one sentence.")
            if cls._sentence_count(idea.risk_exit) != 1:
                errors.append(f"Idea {index} risk exit must contain one sentence.")
            prose = " ".join((idea.thesis, idea.entry, idea.risk_exit))
            if forbidden.search(prose):
                errors.append(f"Idea {index} includes raw numeric data.")
            if formal.search(prose):
                errors.append(f"Idea {index} uses formal or generic report language.")
            normalized = re.sub(r"\W+", " ", idea.thesis.lower()).strip()
            if normalized in prose_seen:
                errors.append(f"Idea {index} duplicates another thesis.")
            prose_seen.add(normalized)
            if set(idea.headline_ids) - news_ids:
                errors.append(f"Idea {index} cites an unknown headline.")
            if set(idea.event_ids) - event_ids:
                errors.append(f"Idea {index} cites an unknown event.")
            if set(idea.market_symbols) - symbols:
                errors.append(f"Idea {index} cites an unknown market symbol.")
        return errors

    def _cached_evidence(
        self, chain: OptionChain, report_date: str
    ) -> MarketEvidencePacket:
        key = f"{chain.timestamp}:{report_date}"
        cached = self.evidence_cache.get(key)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        packet = self.collect_evidence(chain, report_date)
        self.evidence_cache[key] = (time.time(), packet)
        return packet

    @classmethod
    def _preview_idea(
        cls,
        candidate: StrategyCandidate,
        chain: OptionChain,
        *,
        speculative: bool = False,
    ) -> AITradeIdea:
        valid, rejection_reason, ratio = cls._validity(candidate)
        confidence = (
            "speculative"
            if speculative
            else "high"
            if candidate.score > 90 and candidate.liquidity_score > 90
            else "medium"
            if candidate.score > 80
            else "low"
        )
        rationale = (
            "This is an asymmetric setup intended for experienced traders."
            if speculative
            else "Confidence is calculated from scanner quality and executable liquidity."
        )
        return AITradeIdea(
            candidate_id=candidate.id,
            title=f"{candidate.strategy} setup",
            strategy=candidate.strategy,
            outlook=candidate.outlook,
            recommendation="Writing market view...",
            background="",
            analysis="",
            entry_plan="",
            risk_management="",
            confidence=confidence,
            confidence_rationale=rationale,
            risk_label=cls._risk_label(candidate),
            candidate=candidate if valid else None,
            chart_points=cls._chart(candidate, chain) if valid else [],
            valid_setup=valid,
            rejection_reason=rejection_reason,
            speculative=speculative,
            high_risk_reason=(
                "This setup accepts a lower win rate for a larger payoff and can lose its defined risk quickly."
                if speculative
                else None
            ),
            reward_risk_ratio=ratio,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def preview(self, request: RecommendationRequest) -> RecommendationResponse:
        with ThreadPoolExecutor(max_workers=2) as executor:
            near_future = executor.submit(
                provider.get_chain, request.expiry, request.refresh
            )
            far_future = (
                executor.submit(
                    provider.get_chain, request.far_expiry, request.refresh
                )
                if request.far_expiry and request.far_expiry != request.expiry
                else None
            )
            chain = near_future.result()
            far = far_future.result() if far_future else None
        scan_key = f"{chain.timestamp}:{far.timestamp if far else ''}"
        scan_result = None if request.refresh else self.scan_cache.get(scan_key)
        if scan_result is None:
            with ThreadPoolExecutor(max_workers=2) as executor:
                candidates_future = executor.submit(self._candidate_pool, chain, far)
                high_future = executor.submit(self._high_risk_pool, chain)
                candidates = candidates_future.result()
                high_risk = high_future.result()
            self.scan_cache[scan_key] = (candidates, high_risk)
        else:
            candidates, high_risk = scan_result
        valid_candidates = [item for item in candidates if self._validity(item)[0]]
        selected: list[StrategyCandidate] = []
        families: set[str] = set()
        for candidate in valid_candidates:
            family = self._family(candidate)
            if family in families or candidate.score < 65 or candidate.liquidity_score < 50:
                continue
            families.add(family)
            selected.append(candidate)
            if len(selected) == 5:
                break
        ideas = [self._preview_idea(item, chain) for item in selected]
        while len(ideas) < 5:
            ideas.append(
                AITradeIdea(
                    title="No Trade / Wait",
                    strategy="No trade",
                    outlook="Neutral",
                    recommendation="No additional qualified setup is available.",
                    background="",
                    analysis="",
                    entry_plan="",
                    risk_management="",
                    confidence="low",
                    confidence_rationale="No qualified canonical candidate was available.",
                    risk_label="No position risk",
                )
            )
        high_selected = high_risk[:2]
        high_ideas = [
            self._preview_idea(item, chain, speculative=True) for item in high_selected
        ]
        rejected = []
        for item in candidates:
            valid, reason, ratio = self._validity(item)
            if not valid:
                profit, loss = self._effective_profit_loss(item)
                rejected.append(
                    RejectedTradeIdea(
                        candidate_id=item.id,
                        strategy=item.strategy,
                        reason=reason or "Failed reward/risk validation.",
                        max_profit=profit,
                        max_loss=loss,
                        reward_risk_ratio=ratio,
                    )
                )
        analysis_id = hashlib.sha256(
            (
                f"{PROMPT_VERSION}:{request.model_dump_json()}:"
                f"{chain.timestamp}:{far.timestamp if far else ''}"
            ).encode()
        ).hexdigest()[:24]
        summary = calculate_chain_summary(chain)
        response = RecommendationResponse(
            analysis_id=analysis_id,
            narrative_pending=True,
            analysis_date=request.analysis_date,
            generated_by="rules",
            chain_timestamp=chain.timestamp,
            underlying_value=chain.underlying_value,
            market_context=MarketContext(stale=True),
            technical_indicators=TechnicalIndicators(stale=True),
            option_chain_summary=summary,
            market_events=[],
            global_markets=[],
            news=[],
            ideas=ideas,
            high_risk_ideas=high_ideas,
            rejected_ideas=rejected,
            validation_status="preview-ready",
            assumptions=[
                "Narrative commentary is generated separately from canonical strategy calculations."
            ],
            disclaimer="Verify live prices, liquidity and risk independently before trading.",
        )
        self.preview_cache[analysis_id] = (
            time.time(),
            {
                "request": request,
                "chain": chain,
                "far": far,
                "candidates": selected,
                "high_risk": high_selected,
                "response": response,
            },
        )
        return response

    @staticmethod
    def _apply_analysis(
        idea: AITradeIdea,
        analysis: DeskAnalysis,
        evidence: list[EvidenceReference] | None = None,
    ) -> AITradeIdea:
        return idea.model_copy(
            update={
                "recommendation": analysis.thesis,
                "background": analysis.thesis,
                "analysis": analysis.thesis,
                "entry_plan": analysis.entry,
                "risk_management": analysis.risk_exit,
                "desk_analysis": analysis,
                "evidence": evidence or [],
            }
        )

    def narrative(
        self,
        request: RecommendationNarrativeRequest,
        client_ip: str,
    ) -> RecommendationNarrativeResponse:
        if request.analysis_id in self.narrative_cache:
            return self.narrative_cache[request.analysis_id]
        cached = self.preview_cache.get(request.analysis_id)
        if not cached or time.time() - cached[0] > 600:
            raise LookupError("Recommendation preview expired.")
        state = cached[1]
        preview: RecommendationResponse = state["response"]
        chain: OptionChain = state["chain"]
        candidates: list[StrategyCandidate] = state["candidates"] + state["high_risk"]
        packet = self._cached_evidence(chain, state["request"].analysis_date)
        rules = {
            candidate.id: build_rules_analysis(candidate, packet)
            for candidate in candidates
        }
        generated_by: Literal["gemini", "rules"] = "rules"
        validation_status = "rules-fallback"
        fallback_reason: str | None = None
        gemini_lookup: dict[str, GeminiIdea] = {}
        if settings.gemini_api_key and len(candidates) == 7:
            try:
                self._check_limit(client_ip)
                report = self._call_concise_gemini(
                    self._concise_prompt(packet, candidates)
                )
                errors = self._validate_concise(report, candidates, packet)
                if errors:
                    logger.warning("Concise AI validation failed: %s", "; ".join(errors))
                    fallback_reason = (
                        "AI commentary was unavailable, so a concise desk view is shown."
                    )
                else:
                    gemini_lookup = {item.candidate_id: item for item in report.ideas}
                    generated_by = "gemini"
                    validation_status = "passed-server-grounded"
            except (httpx.HTTPError, KeyError, TypeError, ValidationError, ValueError) as error:
                logger.warning("Concise AI generation failed: %s", error)
                fallback_reason = (
                    "AI commentary was unavailable, so a concise desk view is shown."
                )
        else:
            fallback_reason = (
                "AI commentary was unavailable, so a concise desk view is shown."
            )

        def enrich(idea: AITradeIdea) -> AITradeIdea:
            if not idea.candidate_id or idea.candidate_id not in rules:
                return idea
            base = rules[idea.candidate_id]
            generated = gemini_lookup.get(idea.candidate_id)
            if generated:
                base = base.model_copy(
                    update={
                        "thesis": generated.thesis,
                    }
                )
                evidence = self._evidence(
                    generated, packet.news, packet.market_events, packet.global_markets
                )
            else:
                evidence = []
            return self._apply_analysis(idea, base, evidence)

        result = RecommendationNarrativeResponse(
            analysis_id=request.analysis_id,
            generated_by=generated_by,
            ideas=[enrich(item) for item in preview.ideas],
            high_risk_ideas=[enrich(item) for item in preview.high_risk_ideas],
            market_context=MarketContext(
                short_term_trend=packet.short_term_trend,
                medium_term_trend=packet.medium_term_trend,
                momentum=packet.momentum_strength,
                volatility_regime=packet.volatility_regime,
                stale=bool(packet.stale_inputs),
                data_timestamp=packet.technical_indicators.timestamp,
            ),
            technical_indicators=packet.technical_indicators,
            option_chain_summary=packet.option_chain_summary,
            market_events=packet.market_events,
            global_markets=packet.global_markets,
            news=packet.news,
            input_timestamps=packet.input_timestamps,
            stale_inputs=packet.stale_inputs,
            validation_status=validation_status,
            fallback_reason=fallback_reason,
        )
        self.narrative_cache[request.analysis_id] = result
        return result

    def generate(
        self, request: RecommendationRequest, client_ip: str
    ) -> RecommendationResponse:
        preview = self.preview(request)
        narrative = self.narrative(
            RecommendationNarrativeRequest(analysis_id=preview.analysis_id or ""),
            client_ip,
        )
        cached = self.preview_cache[preview.analysis_id or ""][1]
        packet = self._cached_evidence(cached["chain"], request.analysis_date)
        return preview.model_copy(
            update={
                "narrative_pending": False,
                "generated_by": narrative.generated_by,
                "ideas": narrative.ideas,
                "high_risk_ideas": narrative.high_risk_ideas,
                "market_context": MarketContext(
                    short_term_trend=packet.short_term_trend,
                    medium_term_trend=packet.medium_term_trend,
                    momentum=packet.momentum_strength,
                    volatility_regime=packet.volatility_regime,
                    stale=bool(packet.stale_inputs),
                    data_timestamp=packet.technical_indicators.timestamp,
                ),
                "technical_indicators": packet.technical_indicators,
                "option_chain_summary": packet.option_chain_summary,
                "market_events": packet.market_events,
                "global_markets": packet.global_markets,
                "news": packet.news,
                "input_timestamps": packet.input_timestamps,
                "stale_inputs": packet.stale_inputs,
                "validation_status": narrative.validation_status,
                "fallback_reason": narrative.fallback_reason,
            }
        )


recommendation_service = RecommendationService()
