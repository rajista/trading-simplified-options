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
    one_month_return: float | None = None
    correlation_20d: float | None = None
    correlation_60d: float | None = None
    timestamp: str | None = None
    source: str = "Yahoo Finance"
    stale: bool = False


class NewsItem(BaseModel):
    id: str = ""
    title: str
    source: str
    published: str | None = None
    url: str | None = None


class TechnicalIndicators(BaseModel):
    last: float | None = None
    return_1d: float | None = None
    return_5d: float | None = None
    return_20d: float | None = None
    return_3m: float | None = None
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    ema_9: float | None = None
    ema_21: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    atr_14: float | None = None
    atr_percent: float | None = None
    bollinger_position: float | None = None
    realized_volatility_10d: float | None = None
    realized_volatility_20d: float | None = None
    support: float | None = None
    resistance: float | None = None
    swing_low: float | None = None
    swing_high: float | None = None
    distance_to_support_pct: float | None = None
    distance_to_resistance_pct: float | None = None
    timestamp: str | None = None
    source: str = "Yahoo Finance ^NSEI"
    stale: bool = False


class OILevel(BaseModel):
    strike: float
    value: int


class OptionChainSummary(BaseModel):
    spot: float
    atm_strike: float | None = None
    strike_interval: float | None = None
    atm_ce_ltp: float | None = None
    atm_pe_ltp: float | None = None
    atm_straddle_premium: float | None = None
    expected_move_points: float | None = None
    expected_move_percent: float | None = None
    atm_iv: float | None = None
    call_put_iv_skew: float | None = None
    total_oi_pcr: float | None = None
    near_atm_oi_pcr: float | None = None
    change_oi_pcr: float | None = None
    largest_call_oi: OILevel | None = None
    largest_put_oi: OILevel | None = None
    largest_call_oi_change: OILevel | None = None
    largest_put_oi_change: OILevel | None = None
    call_oi_wall: float | None = None
    put_oi_wall: float | None = None
    estimated_max_pain: float | None = None
    timestamp: str
    stale: bool = False


class MarketEvent(BaseModel):
    id: str
    date: str
    title: str
    importance: Literal["low", "medium", "high"] = "medium"
    source: str
    source_url: str | None = None
    verified: bool = False


class EvidenceReference(BaseModel):
    kind: Literal["headline", "event", "market", "indicator", "chain"]
    id: str
    label: str


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
    confidence_rationale: str = ""
    candidate_id: str | None = None
    risk_label: str = ""
    evidence: list[EvidenceReference] = []
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
    technical_indicators: TechnicalIndicators = TechnicalIndicators()
    option_chain_summary: OptionChainSummary | None = None
    market_events: list[MarketEvent] = []
    global_markets: list[MarketMove]
    news: list[NewsItem]
    ideas: list[AITradeIdea]
    input_timestamps: dict[str, str | None] = {}
    stale_inputs: list[str] = []
    validation_status: str = "not-run"
    fallback_reason: str | None = None
    assumptions: list[str]
    disclaimer: str
