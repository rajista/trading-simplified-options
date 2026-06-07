from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from .analysis import AnalysisRequest, analyze_candidate
from .config import settings
from .market_context import market_context_service
from .models import (
    AITradeIdea,
    MarketMove,
    NewsItem,
    OptionChain,
    RecommendationChartPoint,
    RecommendationRequest,
    RecommendationResponse,
    StrategyCandidate,
)
from .provider import provider
from .strategies import (
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
}

NEWS_FEEDS = {
    "Google News NIFTY": "https://news.google.com/rss/search?q=NIFTY%2050%20market&hl=en-IN&gl=IN&ceid=IN:en",
    "Google News Global Markets": "https://news.google.com/rss/search?q=global%20stock%20markets%20oil%20gold&hl=en-IN&gl=IN&ceid=IN:en",
}


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
    def _market_move(symbol: str, name: str) -> MarketMove:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{symbol}?range=10d&interval=1d"
        )
        try:
            response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            closes = [
                float(value)
                for value in result["indicators"]["quote"][0]["close"]
                if value is not None
            ]
            timestamps = result.get("timestamp") or []
            if len(closes) < 2:
                raise ValueError("insufficient prices")
            one_day = closes[-1] / closes[-2] - 1
            base = closes[-6] if len(closes) >= 6 else closes[0]
            one_week = closes[-1] / base - 1
            timestamp = (
                datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).isoformat()
                if timestamps
                else None
            )
            return MarketMove(
                symbol=symbol,
                name=name,
                last=round(closes[-1], 2),
                one_day_return=round(one_day * 100, 2),
                one_week_return=round(one_week * 100, 2),
                timestamp=timestamp,
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return MarketMove(symbol=symbol, name=name)

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
                        items.append(
                            NewsItem(
                                title=title,
                                source=source,
                                published=published,
                                url=item.findtext("link"),
                            )
                        )
            except (httpx.HTTPError, ElementTree.ParseError):
                continue
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            key = item.title.lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return unique[:10]

    @staticmethod
    def _candidate_pool(near: OptionChain, far: OptionChain | None) -> list[StrategyCandidate]:
        pool: list[StrategyCandidate] = []
        scanners = [
            scan_debit_spreads,
            scan_credit_spreads,
            scan_iron_condors,
            scan_butterflies,
            scan_risk_reversals,
            scan_straddles,
            scan_strangles,
        ]
        for scanner in scanners:
            pool.extend(scanner(near, limit=4))
        if far:
            pool.extend(scan_time_spreads(near, far, diagonal=False, limit=3))
            pool.extend(scan_time_spreads(near, far, diagonal=True, limit=3))
        pool.sort(key=lambda item: item.score, reverse=True)
        diversified: list[StrategyCandidate] = []
        families: set[str] = set()
        for candidate in pool:
            family = candidate.metadata.get("strategy_family", candidate.strategy)
            if family in families:
                continue
            families.add(family)
            diversified.append(candidate)
            if len(diversified) >= 12:
                break
        return diversified

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
    def _rules_response(
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
    ) -> RecommendationResponse:
        context = market_context_service.get(force=request.refresh)
        ideas: list[AITradeIdea] = []
        selected = candidates[:5]
        for candidate in selected:
            ideas.append(
                AITradeIdea(
                    title=f"{candidate.strategy} idea",
                    strategy=candidate.strategy,
                    outlook=candidate.outlook,
                    recommendation=(
                        "Review this setup only if live prices, trend context and "
                        "risk limits still match the displayed assumptions."
                    ),
                    background=(
                        f"NIFTY is at {chain.underlying_value:,.2f}. Short-term "
                        f"trend: {context.short_term_trend}; medium-term trend: "
                        f"{context.medium_term_trend}."
                    ),
                    analysis=(
                        f"Candidate score {candidate.score:.0f}/100, liquidity "
                        f"{candidate.liquidity_score:.0f}/100. The idea is selected "
                        "from the current option-chain scanners and ranked metrics."
                    ),
                    entry_plan="Use limit orders and confirm every leg before entry.",
                    risk_management="Avoid oversized positions; respect modeled or maximum loss.",
                    confidence="low" if context.stale else "medium",
                    candidate=candidate,
                    chart_points=RecommendationService._chart(candidate, chain),
                )
            )
        while len(ideas) < 5:
            ideas.append(
                AITradeIdea(
                    title="Wait for better data",
                    strategy="No trade",
                    outlook="Neutral",
                    recommendation="Stand aside until liquid candidates and verified context improve.",
                    background="The deterministic fallback did not find enough diversified setups.",
                    analysis="No additional high-quality option-chain candidate was available.",
                    entry_plan="No entry.",
                    risk_management="Capital preservation is a valid outcome.",
                    confidence="low",
                )
            )
        return RecommendationResponse(
            analysis_date=request.analysis_date,
            generated_by="rules",
            chain_timestamp=chain.timestamp,
            underlying_value=chain.underlying_value,
            market_context=context,
            global_markets=global_markets,
            news=news,
            ideas=ideas[:5],
            assumptions=[
                "Uses current normalized NIFTY option-chain data.",
                "Yahoo Finance returns are best-effort and may be delayed.",
                "RSS headlines are used as context only and may be incomplete.",
                "Educational analysis only; not personalized investment advice.",
            ],
            disclaimer=(
                "Educational recommendations only. Verify live prices, news, "
                "events, liquidity and risk independently before making decisions."
            ),
        )

    def _gemini_response(
        self,
        request: RecommendationRequest,
        chain: OptionChain,
        candidates: list[StrategyCandidate],
        global_markets: list[MarketMove],
        news: list[NewsItem],
    ) -> RecommendationResponse:
        base = self._rules_response(request, chain, candidates, global_markets, news)
        schema = RecommendationResponse.model_json_schema()
        payload = base.model_dump(mode="json")
        payload["candidate_pool"] = [
            candidate.model_dump(mode="json", exclude={"metadata"})
            for candidate in candidates[:12]
        ]
        prompt = (
            "You are generating educational NIFTY options trade ideas for the "
            "analysis_date supplied below. Use only the option-chain data, scanner "
            "candidates, Yahoo return snapshots, RSS headlines and market context "
            "provided. Do not invent news, events, prices or sources. Recommend "
            "exactly five ideas. Each idea must include background, detailed "
            "analysis, entry plan, risk management and confidence. Keep generated_by "
            'as "gemini".\n\n'
            + json.dumps(payload, indent=2)
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
                    "temperature": 0.25,
                },
            },
            timeout=45,
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        report = RecommendationResponse.model_validate_json(text)
        return report.model_copy(
            update={
                "generated_by": "gemini",
                "global_markets": global_markets,
                "news": news,
                "market_context": base.market_context,
            }
        )

    def generate(
        self, request: RecommendationRequest, client_ip: str
    ) -> RecommendationResponse:
        key = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        if not request.refresh and key in self.cache:
            return self.cache[key]
        chain = provider.get_chain(request.expiry, force=request.refresh)
        far = (
            provider.get_chain(request.far_expiry, force=request.refresh)
            if request.far_expiry and request.far_expiry != request.expiry
            else None
        )
        global_markets = [
            self._market_move(symbol, name) for symbol, name in GLOBAL_SYMBOLS.items()
        ]
        news = self._news()
        candidates = self._candidate_pool(chain, far)
        if settings.gemini_api_key:
            try:
                self._check_limit(client_ip)
                result = self._gemini_response(
                    request, chain, candidates, global_markets, news
                )
            except (ValueError, httpx.HTTPError, KeyError, TypeError):
                result = self._rules_response(
                    request, chain, candidates, global_markets, news
                )
        else:
            result = self._rules_response(request, chain, candidates, global_markets, news)
        self.cache[key] = result
        return result


recommendation_service = RecommendationService()
