from typing import Any, Literal

from pydantic import BaseModel, Field


OptionType = Literal["CE", "PE"]


class OptionQuote(BaseModel):
    option_type: OptionType
    strike: float
    expiry: str
    bid: float = 0
    ask: float = 0
    last_price: float = 0
    volume: int = 0
    open_interest: int = 0
    change_in_oi: int = 0
    implied_volatility: float = 0


class ChainRow(BaseModel):
    strike: float
    ce: OptionQuote | None = None
    pe: OptionQuote | None = None


class OptionChain(BaseModel):
    symbol: str = "NIFTY"
    expiry: str
    timestamp: str
    underlying_value: float
    lot_size: int
    rows: list[ChainRow]
    stale: bool = False


class ExpiryResponse(BaseModel):
    symbol: str = "NIFTY"
    expiries: list[str]
    lot_size: int
    stale: bool = False


class StrategyLeg(BaseModel):
    action: Literal["BUY", "SELL"]
    option_type: OptionType
    strike: float
    expiry: str
    price: float
    implied_volatility: float = 0
    quantity: int = Field(default=1, ge=1)


class StrategyCandidate(BaseModel):
    id: str
    strategy: str
    outlook: str
    score: float
    legs: list[StrategyLeg]
    net_debit: float | None = None
    net_credit: float | None = None
    max_profit: float | None = None
    max_loss: float | None = None
    breakevens: list[float] = []
    return_on_risk: float | None = None
    scenario_profit: float | None = None
    scenario_loss: float | None = None
    metric_mode: Literal["fixed", "modeled"] = "fixed"
    estimated_peak_profit: float | None = None
    modeled_worst_loss: float | None = None
    modeled_return_risk: float | None = None
    estimated_breakevens: list[float] = []
    pricing_assumptions: list[str] = []
    liquidity_score: float
    notes: list[str] = []
    metadata: dict[str, Any] = {}


class StrategyResponse(BaseModel):
    strategy: str
    timestamp: str
    underlying_value: float
    lot_size: int
    candidates: list[StrategyCandidate]
    stale: bool = False


class CoveredCallRequest(BaseModel):
    expiry: str
    units: int = Field(gt=0)
    average_cost: float = Field(gt=0)
    current_price: float = Field(gt=0)


class PortfolioStrategyRequest(BaseModel):
    expiry: str
    strategy: Literal["fence", "collar"]
    units: int = Field(gt=0)
    average_cost: float = Field(gt=0)
    current_price: float = Field(gt=0)
    limit: int = Field(default=10, ge=1, le=50)


class MarketContext(BaseModel):
    short_term_trend: str = "Unavailable"
    medium_term_trend: str = "Unavailable"
    momentum: str = "Unavailable"
    volatility_regime: str = "Unavailable"
    global_macro_context: list[str] = []
    upcoming_events: list[str] = []
    sources: list[str] = []
    data_timestamp: str | None = None
    stale: bool = True


class ReportRequest(BaseModel):
    candidate: StrategyCandidate
    chain_timestamp: str
    underlying_value: float
    assumptions: list[str] = []
    analysis: dict[str, Any] | None = None
    market_context: MarketContext | None = None


class TradeReport(BaseModel):
    title: str
    setup: str
    rationale: str
    payoff: str
    breakevens: list[str]
    favorable_scenarios: list[str]
    adverse_scenarios: list[str]
    liquidity_concerns: list[str]
    exit_considerations: list[str]
    risks: list[str]
    assumptions: list[str]
    short_term_trend: str
    medium_term_trend: str
    momentum_and_volatility: str
    global_macro_context: list[str]
    upcoming_events: list[str]
    strategy_suitability: str
    trade_recommendation: str
    entry_conditions: list[str]
    adjustment_conditions: list[str]
    position_sizing_cautions: list[str]
    confidence: Literal["low", "medium", "high"]
    data_timestamps: list[str]
    sources: list[str]
    disclaimer: str
    generated_by: Literal["gemini", "rules"]


class AnalysisRequest(BaseModel):
    candidate: StrategyCandidate
    underlying_value: float = Field(gt=0)
    lot_size: int = Field(gt=0)
    chain_timestamp: str
    evaluation_days: int = Field(default=0, ge=0)
    iv_shift: float = Field(default=0, ge=-10, le=10)
    price_range_pct: float = Field(default=10, ge=2, le=25)


class AnalysisPoint(BaseModel):
    underlying_price: float
    today_pnl: float
    evaluation_pnl: float


class AnalysisResponse(BaseModel):
    points: list[AnalysisPoint]
    spot: float
    evaluation_days: int
    max_evaluation_days: int
    evaluation_label: str
    estimated_peak_profit: float
    modeled_worst_loss: float
    modeled_return_risk: float | None
    estimated_breakevens: list[float]
    net_debit: float | None = None
    net_credit: float | None = None
    assumptions: list[str]


class MarketMove(BaseModel):
    symbol: str
    name: str
    last: float | None = None
    one_day_return: float | None = None
    one_week_return: float | None = None
    timestamp: str | None = None
    source: str = "Yahoo Finance"


class NewsItem(BaseModel):
    title: str
    source: str
    published: str | None = None
    url: str | None = None


class RecommendationChartPoint(BaseModel):
    underlying_price: float
    pnl: float


class AITradeIdea(BaseModel):
    title: str
    strategy: str
    outlook: str
    recommendation: str
    background: str
    analysis: str
    entry_plan: str
    risk_management: str
    confidence: Literal["low", "medium", "high"]
    candidate: StrategyCandidate | None = None
    chart_points: list[RecommendationChartPoint] = []


class RecommendationRequest(BaseModel):
    expiry: str
    far_expiry: str | None = None
    analysis_date: str
    refresh: bool = False


class RecommendationResponse(BaseModel):
    analysis_date: str
    generated_by: Literal["gemini", "rules"]
    chain_timestamp: str
    underlying_value: float
    market_context: MarketContext
    global_markets: list[MarketMove]
    news: list[NewsItem]
    ideas: list[AITradeIdea]
    assumptions: list[str]
    disclaimer: str
