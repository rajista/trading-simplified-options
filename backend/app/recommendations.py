from __future__ import annotations

import hashlib
import json
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
from .market_context import market_context_service
from .models import (
    AITradeIdea,
    EvidenceReference,
    MarketEvent,
    MarketMove,
    NewsItem,
    OptionChain,
    RecommendationChartPoint,
    RecommendationRequest,
    RecommendationResponse,
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


class GeminiIdea(BaseModel):
    candidate_id: str | None = None
    title: str
    outlook: str
    recommendation: str
    background: str
    analysis: str
    entry_plan: str
    risk_management: str
    headline_ids: list[str] = []
    event_ids: list[str] = []
    market_symbols: list[str] = []


class GeminiRecommendation(BaseModel):
    ideas: list[GeminiIdea] = Field(min_length=5, max_length=5)


class RecommendationService:
    def __init__(self) -> None:
        self.cache: dict[str, RecommendationResponse] = {}
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
        events = parse_manual_events(settings.market_events_json)
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
    def _family(candidate: StrategyCandidate) -> str:
        configured = candidate.metadata.get("strategy_family")
        if configured:
            return str(configured)
        name = candidate.strategy.lower()
        for family, markers in (
            ("calendar", ("calendar",)),
            ("diagonal", ("diagonal",)),
            ("credit", ("credit spread",)),
            ("debit", ("debit spread",)),
            ("iron-condor", ("iron condor",)),
            ("broken-wing-butterfly", ("broken-wing",)),
            ("butterfly", ("butterfly",)),
            ("risk-reversal", ("risk reversal",)),
            ("straddle", ("straddle",)),
            ("strangle", ("strangle",)),
        ):
            if any(marker in name for marker in markers):
                return family
        return name

    @staticmethod
    def _risk_label(candidate: StrategyCandidate) -> str:
        if candidate.metric_mode == "modeled":
            value = candidate.modeled_worst_loss
            return (
                f"Modeled range loss: Rs {value:,.0f}; not a guaranteed maximum"
                if value is not None
                else "Modeled risk unavailable"
            )
        if not candidate.metadata.get("bounded_loss", candidate.max_loss is not None):
            return "Maximum loss: Unlimited"
        return (
            f"Exact maximum loss: Rs {candidate.max_loss:,.0f}"
            if candidate.max_loss is not None
            else "Exact maximum loss unavailable"
        )

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
    def _rules_response(
        cls,
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
        fallback_reason: str | None = None,
    ) -> RecommendationResponse:
        context = market_context_service.get(force=request.refresh)
        chain_summary = calculate_chain_summary(chain)
        ideas: list[AITradeIdea] = []
        used_families: set[str] = set()
        for candidate in candidates:
            family = cls._family(candidate)
            if family in used_families or candidate.score < 65 or candidate.liquidity_score < 50:
                continue
            used_families.add(family)
            confidence, rationale = cls._confidence(candidate, indicators)
            ideas.append(
                AITradeIdea(
                    candidate_id=candidate.id,
                    title=f"{candidate.strategy} setup",
                    strategy=candidate.strategy,
                    outlook=candidate.outlook,
                    recommendation="Review only if current price remains within the displayed entry assumptions.",
                    background=(
                        f"NIFTY is {chain.underlying_value:,.2f}, ATM is "
                        f"{chain_summary.atm_strike or 0:,.0f}, and the option-implied "
                        f"move proxy is {chain_summary.expected_move_points or 0:,.0f} points."
                    ),
                    analysis=(
                        f"This candidate scored {candidate.score:.1f}/100 with "
                        f"{candidate.liquidity_score:.1f}/100 liquidity. "
                        f"{cls._risk_label(candidate)}."
                    ),
                    entry_plan="Enter as a complete limit-order combination when all quoted legs remain liquid.",
                    risk_management=(
                        f"Use the supplied breakevens and short strikes as triggers. "
                        f"{cls._risk_label(candidate)}."
                    ),
                    confidence=confidence,
                    confidence_rationale=rationale,
                    risk_label=cls._risk_label(candidate),
                    candidate=candidate,
                    chart_points=cls._chart(candidate, chain),
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
                )
            )
        stale_inputs = [
            name
            for name, stale in (
                ("NIFTY indicators", indicators.stale),
                ("option chain", chain.stale),
                ("global markets", any(item.stale for item in global_markets)),
            )
            if stale
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
            input_timestamps={
                "option_chain": chain.timestamp,
                "nifty_indicators": indicators.timestamp,
                **{item.symbol: item.timestamp for item in global_markets},
            },
            stale_inputs=stale_inputs,
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
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
    ) -> dict[str, Any]:
        summary = calculate_chain_summary(chain)
        relevant_strikes = {
            leg.strike for candidate in candidates for leg in candidate.legs
        }
        quote_context = []
        for row in chain.rows:
            if any(abs(row.strike - strike) <= (summary.strike_interval or 50) for strike in relevant_strikes):
                quote_context.append(
                    {
                        "strike": row.strike,
                        "ce": row.ce.model_dump(mode="json") if row.ce else None,
                        "pe": row.pe.model_dump(mode="json") if row.pe else None,
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
                for candidate in candidates[:15]
            ],
            "relevant_option_quotes": quote_context[:80],
            "global_markets": [item.model_dump(mode="json") for item in global_markets],
            "headlines": [item.model_dump(mode="json") for item in news],
            "nearby_events": [item.model_dump(mode="json") for item in events],
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

Generate exactly five output slots. Use a supplied candidate_id for every trade.
Trade ideas must use distinct strategy_family values and span at least three
families when three qualified families exist. If evidence or candidate quality is
insufficient, use candidate_id null and title "No Trade / Wait".

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

Do not calculate risk, invent a price, event, headline, source, correlation or
market relationship. Do not repeat background or entry-plan prose. Return only
valid JSON matching the schema, with no Markdown.{correction}

DATA:
{json.dumps(payload, indent=2)}
""".strip()

    @staticmethod
    def _call_gemini(prompt: str) -> GeminiRecommendation:
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
                    "responseJsonSchema": GeminiRecommendation.model_json_schema(),
                    "temperature": 0.2,
                },
            },
            timeout=60,
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
            if idea.candidate_id:
                candidate = candidate_lookup.get(idea.candidate_id)
                if not candidate:
                    errors.append(f"Idea {index} uses unknown candidate_id {idea.candidate_id}.")
                else:
                    families.append(cls._family(candidate))
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
                    )
                ),
                re.I,
            ):
                value = float(raw_number.replace(",", ""))
                if allowed_numbers and not any(abs(value - known) <= 0.11 for known in allowed_numbers):
                    errors.append(f"Idea {index} introduces unknown monetary value {value}.")
            backgrounds.append(re.sub(r"\W+", " ", idea.background.lower()).strip())
            entries.append(re.sub(r"\W+", " ", idea.entry_plan.lower()).strip())
        if len(families) != len(set(families)):
            errors.append("A strategy family is repeated.")
        available_families = {cls._family(item) for item in candidates}
        if len(available_families) >= 3 and len(set(families)) < 3:
            errors.append("Fewer than three strategy families were used.")
        if len(backgrounds) != len(set(backgrounds)):
            errors.append("Background prose is duplicated.")
        if len(entries) != len(set(entries)):
            errors.append("Entry-plan prose is duplicated.")
        return errors

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
        global_markets: list[MarketMove],
        news: list[NewsItem],
        events: list[MarketEvent],
        indicators: TechnicalIndicators,
    ) -> RecommendationResponse:
        base = cls._rules_response(
            request, chain, candidates, global_markets, news, events, indicators
        )
        lookup = {item.id: item for item in candidates}
        ideas: list[AITradeIdea] = []
        for item in report.ideas:
            candidate = lookup.get(item.candidate_id or "")
            if candidate:
                confidence, rationale = cls._confidence(candidate, indicators)
                strategy = candidate.strategy
                risk_label = cls._risk_label(candidate)
                chart = cls._chart(candidate, chain)
            else:
                confidence, rationale = "low", "No canonical candidate was attached."
                strategy, risk_label, chart = "No trade", "No position risk", []
            ideas.append(
                AITradeIdea(
                    **item.model_dump(exclude={"headline_ids", "event_ids", "market_symbols"}),
                    strategy=strategy,
                    confidence=confidence,
                    confidence_rationale=rationale,
                    risk_label=risk_label,
                    evidence=cls._evidence(item, news, events, global_markets),
                    candidate=candidate,
                    chart_points=chart,
                )
            )
        return base.model_copy(
            update={
                "generated_by": "gemini",
                "ideas": ideas,
                "validation_status": "passed",
                "fallback_reason": None,
            }
        )

    def generate(self, request: RecommendationRequest, client_ip: str) -> RecommendationResponse:
        key = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        if not request.refresh and key in self.cache:
            return self.cache[key]
        chain = provider.get_chain(request.expiry, force=request.refresh)
        far = (
            provider.get_chain(request.far_expiry, force=request.refresh)
            if request.far_expiry and request.far_expiry != request.expiry
            else None
        )
        indicators, nifty_prices = self._technical_indicators()
        with ThreadPoolExecutor(max_workers=8) as executor:
            global_markets = list(
                executor.map(
                    lambda item: self._market_move(item[0], item[1], nifty_prices),
                    GLOBAL_SYMBOLS.items(),
                )
            )
        news = self._news()
        events = self._events(request.analysis_date)
        candidates = self._candidate_pool(chain, far)
        if settings.gemini_api_key:
            try:
                self._check_limit(client_ip)
                payload = self._prompt_payload(
                    request, chain, candidates, global_markets, news, events, indicators
                )
                validation_errors: list[str] | None = None
                for _ in range(2):
                    gemini = self._call_gemini(self._prompt(payload, validation_errors))
                    validation_errors = self._validate_gemini(
                        gemini,
                        candidates,
                        news,
                        events,
                        global_markets,
                        self._numeric_facts(payload),
                    )
                    if not validation_errors:
                        result = self._attach_gemini(
                            gemini,
                            request,
                            chain,
                            candidates,
                            global_markets,
                            news,
                            events,
                            indicators,
                        )
                        break
                else:
                    result = self._rules_response(
                        request,
                        chain,
                        candidates,
                        global_markets,
                        news,
                        events,
                        indicators,
                        fallback_reason="Gemini output failed grounding validation after one retry.",
                    )
            except (ValueError, httpx.HTTPError, KeyError, TypeError, ValidationError) as error:
                result = self._rules_response(
                    request,
                    chain,
                    candidates,
                    global_markets,
                    news,
                    events,
                    indicators,
                    fallback_reason=f"Gemini unavailable: {type(error).__name__}.",
                )
        else:
            result = self._rules_response(
                request,
                chain,
                candidates,
                global_markets,
                news,
                events,
                indicators,
                fallback_reason="GEMINI_API_KEY is not configured.",
            )
        self.cache[key] = result
        return result


recommendation_service = RecommendationService()
