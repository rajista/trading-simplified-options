from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .models import (
    AnalysisRequest,
    AnalysisResponse,
    CoveredCallRequest,
    MarketContext,
    OptionChain,
    PortfolioStrategyRequest,
    ReportRequest,
    StrategyResponse,
    TradeReport,
)
from .analysis import analyze_candidate
from .market_context import market_context_service
from .provider import MarketDataError, provider
from .reports import ReportRateLimit, ReportUnavailable, report_service
from .strategies import (
    scan_broken_wing_butterflies,
    scan_box_spreads,
    scan_butterflies,
    scan_covered_calls,
    scan_christmas_trees,
    scan_collars,
    scan_credit_spreads,
    scan_debit_spreads,
    scan_iron_condors,
    scan_fences,
    scan_guts,
    scan_jade_lizards,
    scan_poor_mans_covered_calls,
    scan_risk_reversals,
    scan_straddles,
    scan_straps,
    scan_strangles,
    scan_strips,
    scan_seagulls,
    scan_time_spreads,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("options-api")
app = FastAPI(title="Trading Simplified Options API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    logger.info(
        "%s %s %s %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "app_version": "2.1.0",
        "symbol": "NIFTY",
        "lot_size": settings.nifty_lot_size,
        "ai_reports_configured": bool(settings.gemini_api_key),
        "ai_provider": "gemini" if settings.gemini_api_key else "rules",
    }


@app.get("/api/expiries")
def expiries(refresh: bool = False):
    try:
        return provider.get_expiries(force=refresh)
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/chain", response_model=OptionChain)
def chain(expiry: str, refresh: bool = False):
    try:
        return provider.get_chain(expiry, force=refresh)
    except MarketDataError as error:
        status = 400 if "Invalid expiry" in str(error) else 503
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.get("/api/market-context", response_model=MarketContext)
def market_context(refresh: bool = False):
    return market_context_service.get(force=refresh)


@app.get("/api/strategies/{strategy}", response_model=StrategyResponse)
def strategies(
    strategy: str,
    expiry: str,
    far_expiry: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    premium_basis: str = "ltp",
):
    try:
        strategy = {
            "others-1": "jade-lizard",
            "others-2": "strip",
        }.get(strategy, strategy)
        near = provider.get_chain(expiry)
        if strategy == "debit":
            candidates = scan_debit_spreads(near, limit)
        elif strategy == "credit":
            candidates = scan_credit_spreads(near, limit)
        elif strategy == "iron-condor":
            candidates = scan_iron_condors(near, limit)
        elif strategy == "butterfly":
            candidates = scan_butterflies(near, limit)
        elif strategy == "broken-wing-butterfly":
            candidates = scan_broken_wing_butterflies(near, limit)
        elif strategy == "risk-reversal":
            candidates = scan_risk_reversals(near, limit)
        elif strategy == "straddle":
            candidates = scan_straddles(near, limit)
        elif strategy == "strangle":
            candidates = scan_strangles(near, limit)
        elif strategy == "jade-lizard":
            candidates = scan_jade_lizards(near, limit)
        elif strategy == "box-spread":
            candidates = scan_box_spreads(near, limit)
        elif strategy == "seagull":
            candidates = scan_seagulls(near, limit)
        elif strategy == "christmas-tree":
            candidates = scan_christmas_trees(near, limit)
        elif strategy == "guts":
            candidates = scan_guts(near, limit)
        elif strategy == "strip":
            candidates = scan_strips(near, limit)
        elif strategy == "strap":
            candidates = scan_straps(near, limit)
        elif strategy == "poor-mans-covered-call":
            if not far_expiry or far_expiry == expiry:
                raise HTTPException(
                    status_code=400, detail="A different far_expiry is required."
                )
            far = provider.get_chain(far_expiry)
            candidates = scan_poor_mans_covered_calls(near, far, limit)
        elif strategy in {"calendar", "diagonal"}:
            if not far_expiry or far_expiry == expiry:
                raise HTTPException(
                    status_code=400, detail="A different far_expiry is required."
                )
            far = provider.get_chain(far_expiry)
            candidates = scan_time_spreads(
                near, far, diagonal=strategy == "diagonal", limit=limit
            )
        else:
            raise HTTPException(status_code=404, detail="Unknown strategy.")
        return StrategyResponse(
            strategy=strategy,
            timestamp=near.timestamp,
            underlying_value=near.underlying_value,
            lot_size=near.lot_size,
            candidates=candidates,
            stale=near.stale,
        )
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/strategies/portfolio", response_model=StrategyResponse)
def portfolio_strategy(request: PortfolioStrategyRequest):
    try:
        chain_data = provider.get_chain(request.expiry)
        scanner = scan_fences if request.strategy == "fence" else scan_collars
        candidates = scanner(
            chain_data,
            request.units,
            request.average_cost,
            request.current_price,
            request.limit,
        )
        return StrategyResponse(
            strategy=request.strategy,
            timestamp=chain_data.timestamp,
            underlying_value=chain_data.underlying_value,
            lot_size=chain_data.lot_size,
            candidates=candidates,
            stale=chain_data.stale,
        )
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/strategies/covered-call", response_model=StrategyResponse)
def covered_call(request: CoveredCallRequest):
    try:
        chain_data = provider.get_chain(request.expiry)
        return StrategyResponse(
            strategy="covered-call",
            timestamp=chain_data.timestamp,
            underlying_value=chain_data.underlying_value,
            lot_size=chain_data.lot_size,
            candidates=scan_covered_calls(
                chain_data,
                request.units,
                request.average_cost,
                request.current_price,
            ),
            stale=chain_data.stale,
        )
    except MarketDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/reports", response_model=TradeReport)
def report(request: ReportRequest, raw_request: Request):
    client_ip = raw_request.client.host if raw_request.client else "unknown"
    try:
        return report_service.generate(request, client_ip)
    except ReportRateLimit as error:
        raise HTTPException(status_code=429, detail=str(error)) from error
    except ReportUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/analysis", response_model=AnalysisResponse)
def analysis(request: AnalysisRequest):
    try:
        return analyze_candidate(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.exists():
    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        requested = frontend_dist / path
        if path and requested.is_file():
            return FileResponse(requested)
        return FileResponse(frontend_dist / "index.html")
