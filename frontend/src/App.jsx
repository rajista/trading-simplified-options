import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "./api";

const tabs = [
  ["chain", "Option Chain"],
  ["ai-recommends", "AI Recommends"],
  ["debit", "Debit Spreads"],
  ["credit", "Credit Spreads"],
  ["iron-condor", "Iron Condors"],
  ["butterfly", "Butterflies"],
  ["broken-wing-butterfly", "Broken Wings"],
  ["risk-reversal", "Risk Reversals"],
  ["straddle", "Straddles"],
  ["strangle", "Strangles"],
  ["calendar", "Calendar Spreads"],
  ["diagonal", "Diagonal Spreads"],
  ["covered", "Covered Call Proxy"],
  ["others-1", "Others 1"],
  ["others-2", "Others 2"],
  ["report", "AI Trade Report"],
];

const money = (value) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0,
      }).format(value);

const number = (value, digits = 2) =>
  value == null ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: digits });

const today = () => new Date().toISOString().slice(0, 10);

const parseMarketTimestamp = (value) => {
  if (!value) return null;
  const direct = new Date(value);
  if (!Number.isNaN(direct.getTime())) return direct;
  const match = String(value).match(/^(\d{2})-([A-Za-z]{3})-(\d{4})[ T](\d{2}):(\d{2}):?(\d{2})?/);
  if (!match) return null;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const month = months.indexOf(match[2]);
  return month < 0 ? null : new Date(Number(match[3]), month, Number(match[1]), Number(match[4]), Number(match[5]), Number(match[6] || 0));
};

const strategyCopy = {
  debit: ["Debit Spreads", "Defined-risk directional structures ranked across calls and puts."],
  credit: ["Credit Spreads", "Bull put and bear call credit spreads ranked by premium, risk and liquidity."],
  "iron-condor": ["Iron Condors", "Range-bound four-leg structures with defined risk on both sides."],
  butterfly: ["Butterflies", "Long and short call/put butterflies around liquid strikes."],
  "broken-wing-butterfly": ["Broken-Wing Butterflies", "Asymmetric butterflies designed to shift risk and reward."],
  "risk-reversal": ["Risk Reversals", "Bullish and bearish synthetic directional structures with unbounded tail risk."],
  straddle: ["Straddles", "ATM long and short volatility structures."],
  strangle: ["Strangles", "OTM long and short volatility structures."],
  calendar: ["Calendar Spreads", "Same-strike, different-expiry structures evaluated at the near expiry."],
  diagonal: ["Diagonal Spreads", "Different-strike time spreads balancing direction, decay and volatility."],
};

const strategyGuidance = {
  debit: { suitable: "Use when you expect a directional move and want the entry debit to define maximum loss.", iv: "Low to normal", bias: "Bullish calls / bearish puts", risk: "Defined", expiry: "Single expiry" },
  credit: { suitable: "Use when you expect price to remain beyond the short strike and want time decay working in your favor.", iv: "Normal to elevated", bias: "Bullish puts / bearish calls", risk: "Defined", expiry: "Single expiry" },
  "iron-condor": { suitable: "Use when you expect NIFTY to remain inside a range and option premium is rich enough to justify the credit.", iv: "Elevated, preferably falling", bias: "Range-bound", risk: "Defined", expiry: "Single expiry" },
  butterfly: { suitable: "Use when you expect NIFTY to finish near a target strike or want a low-cost volatility structure.", iv: "Low to normal for long", bias: "Targeted / neutral", risk: "Defined", expiry: "Single expiry" },
  "broken-wing-butterfly": { suitable: "Use when you have a directional target but want asymmetric cost and risk versus a standard butterfly.", iv: "Normal", bias: "Directional neutral", risk: "Inspect each candidate", expiry: "Single expiry" },
  "risk-reversal": { suitable: "Use for a strong directional view only when you understand the substantial naked-option tail risk.", iv: "Skew-dependent", bias: "Strong bullish or bearish", risk: "Unbounded tail", expiry: "Single expiry" },
  straddle: { suitable: "Long straddles suit a large move from ATM; short straddles suit contraction but carry unlimited tail risk.", iv: "Long: low; short: high", bias: "Volatility", risk: "Varies by side", expiry: "Single expiry" },
  strangle: { suitable: "Long strangles suit a large move beyond OTM strikes; short strangles suit range conditions with unlimited tail risk.", iv: "Long: low; short: high", bias: "Volatility", risk: "Varies by side", expiry: "Single expiry" },
  calendar: { suitable: "Use when you expect price near the shared strike at near expiry and want time-decay and IV-term exposure.", iv: "Stable or rising far IV", bias: "Neutral / directional", risk: "Modeled debit", expiry: "Near and far" },
  diagonal: { suitable: "Use for a directional view combined with time-decay and volatility-term-structure exposure.", iv: "Stable term structure", bias: "Directional", risk: "Modeled", expiry: "Near and far" },
  covered: { suitable: "Use when holding NIFTYBEES with a neutral-to-moderately bullish view and accepting capped upside for premium.", iv: "Normal to elevated", bias: "Neutral / moderately bullish", risk: "ETF-option proxy", expiry: "Single option expiry" },
  report: { suitable: "Use after selecting and analyzing a candidate. The report compares it with supplied trend, macro and event context.", iv: "Selected strategy", bias: "Selected strategy", risk: "Educational analysis", expiry: "Selected strategy" },
};

const otherGroups = {
  "others-1": [
    { id: "jade-lizard", name: "Jade Lizard", suitable: "Neutral to moderately bullish markets with elevated IV.", iv: "Elevated", bias: "Neutral / bullish", risk: "Unbounded downside", expiry: "Single expiry" },
    { id: "box-spread", name: "Box Spread", suitable: "Synthetic financing or clear pricing discrepancies after all execution costs.", iv: "Low sensitivity", bias: "Market neutral", risk: "Defined", expiry: "Single expiry" },
    { id: "seagull", name: "Seagull", suitable: "A directional view where reduced premium is worth capped reward and one open tail.", iv: "Normal to elevated", bias: "Bullish or bearish", risk: "One unbounded tail", expiry: "Single expiry" },
    { id: "christmas-tree", name: "Christmas Tree", suitable: "A controlled move toward a target strike rather than an unlimited trend.", iv: "Normal", bias: "Directional", risk: "Defined", expiry: "Single expiry" },
    { id: "guts", name: "Guts", suitable: "A large move or volatility contraction, with higher premium than a comparable straddle.", iv: "Long: low; short: high", bias: "Volatility", risk: "Varies by side", expiry: "Single expiry" },
  ],
  "others-2": [
    { id: "fence", name: "Fence", suitable: "Protecting NIFTYBEES while reducing hedge cost and accepting capped upside/reduced tail protection.", iv: "Normal to elevated", bias: "Protective", risk: "Proxy hedge", expiry: "Single expiry" },
    { id: "collar", name: "Collar", suitable: "Limiting downside on NIFTYBEES while accepting a cap on upside.", iv: "Normal to elevated", bias: "Protective / bullish", risk: "Proxy hedge", expiry: "Single expiry" },
    { id: "poor-mans-covered-call", name: "Poor Man's Covered Call", suitable: "A moderately bullish outlook using a long-dated ITM call and near-dated call income.", iv: "Stable term structure", bias: "Moderately bullish", risk: "Modeled / defined debit", expiry: "Near and far" },
    { id: "strip", name: "Strip", suitable: "Expecting a large move with stronger bearish conviction.", iv: "Prefer lower IV", bias: "Volatility / bearish", risk: "Defined debit", expiry: "Single expiry" },
    { id: "strap", name: "Strap", suitable: "Expecting a large move with stronger bullish conviction.", iv: "Prefer lower IV", bias: "Volatility / bullish", risk: "Defined debit", expiry: "Single expiry" },
  ],
};

function metricMoney(value, bounded = true) {
  if (bounded === false && value == null) return "Unlimited";
  return money(value);
}

function StatusBar({ chain, loading, onRefresh }) {
  return (
    <div className="status-bar">
      <div>
        <span className={`status-dot ${chain?.stale ? "warn" : ""}`} />
        {loading ? "Updating market data…" : chain ? `NIFTY ${number(chain.underlying_value)}` : "Connecting…"}
      </div>
      <div className="status-meta">
        {chain?.timestamp && <span>As of {chain.timestamp}</span>}
        <button className="button ghost" onClick={onRefresh} disabled={loading}>
          Refresh
        </button>
      </div>
    </div>
  );
}

function Metric({ label, value, tone = "" }) {
  return (
    <div className={`metric ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ChainTable({ chain, search, setSearch, range, setRange }) {
  const rows = useMemo(() => {
    if (!chain) return [];
    return chain.rows.filter((row) => {
      const matchesSearch = !search || String(row.strike).includes(search);
      const matchesRange =
        range === "all" ||
        Math.abs(row.strike - chain.underlying_value) <= Number(range);
      return matchesSearch && matchesRange;
    });
  }, [chain, search, range]);

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <p className="eyebrow">Live derivatives board</p>
          <h2>Option Chain</h2>
        </div>
        <div className="filters">
          <input
            aria-label="Search strike"
            placeholder="Search strike"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <select aria-label="Strike range" value={range} onChange={(event) => setRange(event.target.value)}>
            <option value="500">± 500 points</option>
            <option value="1000">± 1,000 points</option>
            <option value="all">All strikes</option>
          </select>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr className="group-head">
              <th colSpan="7" className="ce-head">CALLS</th>
              <th>STRIKE</th>
              <th colSpan="7" className="pe-head">PUTS</th>
            </tr>
            <tr>
              <th>OI</th><th>OI Δ</th><th>Vol</th><th>IV</th><th>Bid</th><th>LTP</th><th>Ask</th>
              <th>Price</th>
              <th>Bid</th><th>LTP</th><th>Ask</th><th>IV</th><th>Vol</th><th>OI Δ</th><th>OI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const atm = Math.abs(row.strike - chain.underlying_value) < 25;
              return (
                <tr key={row.strike} className={atm ? "atm" : ""}>
                  <td>{number(row.ce?.open_interest, 0)}</td>
                  <td className={row.ce?.change_in_oi >= 0 ? "positive" : "negative"}>{number(row.ce?.change_in_oi, 0)}</td>
                  <td>{number(row.ce?.volume, 0)}</td><td>{number(row.ce?.implied_volatility)}</td>
                  <td>{number(row.ce?.bid)}</td><td className="ltp">{number(row.ce?.last_price)}</td><td>{number(row.ce?.ask)}</td>
                  <th>{number(row.strike, 0)}</th>
                  <td>{number(row.pe?.bid)}</td><td className="ltp">{number(row.pe?.last_price)}</td><td>{number(row.pe?.ask)}</td>
                  <td>{number(row.pe?.implied_volatility)}</td><td>{number(row.pe?.volume, 0)}</td>
                  <td className={row.pe?.change_in_oi >= 0 ? "positive" : "negative"}>{number(row.pe?.change_in_oi, 0)}</td>
                  <td>{number(row.pe?.open_interest, 0)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {!rows.length && <div className="empty">No strikes match the current filter.</div>}
      <SiteNavigationGuide />
    </section>
  );
}

function SuitabilityGuide({ title, guidance }) {
  if (!guidance) return null;
  return (
    <article className="suitability-card">
      <div><p className="eyebrow">When it is suitable</p><h3>{title}</h3><p>{guidance.suitable}</p></div>
      <dl>
        <div><dt>IV environment</dt><dd>{guidance.iv}</dd></div>
        <div><dt>Directional bias</dt><dd>{guidance.bias}</dd></div>
        <div><dt>Risk profile</dt><dd>{guidance.risk}</dd></div>
        <div><dt>Expiry</dt><dd>{guidance.expiry}</dd></div>
      </dl>
    </article>
  );
}

function SiteNavigationGuide() {
  return (
    <aside className="navigation-guide">
      <p className="eyebrow">How to navigate the site</p>
      <h3>From chain to trade analysis</h3>
      <ol>
        <li>Select an expiry and inspect LTP, liquidity, OI, volume and IV.</li>
        <li>Open a strategy tab matching your outlook and read its suitability guidance.</li>
        <li>Select <b>Analyse</b> to inspect payoff, breakevens, date and IV scenarios.</li>
        <li>Add verified macro/events context, then generate the educational trade report.</li>
      </ol>
    </aside>
  );
}

function PayoffMini({ candidate, spot, lotSize }) {
  const values = [];
  for (let i = -4; i <= 4; i += 1) {
    const price = spot + i * 100;
    let pnl = 0;
    candidate.legs.forEach((leg) => {
      const intrinsic =
        leg.option_type === "CE"
          ? Math.max(0, price - leg.strike)
          : Math.max(0, leg.strike - price);
      pnl += (leg.action === "BUY" ? intrinsic - leg.price : leg.price - intrinsic) * lotSize * (leg.quantity || 1);
    });
    values.push(pnl);
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const points = values
    .map((value, index) => `${index * 25},${52 - ((value - min) / Math.max(max - min, 1)) * 44}`)
    .join(" ");
  return (
    <svg className="payoff" viewBox="0 0 200 58" role="img" aria-label="Indicative payoff shape">
      <line x1="0" x2="200" y1="29" y2="29" />
      <polyline points={points} />
    </svg>
  );
}

function CandidateCard({ candidate, spot, lotSize, selected, onSelect }) {
  const modeled = candidate.metric_mode === "modeled";
  return (
    <article className={`candidate ${selected ? "selected" : ""}`}>
      <div className="candidate-top">
        <div>
          <span className="score">Score {number(candidate.score, 0)}</span>
          <h3>{candidate.strategy}</h3>
          <p>{candidate.outlook}</p>
        </div>
        <button className="button small" onClick={() => onSelect(candidate)}>
          {selected ? "Selected" : "Analyse"}
        </button>
      </div>
      <div className="legs">
        {candidate.legs.map((leg, index) => (
          <div className={`leg ${leg.action.toLowerCase()}`} key={`${leg.action}-${leg.strike}-${index}`}>
            <b>{leg.action}</b> {leg.quantity > 1 ? `${leg.quantity}x ` : ""}{leg.option_type} {number(leg.strike, 0)}
            <span>{leg.expiry} · {number(leg.price)}</span>
          </div>
        ))}
      </div>
      <div className="metrics-grid">
        <Metric label="Net debit" value={money(candidate.net_debit)} />
        <Metric label="Net credit" value={money(candidate.net_credit)} />
        <Metric label={modeled ? "Estimated peak profit" : "Max profit"} value={modeled ? money(candidate.estimated_peak_profit) : metricMoney(candidate.max_profit, candidate.metadata?.bounded_profit)} tone="good" />
        <Metric label={modeled ? "Modeled worst loss" : "Max loss"} value={modeled ? money(candidate.modeled_worst_loss) : metricMoney(candidate.max_loss, candidate.metadata?.bounded_loss)} tone="risk" />
        <Metric label={modeled ? "Modeled return / risk" : "Return / risk"} value={(modeled ? candidate.modeled_return_risk : candidate.return_on_risk) == null ? "—" : `${number(modeled ? candidate.modeled_return_risk : candidate.return_on_risk)}%`} />
        <Metric label="Liquidity" value={`${number(candidate.liquidity_score, 0)}/100`} />
      </div>
      {candidate.metric_mode === "fixed" && <PayoffMini candidate={candidate} spot={spot} lotSize={lotSize} />}
      {(modeled ? candidate.estimated_breakevens : candidate.breakevens)?.length > 0 && <p className="breakeven">{modeled ? "Estimated breakevens" : "Breakeven"}: {(modeled ? candidate.estimated_breakevens : candidate.breakevens).map(number).join(", ")}</p>}
      {candidate.notes?.map((note) => <p className="note" key={note}>{note}</p>)}
    </article>
  );
}

const spotChange = (price, spot) => {
  if (!spot || price == null) return null;
  return ((Number(price) - Number(spot)) / Number(spot)) * 100;
};

const signedPercent = (value) => {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${number(value, 2)}%`;
};

function AnalysisTooltip({ active, payload, label, spot }) {
  if (!active || !payload?.length) return null;
  const change = spotChange(label, spot);
  return (
    <div className="chart-tooltip">
      <strong>NIFTY {number(label, 0)}</strong>
      {payload.filter((item) => ["today_pnl", "evaluation_pnl"].includes(item.dataKey)).map((item) => (
        <div key={item.dataKey} style={{ color: item.color }}>
          {item.name}: {money(item.value)}
        </div>
      ))}
      {change != null && (
        <div className={`tooltip-change ${change >= 0 ? "positive" : "negative"}`}>
          Chg. from spot: {signedPercent(change)}
        </div>
      )}
    </div>
  );
}

function RecommendationChartTooltip({ active, payload, label, spot }) {
  if (!active || !payload?.length) return null;
  const point = payload.find((item) => item.dataKey === "pnl") || payload[0];
  const change = spotChange(label, spot);
  return (
    <div className="chart-tooltip">
      <strong>NIFTY {number(label, 0)}</strong>
      <div className={Number(point?.value) >= 0 ? "tooltip-profit" : "tooltip-loss"}>
        Profit / Loss: {money(point?.value)}
      </div>
      {change != null && (
        <div className={`tooltip-change ${change >= 0 ? "positive" : "negative"}`}>
          Chg. from spot: {signedPercent(change)}
        </div>
      )}
    </div>
  );
}

function RecommendationMiniChart({ points, spot }) {
  if (!points?.length) return <div className="empty mini-empty">Chart unavailable for this idea.</div>;
  return (
    <div className="recommend-chart">
      <ResponsiveContainer width="100%" height={180}>
        <ComposedChart data={points} margin={{ top: 12, right: 18, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#dedbd2" />
          <XAxis dataKey="underlying_price" tickFormatter={(value) => number(value, 0)} />
          <YAxis tickFormatter={(value) => `₹${number(value, 0)}`} width={70} />
          <Tooltip content={<RecommendationChartTooltip spot={spot} />} />
          <ReferenceLine y={0} stroke="#798092" />
          <Line type="linear" dataKey="pnl" name="Scenario P&L" stroke="#0b8a65" strokeWidth={2.5} dot={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function EnhancedRecommendationChart({ points, candidate, spot }) {
  const [simulator, setSimulator] = useState(null);
  const [dragging, setDragging] = useState(false);
  if (!points?.length) return <div className="empty mini-empty">Chart unavailable for this idea.</div>;
  const allChartData = points.map((point) => ({
    ...point,
    profit: Math.max(point.pnl, 0),
    loss: Math.min(point.pnl, 0),
  }));
  const breakevens = candidate?.breakevens?.length
    ? candidate.breakevens
    : candidate?.estimated_breakevens || [];
  const anchors = [
    spot,
    ...breakevens,
    ...(candidate?.legs || []).map((leg) => leg.strike),
  ].filter((value) => Number.isFinite(Number(value))).map(Number);
  const anchorMin = anchors.length ? Math.min(...anchors) : spot;
  const anchorMax = anchors.length ? Math.max(...anchors) : spot;
  const padding = Math.max((anchorMax - anchorMin) * 0.22, (spot || 1) * 0.018);
  const focusMin = Math.max((spot || anchorMin) * 0.94, anchorMin - padding);
  const focusMax = Math.min((spot || anchorMax) * 1.06, anchorMax + padding);
  const focused = allChartData.filter(
    (point) => point.underlying_price >= focusMin && point.underlying_price <= focusMax,
  );
  const chartData = focused.length >= 6 ? focused : allChartData;
  const maxPoint = chartData.reduce((best, point) => point.pnl > best.pnl ? point : best, chartData[0]);
  return (
    <div className="recommend-chart interactive-chart">
      <ResponsiveContainer width="100%" height={250}>
        <ComposedChart
          data={chartData}
          margin={{ top: 30, right: 24, bottom: 14, left: 10 }}
          onMouseDown={(state) => {
            setDragging(true);
            if (state?.activePayload?.[0]?.payload) setSimulator(state.activePayload[0].payload);
          }}
          onMouseMove={(state) => {
            if (dragging && state?.activePayload?.[0]?.payload) setSimulator(state.activePayload[0].payload);
          }}
          onMouseUp={() => setDragging(false)}
          onMouseLeave={() => setDragging(false)}
        >
          <defs>
            <linearGradient id="recommendProfit" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22c55e" stopOpacity={0.28} />
              <stop offset="100%" stopColor="#22c55e" stopOpacity={0.04} />
            </linearGradient>
            <linearGradient id="recommendLoss" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ef4444" stopOpacity={0.04} />
              <stop offset="100%" stopColor="#ef4444" stopOpacity={0.26} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 5" stroke="#d9dee7" vertical={false} />
          <XAxis dataKey="underlying_price" tickFormatter={(value) => number(value, 0)}
            tick={{ fill: "#657086", fontSize: 10 }} axisLine={{ stroke: "#aeb7c5" }} />
          <YAxis tickFormatter={(value) => `₹${number(value, 0)}`} width={70} />
          <Tooltip content={<RecommendationChartTooltip spot={spot} />} />
          <ReferenceLine y={0} stroke="#798092" />
          <Area type="linear" dataKey="profit" stroke="none" fill="url(#recommendProfit)" />
          <Area type="linear" dataKey="loss" stroke="none" fill="url(#recommendLoss)" />
          {breakevens.map((value) => (
            <ReferenceLine key={value} x={value} stroke="#d49b32" strokeDasharray="5 4"
              label={{ value: "BE", position: "top", fill: "#9a6816", fontSize: 9 }} />
          ))}
          {spot && <ReferenceLine x={spot} className="spot-pulse-line" stroke="#2563eb" strokeWidth={2} />}
          {simulator && <ReferenceLine x={simulator.underlying_price} stroke="#334155" strokeDasharray="2 2" />}
          <ReferenceDot x={maxPoint.underlying_price} y={maxPoint.pnl} r={5} fill="#d4a33b" stroke="#8a5c0c"
            label={{ value: money(maxPoint.pnl), position: "top", fill: "#8a5c0c", fontSize: 9 }} />
          <Line type="linear" dataKey="pnl" name="Scenario P&L" stroke="#078365" strokeWidth={3}
            dot={false} activeDot={{ r: 5, fill: "#ffffff", stroke: "#078365", strokeWidth: 3 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="chart-simulator">
        {simulator
          ? `At ${money(simulator.underlying_price)}: P&L = ${money(simulator.pnl)} · Chg. from spot ${signedPercent(spotChange(simulator.underlying_price, spot))}`
          : `Drag across chart to simulate P&L · Max profit point ${money(maxPoint.pnl)}`}
      </div>
    </div>
  );
}

function ScoreGauge({ score = 0 }) {
  const normalized = Math.max(0, Math.min(100, score));
  const color = normalized >= 80 ? "#0f6b3a" : normalized >= 60 ? "#b45309" : "#b42318";
  return (
    <div className="score-gauge" aria-label={`Trade score ${number(normalized, 0)} out of 100`}>
      <svg viewBox="0 0 120 70" role="img">
        <path className="gauge-track" d="M15 60 A45 45 0 0 1 105 60" />
        <path className="gauge-value" d="M15 60 A45 45 0 0 1 105 60"
          pathLength="100" stroke={color} style={{ strokeDasharray: `${normalized} 100` }} />
      </svg>
      <b>{number(normalized, 0)}</b><span>/100</span>
    </div>
  );
}

function PersistentSection({ id, title, children, defaultOpen = false }) {
  const key = `desk-section:${id}`;
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(key);
    return saved == null ? defaultOpen : saved === "true";
  });
  return (
    <details className="desk-section" open={open} onToggle={(event) => {
      const next = event.currentTarget.open;
      setOpen(next);
      localStorage.setItem(key, String(next));
    }}>
      <summary>{title}</summary>
      <div>{children}</div>
    </details>
  );
}

function AIRecommendsPanelLegacy({ expiry, farExpiry, chain }) {
  const [analysisDate, setAnalysisDate] = useState(today());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const generate = async (refresh = false) => {
    if (!expiry) return;
    setLoading(true);
    setError("");
    try {
      setData(await api.recommendations({
        expiry,
        far_expiry: farExpiry,
        analysis_date: analysisDate,
        refresh,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!expiry) return;
    generate(false);
  }, [expiry, farExpiry]);

  return (
    <section>
      <div className="section-intro wide">
        <p className="eyebrow">AI market desk</p>
        <h2>AI Recommends</h2>
        <p>AI receives the selected date, option-chain snapshot, scanner candidates, RSS headlines and Yahoo Finance return snapshots for US markets, oil, gold and Asian indices.</p>
      </div>
      <div className="recommend-controls panel">
        <label>Analysis date
          <input type="date" value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} />
        </label>
        <button className="button" onClick={() => generate(false)} disabled={loading}>{loading ? "Generating…" : "Generate 5 ideas"}</button>
        <button className="button ghost" onClick={() => generate(true)} disabled={loading}>Refresh data</button>
        <p>Current NIFTY: <b>{chain ? number(chain.underlying_value) : "—"}</b>. Ideas are educational and must be checked against live prices.</p>
      </div>
      {error && <div className="alert">{error}</div>}
      {loading && <div className="loading-card">Collecting option chain, RSS, Yahoo Finance returns and recommendation context…</div>}
      {data && (
        <>
          <div className="recommend-summary">
            <article>
              <p className="eyebrow">Source</p>
              <h3>{data.generated_by === "gemini" ? "AI Recommendations" : "Rules fallback"}</h3>
              <p>Chain timestamp: {data.chain_timestamp}</p>
              <p>NIFTY trend: {data.market_context.short_term_trend} short term, {data.market_context.medium_term_trend} medium term.</p>
            </article>
            <article>
              <p className="eyebrow">Momentum and volatility</p>
              <h3>{data.market_context.momentum}</h3>
              <p>{data.market_context.volatility_regime}</p>
            </article>
          </div>
          <section className="market-moves">
            <div className="section-intro compact">
              <p className="eyebrow">Yahoo Finance return snapshot</p>
              <h2>Global Market Inputs</h2>
            </div>
            <div className="move-grid">
              {data.global_markets.map((move) => (
                <article key={move.symbol} className="move-card">
                  <span>{move.symbol}</span>
                  <h3>{move.name}</h3>
                  <p>Last: {number(move.last)}</p>
                  <p className={(move.one_day_return || 0) >= 0 ? "positive" : "negative"}>1D: {move.one_day_return == null ? "—" : `${number(move.one_day_return)}%`}</p>
                  <p className={(move.one_week_return || 0) >= 0 ? "positive" : "negative"}>1W: {move.one_week_return == null ? "—" : `${number(move.one_week_return)}%`}</p>
                </article>
              ))}
            </div>
          </section>
          <section className="news-panel panel">
            <p className="eyebrow">RSS news inputs</p>
            <h2>Headlines Fed To AI</h2>
            {!data.news.length && <p className="muted">No RSS headlines were available. AI is told not to invent news.</p>}
            <ul>
              {data.news.map((item) => (
                <li key={`${item.source}-${item.title}`}>
                  {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : item.title}
                  <span>{item.source}{item.published ? ` · ${item.published}` : ""}</span>
                </li>
              ))}
            </ul>
          </section>
          <div className="recommend-grid">
            {data.ideas.map((idea, index) => (
              <article className="recommend-card" key={`${idea.title}-${index}`}>
                <span className="score">Idea {index + 1} · {idea.confidence} confidence</span>
                <h3>{idea.title}</h3>
                <p className="muted">{idea.strategy} · {idea.outlook}</p>
                {idea.desk_analysis && <span className={`decision-badge ${idea.desk_analysis.decision.toLowerCase()}`}>{idea.desk_analysis.decision}</span>}
                <p><b>Market view:</b> {idea.desk_analysis?.thesis || idea.desk_analysis?.executive_summary || idea.recommendation}</p>
                {idea.desk_analysis ? (
                  <details className="desk-details">
                    <summary>Read full desk analysis</summary>
                    <DeskAnalysisView analysis={idea.desk_analysis} />
                  </details>
                ) : (
                  <>
                    <p><b>Background:</b> {idea.background}</p>
                    <p><b>Analysis:</b> {idea.analysis}</p>
                    <p><b>Entry plan:</b> {idea.entry_plan}</p>
                    <p><b>Risk management:</b> {idea.risk_management}</p>
                  </>
                )}
                {idea.candidate && (
                  <div className="legs">
                    {idea.candidate.legs.map((leg, legIndex) => (
                      <div className={`leg ${leg.action.toLowerCase()}`} key={`${idea.title}-${legIndex}`}>
                        <b>{leg.action}</b> {leg.quantity > 1 ? `${leg.quantity}x ` : ""}{leg.option_type} {number(leg.strike, 0)}
                        <span>{leg.expiry} · {number(leg.price)}</span>
                      </div>
                    ))}
                  </div>
                )}
                <RecommendationMiniChart points={idea.chart_points} />
              </article>
            ))}
          </div>
          <div className="disclaimer">{data.disclaimer}</div>
        </>
      )}
    </section>
  );
}

function EvidencePanel({ title, subtitle, children, open = false }) {
  return (
    <details className="evidence-panel panel" open={open}>
      <summary><span>{title}</span><small>{subtitle}</small></summary>
      <div className="evidence-content">{children}</div>
    </details>
  );
}

function EvidenceMetric({ label, value, suffix = "" }) {
  return (
    <div className="evidence-metric">
      <span>{label}</span>
      <b>{value == null ? "—" : `${typeof value === "string" ? value : number(value)}${suffix}`}</b>
    </div>
  );
}

function DeskAnalysisView({ analysis, storageKey = "report" }) {
  if (!analysis) return null;
  if (analysis.thesis) {
    return (
      <div className="concise-analysis">
        <p className="summary-thesis">{analysis.thesis}</p>
        <p><b>Entry:</b> {analysis.entry}</p>
        <p><b>Risk/exit:</b> {analysis.risk_exit}</p>
      </div>
    );
  }
  const sections = [
    ["NIFTY price action", analysis.price_action_analysis],
    ["Option chain and volatility", analysis.option_chain_analysis],
    ["News and event risk", analysis.news_event_risk],
    ["Score and liquidity", analysis.score_liquidity_analysis],
    ["Strategy and strike rationale", analysis.strategy_rationale],
    ["Entry and execution", analysis.entry_execution_plan],
    ["Risk analysis", analysis.risk_analysis],
    ["Adjustment, invalidation and exit", analysis.adjustment_exit_plan],
  ];
  return (
    <div className="desk-analysis">
      <PersistentSection id={`${storageKey}:evidence`} title="Supporting and conflicting evidence" defaultOpen>
        <div className="evidence-columns">
          <section><h4>Supporting evidence</h4><ul>{analysis.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul></section>
          <section><h4>Conflicting evidence</h4><ul>{analysis.conflicting_evidence.map((item) => <li key={item}>{item}</li>)}</ul></section>
        </div>
      </PersistentSection>
      {sections.map(([title, text], index) => (
        <PersistentSection id={`${storageKey}:section-${index}`} title={title} key={title} defaultOpen={index < 2}>
          <p>{text}</p>
        </PersistentSection>
      ))}
      <PersistentSection id={`${storageKey}:global`} title="Relevant global cues">
        <ul>{analysis.global_cues.map((item) => <li key={item}>{item}</li>)}</ul>
      </PersistentSection>
      <PersistentSection id={`${storageKey}:monitoring`} title="Monitoring checklist">
        <ol>{analysis.monitoring_checklist.map((item) => <li key={item}>{item}</li>)}</ol>
      </PersistentSection>
    </div>
  );
}

const payoffMetrics = (idea) => {
  const candidate = idea.candidate;
  if (!candidate) return { maxProfit: null, maxLoss: null, ratio: idea.reward_risk_ratio };
  const modeled = candidate.metric_mode === "modeled";
  const maxProfit = modeled ? candidate.estimated_peak_profit : candidate.max_profit;
  const maxLoss = modeled ? candidate.modeled_worst_loss : candidate.max_loss;
  return {
    maxProfit,
    maxLoss,
    ratio: idea.reward_risk_ratio ?? (maxProfit != null && maxLoss > 0 ? maxProfit / maxLoss : null),
  };
};

const brokerLegText = (leg) => {
  const expiry = String(leg.expiry || "").replaceAll("-", "").toUpperCase();
  return `NIFTY ${expiry} ${number(leg.strike, 0).replaceAll(",", "")} ${leg.option_type} ${leg.action} @ ${leg.price}`;
};

const copyBrokerLeg = async (leg, button) => {
  const text = brokerLegText(leg);
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    document.execCommand("copy");
    field.remove();
  }
  if (button) {
    const original = button.textContent;
    button.textContent = "✓";
    window.setTimeout(() => { button.textContent = original; }, 1200);
  }
};

function TradeIdeaCard({ idea, index, spot, chainTimestamp, summaryMode = false }) {
  const candidate = idea.candidate;
  const metrics = payoffMetrics(idea);
  if (!idea.valid_setup) {
    return (
      <article className="rejected-card">
        <b>⚠ Setup Rejected</b>
        <h3>{idea.title}</h3>
        <p>{idea.rejection_reason}</p>
      </article>
    );
  }
  const confidenceClass = idea.speculative ? "speculative" : idea.confidence;
  const riskHeat = Math.min(100, ((metrics.maxLoss || 0) / 150000) * 100);
  const generated = idea.generated_at ? new Date(idea.generated_at) : null;
  const minutesAgo = generated && !Number.isNaN(generated.getTime())
    ? Math.max(0, Math.round((Date.now() - generated.getTime()) / 60000))
    : null;
  const chainDate = parseMarketTimestamp(chainTimestamp);
  const stale = chainDate && Date.now() - chainDate.getTime() > 30 * 60000;
  return (
    <article className={`recommend-card confidence-${confidenceClass} ${summaryMode ? "summary-card" : ""}`}>
      <div className="risk-thermometer" aria-label={`Risk intensity ${number(riskHeat, 0)} percent`}>
        <span style={{ height: `${Math.max(8, riskHeat)}%` }} />
      </div>
      <div className="card-topline">
        <span>Idea {index}</span>
        <span>{minutesAgo == null ? "Generated now" : `⏱ Generated ${minutesAgo} mins ago`}</span>
        {stale && <span className="stale-warning">⚠ Stale data</span>}
      </div>
      <div className="card-title-row">
        <div>
          <h3>{idea.title}</h3>
          <p className="muted">{idea.strategy} · {idea.outlook}</p>
        </div>
        <ScoreGauge score={candidate?.score || 0} />
      </div>
      <div className="card-badges">
        <span className={`confidence-badge ${confidenceClass}`}>
          {idea.speculative ? "SPECULATIVE" : `${idea.confidence.toUpperCase()} CONFIDENCE`}
        </span>
        {idea.desk_analysis && (
          <span className={`decision-badge ${idea.desk_analysis.decision.toLowerCase()}`}>
            {idea.desk_analysis.decision}
          </span>
        )}
        {metrics.ratio != null && <span className="rr-badge">R:R {number(metrics.ratio)}:1</span>}
      </div>
      {summaryMode ? (
        <p className="summary-thesis">{idea.desk_analysis?.thesis || idea.recommendation}</p>
      ) : (
        <>
          {idea.speculative && <div className="high-risk-callout"><b>Why High Risk</b><p>{idea.high_risk_reason}</p></div>}
          <div className="risk-provenance">
            <b>{idea.risk_label}</b>
            <span>{idea.confidence_rationale}</span>
          </div>
          {idea.desk_analysis
            ? <DeskAnalysisView analysis={idea.desk_analysis} storageKey={idea.candidate_id || `idea-${index}`} />
            : <div className="narrative-placeholder">Writing market view…</div>}
          {!!idea.evidence?.length && (
            <div className="evidence-chips">
              {idea.evidence.map((item) => <span key={`${item.kind}-${item.id}`}>{item.kind}: {item.label}</span>)}
            </div>
          )}
          {candidate && (
            <div className="legs">
              {candidate.legs.map((leg, legIndex) => (
                <div className={`leg-pill ${leg.action.toLowerCase()}`} key={`${idea.title}-${legIndex}`}>
                  <div><b>{leg.action} {leg.quantity > 1 ? `${leg.quantity}x ` : ""}{leg.option_type} <strong>{number(leg.strike, 0)}</strong></b>
                    <span>{leg.expiry} · {number(leg.price)}</span></div>
                  <button aria-label={`Copy ${leg.action} ${leg.option_type} ${leg.strike}`} title="Copy broker-ready leg"
                    onClick={(event) => copyBrokerLeg(leg, event.currentTarget)}>⧉</button>
                </div>
              ))}
            </div>
          )}
          <EnhancedRecommendationChart points={idea.chart_points} candidate={candidate} spot={spot} />
        </>
      )}
    </article>
  );
}

function ComparisonTable({ ideas, spot }) {
  const [sort, setSort] = useState({ key: "ratio", direction: "desc" });
  const rows = ideas.filter((idea) => idea.valid_setup && idea.candidate).map((idea) => {
    const breakevens = idea.candidate.breakevens?.length
      ? idea.candidate.breakevens
      : idea.candidate.estimated_breakevens || [];
    const breakevenChanges = breakevens.map((value) => spotChange(value, spot));
    return {
      idea,
      ...payoffMetrics(idea),
      confidence: idea.speculative ? "SPECULATIVE" : idea.confidence.toUpperCase(),
      strategy: idea.strategy,
      expiry: idea.candidate.legs[0]?.expiry || "—",
      breakevens,
      breakevenChanges,
      breakevenDistance: breakevenChanges.length
        ? Math.min(...breakevenChanges.map((value) => Math.abs(value)))
        : null,
    };
  });
  const sorted = [...rows].sort((a, b) => {
    const left = a[sort.key] ?? -Infinity;
    const right = b[sort.key] ?? -Infinity;
    const result = typeof left === "string" ? left.localeCompare(right) : left - right;
    return sort.direction === "asc" ? result : -result;
  });
  const heading = (label, key) => (
    <button onClick={() => setSort((current) => ({
      key,
      direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
    }))}>{label}</button>
  );
  return (
    <div className="comparison-wrap panel">
      <table className="comparison-table">
        <thead><tr><th>{heading("Strategy", "strategy")}</th><th>{heading("Max Loss", "maxLoss")}</th>
          <th>{heading("Max Profit", "maxProfit")}</th><th>{heading("R:R", "ratio")}</th>
          <th>{heading("Breakeven (% from spot)", "breakevenDistance")}</th>
          <th>{heading("Confidence", "confidence")}</th><th>{heading("Expiry", "expiry")}</th></tr></thead>
        <tbody>{sorted.map((row) => <tr key={row.idea.candidate_id}><td>{row.strategy}</td>
          <td>{money(row.maxLoss)}</td><td>{money(row.maxProfit)}</td><td>{number(row.ratio)}:1</td>
          <td>{row.breakevens.length
            ? row.breakevens.map((value, index) => (
              <span className="comparison-be" key={value}>
                {number(value, 0)} <small>{signedPercent(row.breakevenChanges[index])}</small>
              </span>
            ))
            : "—"}</td>
          <td>{row.confidence}</td><td>{row.expiry}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function AIRecommendsPanel({ expiry, farExpiry }) {
  const [analysisDate, setAnalysisDate] = useState(today());
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [writing, setWriting] = useState(false);
  const [error, setError] = useState("");
  const [compareMode, setCompareMode] = useState(false);
  const [summaryMode, setSummaryMode] = useState(false);

  const generate = async (refresh = false) => {
    if (!expiry) return;
    setLoading(true);
    setWriting(false);
    setError("");
    const payload = {
      expiry,
      far_expiry: farExpiry,
      analysis_date: analysisDate,
      refresh,
    };
    try {
      let preview = await api.recommendationPreview(payload);
      setData(preview);
      setLoading(false);
      setWriting(true);
      let narrative;
      try {
        narrative = await api.recommendationNarrative(preview.analysis_id);
      } catch (err) {
        if (err.status !== 410) throw err;
        preview = await api.recommendationPreview({ ...payload, refresh: true });
        setData(preview);
        narrative = await api.recommendationNarrative(preview.analysis_id);
      }
      setData((current) => ({
        ...current,
        ...narrative,
        narrative_pending: false,
        rejected_ideas: current?.rejected_ideas || [],
        assumptions: current?.assumptions || [],
        disclaimer: current?.disclaimer || preview.disclaimer,
        chain_timestamp: current?.chain_timestamp || preview.chain_timestamp,
        underlying_value: current?.underlying_value || preview.underlying_value,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
      setWriting(false);
    }
  };

  useEffect(() => {
    if (expiry) generate(false);
  }, [expiry, farExpiry]);

  return (
    <section>
      <div className="section-intro wide">
        <p className="eyebrow">AI market desk</p>
        <h2>AI Recommends</h2>
        <p>AI explains server-calculated NIFTY indicators, option-chain structure, scanner candidates, verified events, supplied headlines and timestamped cross-market evidence.</p>
      </div>
      <div className="recommend-controls panel">
        <label>Report date
          <input type="date" value={analysisDate} onChange={(event) => setAnalysisDate(event.target.value)} />
        </label>
        <button className="button" onClick={() => generate(false)} disabled={loading || writing}>{loading ? "Finding ideas…" : writing ? "Writing market view…" : "Generate 5 ideas"}</button>
        <button className="button ghost" onClick={() => generate(true)} disabled={loading || writing}>Refresh data</button>
        <p><b>Latest-data report:</b> the date labels the report. Market inputs use their latest available timestamps; this is not historical analysis.</p>
      </div>
      {error && <div className="alert">{error}</div>}
      {loading && <div className="loading-card">Finding the strongest valid structures…</div>}
      {writing && <div className="loading-card subtle">Cards are ready. Writing the market view in the background…</div>}
      {data && (
        <>
          <div className={`market-mood-banner ${String(data.market_context.short_term_trend).toLowerCase().includes("bear") ? "bearish" : "neutral"}`}>
            <b>{String(data.market_context.short_term_trend).toLowerCase().includes("bear") ? "🔴 Bearish Pressure" : "🟡 Sideways Grind"}</b>
            <span>{data.market_context.momentum}. Options traders should favor structures whose breakevens respect the current ATR and OI walls.</span>
          </div>
          <div className="sticky-snapshot">
            <span>NIFTY Spot <b>{number(data.underlying_value)}</b></span>
            <span className={(data.global_markets.find((item) => item.symbol === "^INDIAVIX")?.one_day_return || 0) >= 0 ? "up" : "down"}>
              India VIX <b>{number(data.global_markets.find((item) => item.symbol === "^INDIAVIX")?.last)}</b>
            </span>
            <span>ATM IV <b>{number(data.option_chain_summary?.atm_iv)}%</b></span>
            <span>Expected Move <b>{number(data.option_chain_summary?.expected_move_points)} pts</b></span>
            <span className={(data.option_chain_summary?.near_atm_oi_pcr || 0) >= 1 ? "up" : "down"}>
              PCR <b>{number(data.option_chain_summary?.near_atm_oi_pcr)}</b>
            </span>
          </div>
          <div className="recommend-summary">
            <article>
              <p className="eyebrow">Current market view</p>
              <h3>{data.market_context.short_term_trend} / {data.market_context.medium_term_trend}</h3>
              <p>{data.market_context.momentum}</p>
              <p>{data.market_context.volatility_regime}</p>
              {!!data.stale_inputs?.length && <p className="negative">Stale or unavailable: {data.stale_inputs.join(", ")}</p>}
            </article>
          </div>

          <EvidencePanel title="Data used for this view" subtitle="Indicators, option chain, global markets, events and headlines">
          <div className="evidence-stack">
            <EvidencePanel title="NIFTY Indicators and Price Action" subtitle={`Yahoo Finance · ${data.technical_indicators.timestamp || "timestamp unavailable"}`} open>
              <div className="evidence-metrics">
                <EvidenceMetric label="NIFTY" value={data.technical_indicators.last} />
                <EvidenceMetric label="1D return" value={data.technical_indicators.return_1d} suffix="%" />
                <EvidenceMetric label="5D return" value={data.technical_indicators.return_5d} suffix="%" />
                <EvidenceMetric label="20D return" value={data.technical_indicators.return_20d} suffix="%" />
                <EvidenceMetric label="3M return" value={data.technical_indicators.return_3m} suffix="%" />
                <EvidenceMetric label="SMA 20 / 50" value={data.technical_indicators.sma_20 == null ? null : `${number(data.technical_indicators.sma_20)} / ${number(data.technical_indicators.sma_50)}`} />
                <EvidenceMetric label="SMA 200" value={data.technical_indicators.sma_200} />
                <EvidenceMetric label="EMA 9 / 21" value={data.technical_indicators.ema_9 == null ? null : `${number(data.technical_indicators.ema_9)} / ${number(data.technical_indicators.ema_21)}`} />
                <EvidenceMetric label="RSI 14" value={data.technical_indicators.rsi_14} />
                <EvidenceMetric label="MACD / signal" value={data.technical_indicators.macd == null ? null : `${number(data.technical_indicators.macd)} / ${number(data.technical_indicators.macd_signal)}`} />
                <EvidenceMetric label="ATR 14" value={data.technical_indicators.atr_14} />
                <EvidenceMetric label="ATR %" value={data.technical_indicators.atr_percent} suffix="%" />
                <EvidenceMetric label="Realized vol 10D / 20D" value={data.technical_indicators.realized_volatility_10d == null ? null : `${number(data.technical_indicators.realized_volatility_10d)}% / ${number(data.technical_indicators.realized_volatility_20d)}%`} />
                <EvidenceMetric label="Bollinger position" value={data.technical_indicators.bollinger_position} />
                <EvidenceMetric label="Support" value={data.technical_indicators.support} />
                <EvidenceMetric label="Resistance" value={data.technical_indicators.resistance} />
              </div>
            </EvidencePanel>

            <EvidencePanel title="Option-Chain Structure and OI Walls" subtitle={data.option_chain_summary?.timestamp || "timestamp unavailable"}>
              {data.option_chain_summary && (
                <div className="evidence-metrics">
                  <EvidenceMetric label="ATM strike" value={data.option_chain_summary.atm_strike} />
                  <EvidenceMetric label="Strike interval" value={data.option_chain_summary.strike_interval} />
                  <EvidenceMetric label="ATM CE / PE LTP" value={`${number(data.option_chain_summary.atm_ce_ltp)} / ${number(data.option_chain_summary.atm_pe_ltp)}`} />
                  <EvidenceMetric label="Straddle / move proxy" value={data.option_chain_summary.expected_move_points} />
                  <EvidenceMetric label="Expected move" value={data.option_chain_summary.expected_move_percent} suffix="%" />
                  <EvidenceMetric label="ATM IV" value={data.option_chain_summary.atm_iv} suffix="%" />
                  <EvidenceMetric label="Put-call IV skew" value={data.option_chain_summary.call_put_iv_skew} />
                  <EvidenceMetric label="Total OI PCR" value={data.option_chain_summary.total_oi_pcr} />
                  <EvidenceMetric label="Near-ATM PCR" value={data.option_chain_summary.near_atm_oi_pcr} />
                  <EvidenceMetric label="Change OI PCR" value={data.option_chain_summary.change_oi_pcr} />
                  <EvidenceMetric label="Put OI wall" value={data.option_chain_summary.put_oi_wall} />
                  <EvidenceMetric label="Call OI wall" value={data.option_chain_summary.call_oi_wall} />
                  <EvidenceMetric label="Estimated max pain" value={data.option_chain_summary.estimated_max_pain} />
                </div>
              )}
              <p className="context-note">Expected move is an ATM-straddle proxy. Max pain is an open-interest estimate, not a forecast.</p>
            </EvidencePanel>

            <EvidencePanel title="Nearby Verified Events" subtitle="Seven calendar days before or after the report date">
              {!data.market_events.length && <p className="muted">No verified nearby event was available. AI is instructed to say so.</p>}
              <div className="event-list">
                {data.market_events.map((event) => (
                  <article key={event.id}>
                    <b>{event.date} · {event.title}</b>
                    <span>{event.importance} importance · {event.source} · {event.verified ? "verified" : "unverified"}</span>
                    {event.source_url && <a href={event.source_url} target="_blank" rel="noreferrer">Official source</a>}
                  </article>
                ))}
              </div>
            </EvidencePanel>

            <EvidencePanel title="Global Returns and Correlations" subtitle="Yahoo Finance, best effort">
              <div className="move-grid">
                {data.global_markets.map((move) => (
                  <article key={move.symbol} className="move-card">
                    <span>{move.symbol}</span>
                    <h3>{move.name}</h3>
                    <p>Last: {number(move.last)}</p>
                    <p className={(move.one_day_return || 0) >= 0 ? "positive" : "negative"}>1D: {move.one_day_return == null ? "—" : `${number(move.one_day_return)}%`}</p>
                    <p>1W / 1M: {move.one_week_return == null ? "—" : `${number(move.one_week_return)}%`} / {move.one_month_return == null ? "—" : `${number(move.one_month_return)}%`}</p>
                    <p>Corr. 20D / 60D: {number(move.correlation_20d)} / {number(move.correlation_60d)}</p>
                    <small>{move.timestamp || "timestamp unavailable"}</small>
                  </article>
                ))}
              </div>
            </EvidencePanel>

            <EvidencePanel title="Exact Headlines Supplied to AI" subtitle={`${data.news.length} contextual RSS items`}>
              <div className="news-panel embedded">
                {!data.news.length && <p className="muted">No RSS headlines were available. AI is told not to invent news.</p>}
                <ul>
                  {data.news.map((item) => (
                    <li key={item.id || `${item.source}-${item.title}`}>
                      <code>{item.id}</code>
                      {item.url ? <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a> : item.title}
                      <span>{item.source}{item.published ? ` · ${item.published}` : ""}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </EvidencePanel>
          </div>
          </EvidencePanel>

          <div className="section-intro compact ai-ideas-heading">
            <p className="eyebrow">Grounded output</p>
            <h2>Five Trade-Plan Slots</h2>
            <p>Every displayed structure passes the server-side payoff sanity check.</p>
            <div className="view-toggles">
              <button className={`button ghost ${compareMode ? "active" : ""}`} onClick={() => setCompareMode(!compareMode)}>Compare Ideas</button>
              <button className={`button ghost ${summaryMode ? "active" : ""}`} onClick={() => setSummaryMode(!summaryMode)}>One-Tap Summary</button>
            </div>
          </div>
          {compareMode && <ComparisonTable ideas={[...data.ideas, ...(data.high_risk_ideas || [])]} spot={data.underlying_value} />}
          <div className="ai-recommend-grid">
            {data.ideas.map((idea, index) => (
              <TradeIdeaCard key={`${idea.title}-${index}`} idea={idea} index={index + 1}
                spot={data.underlying_value} chainTimestamp={data.chain_timestamp} summaryMode={summaryMode} />
            ))}
          </div>
          {!!data.high_risk_ideas?.length && (
            <section className="high-risk-section">
              <div className="section-intro compact">
                <p className="eyebrow">Experienced traders only</p>
                <h2>⚡ High Risk-Reward Ideas</h2>
                <p>Asymmetric setups with outsized reward potential. Capital at risk is higher. Suitable for experienced traders only.</p>
              </div>
              <div className="ai-recommend-grid">
                {data.high_risk_ideas.map((idea, index) => (
                  <TradeIdeaCard key={idea.candidate_id || index} idea={idea} index={index + 6}
                    spot={data.underlying_value} chainTimestamp={data.chain_timestamp} summaryMode={summaryMode} />
                ))}
              </div>
            </section>
          )}
          {!!data.rejected_ideas?.length && (
            <details className="rejected-drawer panel">
              <summary>Rejected Ideas ({data.rejected_ideas.length})</summary>
              {data.rejected_ideas.map((idea) => (
                <article key={idea.candidate_id}><b>{idea.strategy}</b><p>{idea.reason}</p>
                  <span>Max profit {money(idea.max_profit)} · Max loss {money(idea.max_loss)} · R:R {number(idea.reward_risk_ratio)}</span></article>
              ))}
            </details>
          )}
          <div className="disclaimer">{data.disclaimer}</div>
        </>
      )}
    </section>
  );
}

function AnalysisModal({ candidate, chain, expiry, farExpiry, open, onClose, report, setReport, marketContext, setMarketContext }) {
  const [analysis, setAnalysis] = useState(null);
  const [evaluationDays, setEvaluationDays] = useState(3650);
  const [ivShift, setIvShift] = useState(0);
  const [priceRange, setPriceRange] = useState(5);
  const [loading, setLoading] = useState(false);
  const [reportLoading, setReportLoading] = useState(false);
  const [error, setError] = useState("");
  const closeRef = useRef(null);
  const previousFocus = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    previousFocus.current = document.activeElement;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const handleKey = (event) => {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const focusable = document.querySelectorAll(
          ".analysis-modal button, .analysis-modal input, .analysis-modal select",
        );
        if (!focusable.length) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      document.body.style.overflow = "";
      previousFocus.current?.focus?.();
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    setAnalysis(null);
    setEvaluationDays(3650);
    setIvShift(0);
    setPriceRange(5);
  }, [candidate?.id, open]);

  useEffect(() => {
    if (!open || !candidate || !chain) return;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError("");
      try {
        const result = await api.analysis({
          candidate,
          underlying_value: chain.underlying_value,
          lot_size: chain.lot_size,
          chain_timestamp: chain.timestamp,
          evaluation_days: evaluationDays,
          iv_shift: ivShift,
          price_range_pct: priceRange,
        });
        setAnalysis(result);
        if (evaluationDays > result.max_evaluation_days) {
          setEvaluationDays(result.max_evaluation_days);
        }
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }, 180);
    return () => clearTimeout(timer);
  }, [open, candidate, chain, evaluationDays, ivShift, priceRange]);

  if (!open || !candidate) return null;
  const chartData = (analysis?.points || []).map((point) => ({
    ...point,
    profit: Math.max(0, point.evaluation_pnl),
    loss: Math.min(0, point.evaluation_pnl),
  }));
  const reset = () => {
    setEvaluationDays(analysis?.max_evaluation_days ?? 3650);
    setIvShift(0);
    setPriceRange(5);
  };
  const generateReport = async () => {
    setReportLoading(true);
    setError("");
    try {
      setReport(await api.report({
        candidate,
        chain_timestamp: chain.timestamp,
        underlying_value: chain.underlying_value,
        report_date: today(),
        expiry,
        far_expiry: farExpiry,
        assumptions: [
          `NIFTY lot size ${chain.lot_size}`,
          "Quotes may be delayed or stale",
          "Educational analysis only",
        ],
        analysis,
        market_context: marketContext,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setReportLoading(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="analysis-modal" role="dialog" aria-modal="true" aria-labelledby="analysis-title">
        <header className="modal-head">
          <div>
            <p className="eyebrow">Interactive strategy analysis</p>
            <h2 id="analysis-title">{candidate.strategy}</h2>
            <p>{candidate.outlook} · Score {number(candidate.score, 0)} · Liquidity {number(candidate.liquidity_score, 0)}/100</p>
          </div>
          <button ref={closeRef} className="modal-close" onClick={onClose} aria-label="Close analysis">×</button>
        </header>

        <div className="analysis-layout">
          <aside className="analysis-sidebar">
            <div className="legs modal-legs">
              {candidate.legs.map((leg) => (
                <div className={`leg ${leg.action.toLowerCase()}`} key={`${leg.action}-${leg.strike}-${leg.expiry}`}>
                  <b>{leg.action}</b> {leg.quantity > 1 ? `${leg.quantity}x ` : ""}{leg.option_type} {number(leg.strike, 0)}
                  <span>{leg.expiry} · ₹{number(leg.price)} · IV {number(leg.implied_volatility)}%</span>
                </div>
              ))}
            </div>
            <div className="analysis-metrics">
              <Metric label="Net debit" value={money(analysis?.net_debit)} />
              <Metric label="Net credit" value={money(analysis?.net_credit)} />
              <Metric label={candidate.metric_mode === "modeled" ? "Estimated peak profit" : "Max profit"} value={metricMoney(candidate.metric_mode === "modeled" ? analysis?.estimated_peak_profit : candidate.max_profit, candidate.metadata?.bounded_profit)} tone="good" />
              <Metric label={candidate.metric_mode === "modeled" ? "Modeled worst loss" : "Max loss"} value={metricMoney(candidate.metric_mode === "modeled" ? analysis?.modeled_worst_loss : candidate.max_loss, candidate.metadata?.bounded_loss)} tone="risk" />
              <Metric label="Modeled return / risk" value={analysis?.modeled_return_risk == null ? "—" : `${number(analysis.modeled_return_risk)}%`} />
              <Metric label="Breakevens" value={analysis?.estimated_breakevens?.length ? analysis.estimated_breakevens.map((value) => number(value, 0)).join(" – ") : "Outside range"} />
            </div>
            <div className="basis-box">
              <p className="eyebrow">Calculation basis</p>
              <span>Lot size <b>{candidate.metadata?.lot_size || chain.lot_size}</b></span>
              <span>Premium basis <b>{candidate.metadata?.premium_basis || "LTP"}</b></span>
              <span>Payoff type <b>{candidate.metadata?.payoff_type === "expiry" ? "Expiry payoff" : "Modeled payoff"}</b></span>
            </div>
            <button className="button report-button" onClick={generateReport} disabled={reportLoading || loading}>
              {reportLoading ? "Generating…" : "Generate Trade Report"}
            </button>
          </aside>

          <div className="analysis-main">
            <div className="analysis-controls">
              <label>Evaluation date <span>{analysis?.evaluation_label || "—"}</span>
                <input type="range" min="0" max={analysis?.max_evaluation_days || 0} value={Math.min(evaluationDays, analysis?.max_evaluation_days || 0)} onChange={(event) => setEvaluationDays(Number(event.target.value))} />
              </label>
              <label>IV shift <span>{ivShift > 0 ? "+" : ""}{ivShift}%</span>
                <input type="range" min="-10" max="10" step="1" value={ivShift} onChange={(event) => setIvShift(Number(event.target.value))} />
              </label>
              <label>Price range
                <select value={priceRange} onChange={(event) => setPriceRange(Number(event.target.value))}>
                  <option value="5">± 5%</option><option value="10">± 10%</option><option value="15">± 15%</option><option value="20">± 20%</option>
                </select>
              </label>
              <button className="button ghost" onClick={reset}>Reset</button>
            </div>

            <div className="chart-shell" aria-label="Interactive profit and loss chart">
              {loading && <div className="chart-loading">Repricing strategy…</div>}
              <ResponsiveContainer width="100%" height={390}>
                <ComposedChart data={chartData} margin={{ top: 20, right: 24, bottom: 18, left: 18 }}>
                  <defs>
                    <linearGradient id="analysisProfit" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#22c55e" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#22c55e" stopOpacity={0.04} />
                    </linearGradient>
                    <linearGradient id="analysisLoss" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#ef4444" stopOpacity={0.04} />
                      <stop offset="100%" stopColor="#ef4444" stopOpacity={0.28} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 5" stroke="#d9dee7" vertical={false} />
                  <XAxis dataKey="underlying_price" type="number" domain={["dataMin", "dataMax"]}
                    tickFormatter={(value) => number(value, 0)}
                    tick={{ fill: "#657086", fontSize: 11 }} axisLine={{ stroke: "#aeb7c5" }} />
                  <YAxis tickFormatter={(value) => `₹${number(value, 0)}`} width={82} />
                  <Tooltip content={<AnalysisTooltip spot={chain.underlying_value} />} />
                  <Legend />
                  <ReferenceLine y={0} stroke="#798092" />
                  <ReferenceLine x={chain.underlying_value} className="spot-pulse-line" stroke="#2563eb"
                    strokeWidth={2} label={{ value: "SPOT", position: "top", fill: "#2563eb", fontSize: 10 }} />
                  {analysis?.estimated_breakevens?.map((value) => (
                    <ReferenceLine key={value} x={value} stroke="#d49b32" strokeDasharray="5 4"
                      label={{ value: "BE", position: "top", fill: "#9a6816", fontSize: 10 }} />
                  ))}
                  <Area type="linear" dataKey="profit" name="Profit zone" stroke="none" fill="url(#analysisProfit)" />
                  <Area type="linear" dataKey="loss" name="Loss zone" stroke="none" fill="url(#analysisLoss)" />
                  <Line type="linear" dataKey="today_pnl" name="Today P&L" stroke="#2b62be" strokeWidth={2.25}
                    strokeDasharray="6 4" dot={false}
                    activeDot={{ r: 4, fill: "#ffffff", stroke: "#2b62be", strokeWidth: 2 }} />
                  <Line type="linear" dataKey="evaluation_pnl" name={`${analysis?.evaluation_label || "Evaluation"} P&L`}
                    stroke="#078365" strokeWidth={3.25} dot={false}
                    activeDot={{ r: 5, fill: "#ffffff", stroke: "#078365", strokeWidth: 3 }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            {error && <div className="alert">{error}</div>}
            <div className="analysis-assumptions">
              {(analysis?.assumptions || candidate.pricing_assumptions || []).map((item) => <p key={item}>{item}</p>)}
            </div>
            <MarketContextEditor context={marketContext} setContext={setMarketContext} />
            {report && (
              <article className="inline-report">
                <div className="report-title-row">
                  <div><p className="eyebrow">Trade report</p><h3>{report.title}</h3></div>
                  <span className="source-badge">{report.generated_by === "gemini" ? "AI" : "Rules fallback"}</span>
                </div>
                {report.fallback_reason && <div className="alert warning">{report.fallback_reason}</div>}
                {report.desk_analysis && (
                  <>
                    <span className={`decision-badge ${report.desk_analysis.decision.toLowerCase()}`}>{report.desk_analysis.decision}</span>
                    <p><b>Executive summary:</b> {report.desk_analysis.executive_summary}</p>
                    <DeskAnalysisView analysis={report.desk_analysis} />
                  </>
                )}
                <p><b>Setup:</b> {report.setup}</p>
                <p><b>Rationale:</b> {report.rationale}</p>
                <p><b>Payoff:</b> {report.payoff}</p>
                <p><b>Trend:</b> Short term {report.short_term_trend}; medium term {report.medium_term_trend}. {report.momentum_and_volatility}</p>
                <p><b>Suitability:</b> {report.strategy_suitability}</p>
                <p><b>Trade recommendation:</b> {report.trade_recommendation}</p>
                <div className="report-columns">
                  <div><h4>Favorable</h4><ul>{report.favorable_scenarios.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  <div><h4>Risks</h4><ul>{report.risks.map((item) => <li key={item}>{item}</li>)}</ul></div>
                </div>
                <div className="report-columns">
                  <div><h4>Global macro</h4><ul>{report.global_macro_context.map((item) => <li key={item}>{item}</li>)}</ul></div>
                  <div><h4>Upcoming events</h4><ul>{report.upcoming_events.map((item) => <li key={item}>{item}</li>)}</ul></div>
                </div>
                <div className="disclaimer">{report.disclaimer}</div>
              </article>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

function StrategyPanel({ title, description, guidance, candidates, loading, error, selected, setSelected, spot, lotSize, showScore = true }) {
  return (
    <section>
      <div className="section-intro">
        <p className="eyebrow">Balanced opportunity ranking</p>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      <SuitabilityGuide title={title} guidance={guidance} />
      {loading && <div className="loading-card">Scanning liquid strikes and calculating payoff…</div>}
      {error && <div className="alert">{error}</div>}
      {!loading && !error && !candidates.length && <div className="empty">No valid candidates found for these expiries.</div>}
      <div className="candidate-grid">
        {candidates.map((candidate) => (
          <CandidateCard
            key={candidate.id}
            candidate={candidate}
            spot={spot}
            lotSize={lotSize}
            selected={selected?.id === candidate.id}
            onSelect={setSelected}
          />
        ))}
      </div>
      {showScore && <ScoreExplanation />}
    </section>
  );
}

function ScoreExplanation() {
  return (
    <aside className="score-explanation">
      <p className="eyebrow">How the score works</p>
      <h3>Balanced opportunity score</h3>
      <p>
        The 0–100 score blends executable liquidity, bid/ask quality, reward versus
        risk, breakeven distance, OI and volume, sensible strike spacing, and whether
        risk is clearly defined. Crossed quotes are rejected; weak liquidity,
        unrealistic spacing, proxy mismatch, and unbounded exposure reduce the rank.
        It is a comparison tool, not a probability of profit or an entry signal.
      </p>
    </aside>
  );
}

function PortfolioInputs({ values, setValues, onScan, loading }) {
  return (
    <form className="portfolio-inputs" onSubmit={(event) => { event.preventDefault(); onScan(); }}>
      {[
        ["units", "NIFTYBEES units", 1],
        ["average_cost", "Average cost", 0.01],
        ["current_price", "Current price", 0.01],
      ].map(([key, label, step]) => (
        <label key={key}>{label}
          <input type="number" min={step} step={step} value={values[key]} onChange={(event) => setValues({ ...values, [key]: Number(event.target.value) })} />
        </label>
      ))}
      <button className="button" disabled={loading}>Scan position</button>
      <p>Session-only values. NIFTYBEES and NIFTY options retain tracking, multiplier, basis and cash-settlement risk.</p>
    </form>
  );
}

function OtherStrategiesPanel({
  group,
  selectedStrategy,
  setSelectedStrategy,
  candidates,
  loading,
  error,
  selected,
  setSelected,
  spot,
  lotSize,
  portfolio,
  setPortfolio,
  scanPortfolio,
}) {
  const strategies = otherGroups[group];
  const active = strategies.find((item) => item.id === selectedStrategy) || strategies[0];
  return (
    <section>
      <div className="section-intro wide">
        <p className="eyebrow">{group === "others-1" ? "Income and asymmetric structures" : "Portfolio and directional structures"}</p>
        <h2>{group === "others-1" ? "Others 1" : "Others 2"}</h2>
        <p>Select a strategy to see when it is suitable before reviewing ranked candidates.</p>
      </div>
      <div className="strategy-selector" role="tablist" aria-label={`${group} strategies`}>
        {strategies.map((item) => (
          <button key={item.id} role="tab" aria-selected={item.id === selectedStrategy} className={item.id === selectedStrategy ? "active" : ""} onClick={() => setSelectedStrategy(item.id)}>
            {item.name}
          </button>
        ))}
      </div>
      <SuitabilityGuide title={active.name} guidance={active} />
      {["fence", "collar"].includes(selectedStrategy) && (
        <PortfolioInputs values={portfolio} setValues={setPortfolio} onScan={scanPortfolio} loading={loading} />
      )}
      <StrategyPanel title={`${active.name} Candidates`} description={`Top candidates ranked for the selected ${active.name} structure.`} candidates={candidates} loading={loading} error={error} selected={selected} setSelected={setSelected} spot={spot} lotSize={lotSize} showScore={false} />
      <ScoreExplanation />
    </section>
  );
}

function MarketContextEditor({ context, setContext }) {
  const updateList = (key, value) => setContext({
    ...context,
    [key]: value.split("\n").map((item) => item.trim()).filter(Boolean),
  });
  return (
    <section className="market-context">
      <div className="market-context-head">
        <div><p className="eyebrow">Grounded market context</p><h3>NIFTY trend, macro and events</h3></div>
        <span className={`context-status ${context?.stale ? "stale" : ""}`}>{context?.stale ? "Trend unavailable / stale" : "Trend data current"}</span>
      </div>
      <div className="trend-grid">
        <span>Short term <b>{context?.short_term_trend || "Unavailable"}</b></span>
        <span>Medium term <b>{context?.medium_term_trend || "Unavailable"}</b></span>
        <span>Momentum <b>{context?.momentum || "Unavailable"}</b></span>
        <span>Volatility <b>{context?.volatility_regime || "Unavailable"}</b></span>
      </div>
      <div className="context-inputs">
        <label>Verified global macro context
          <textarea rows="3" value={(context?.global_macro_context || []).join("\n")} onChange={(event) => updateList("global_macro_context", event.target.value)} placeholder="One verified item per line" />
        </label>
        <label>Expected upcoming events
          <textarea rows="3" value={(context?.upcoming_events || []).join("\n")} onChange={(event) => updateList("upcoming_events", event.target.value)} placeholder="One dated event per line" />
        </label>
        <label>Macro and event sources
          <textarea rows="3" value={(context?.sources || []).join("\n")} onChange={(event) => updateList("sources", event.target.value)} placeholder="One attributable source per line" />
        </label>
      </div>
      <p className="context-note">Only the timestamped trend metrics and text entered here are sent with the trade calculations. AI is instructed not to invent current events.</p>
    </section>
  );
}

function CoveredCallForm({ expiry, onResults, setLoading, setError }) {
  const [form, setForm] = useState({ units: 5000, average_cost: 250, current_price: 285 });
  const submit = async (event) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      onResults(await api.coveredCall({ expiry, ...form }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  return (
    <form className="covered-form panel" onSubmit={submit}>
      <div>
        <p className="eyebrow">Your ETF position</p>
        <h2>NIFTYBEES Covered-Call Proxy</h2>
        <p>Enter your position to compare its value with one cash-settled NIFTY option lot.</p>
      </div>
      {["units", "average_cost", "current_price"].map((field) => (
        <label key={field}>
          {field.replace("_", " ")}
          <input
            type="number"
            min="0.01"
            step={field === "units" ? "1" : "0.01"}
            value={form[field]}
            onChange={(event) => setForm({ ...form, [field]: Number(event.target.value) })}
          />
        </label>
      ))}
      <button className="button" type="submit">Find call overwrites</button>
      <p className="note">This is an exposure proxy, not a fully covered position. Tracking, basis and settlement risks remain.</p>
    </form>
  );
}

function ReportPanel({ selected, chain, expiry, farExpiry, report, setReport, marketContext, setMarketContext }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generate = async () => {
    if (!selected || !chain) return;
    setLoading(true);
    setError("");
    try {
      setReport(await api.report({
        candidate: selected,
        chain_timestamp: chain.timestamp,
        underlying_value: chain.underlying_value,
        report_date: today(),
        expiry,
        far_expiry: farExpiry,
        assumptions: [
          `NIFTY lot size ${chain.lot_size}`,
          "Quotes may be delayed or stale",
          "Educational analysis only",
        ],
        market_context: marketContext,
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };
  if (!selected) {
    return (
      <section>
        <SuitabilityGuide title="AI Trade Report" guidance={strategyGuidance.report} />
        <div className="empty report-empty">Select “Analyse” on a strategy candidate to prepare an AI trade report.</div>
        <ScoreExplanation />
      </section>
    );
  }
  return (
    <section>
      <SuitabilityGuide title="AI Trade Report" guidance={strategyGuidance.report} />
      <div className="report-layout">
        <aside className="panel report-source">
        <p className="eyebrow">Selected setup</p>
        <h2>{selected.strategy}</h2>
        {selected.legs.map((leg) => <p key={`${leg.action}-${leg.strike}`}>{leg.action} {leg.quantity > 1 ? `${leg.quantity}x ` : ""}{leg.option_type} {leg.strike} · {leg.expiry}</p>)}
        <button className="button" onClick={generate} disabled={loading}>
          {loading ? "Generating…" : "Generate AI report"}
        </button>
        {error && <div className="alert">{error}</div>}
        </aside>
        <article className="panel report">
        {!report ? <p className="muted">The report will use only this trade’s calculated metrics and chain timestamp.</p> : (
          <>
            <p className="eyebrow">AI-assisted risk review</p>
            <span className="source-badge">{report.generated_by === "gemini" ? "AI" : "Rules fallback"}</span>
            <h1>{report.title}</h1>
            {report.fallback_reason && <div className="alert warning">{report.fallback_reason}</div>}
            {report.desk_analysis && (
              <>
                <span className={`decision-badge ${report.desk_analysis.decision.toLowerCase()}`}>{report.desk_analysis.decision}</span>
                <p><b>Executive summary:</b> {report.desk_analysis.executive_summary}</p>
                <DeskAnalysisView analysis={report.desk_analysis} />
              </>
            )}
            {[ 
              ["Setup", report.setup],
              ["Rationale", report.rationale],
              ["Payoff", report.payoff],
              ["Short-term NIFTY trend", report.short_term_trend],
              ["Medium-term NIFTY trend", report.medium_term_trend],
              ["Momentum and volatility", report.momentum_and_volatility],
              ["Strategy suitability", report.strategy_suitability],
              ["Trade recommendation", report.trade_recommendation],
            ].map(([heading, text]) => <section key={heading}><h3>{heading}</h3><p>{text}</p></section>)}
            {[
              ["Breakevens", report.breakevens],
              ["Favorable scenarios", report.favorable_scenarios],
              ["Adverse scenarios", report.adverse_scenarios],
              ["Liquidity concerns", report.liquidity_concerns],
              ["Exit considerations", report.exit_considerations],
              ["Risks", report.risks],
              ["Assumptions", report.assumptions],
              ["Global macro context", report.global_macro_context],
              ["Upcoming events", report.upcoming_events],
              ["Entry conditions", report.entry_conditions],
              ["Adjustment conditions", report.adjustment_conditions],
              ["Position sizing cautions", report.position_sizing_cautions],
              ["Data timestamps", report.data_timestamps],
              ["Sources", report.sources],
            ].map(([heading, items]) => <section key={heading}><h3>{heading}</h3><ul>{items.map((item) => <li key={item}>{item}</li>)}</ul></section>)}
            <p><b>Confidence:</b> {report.confidence}</p>
            <div className="disclaimer">{report.disclaimer}</div>
          </>
        )}
        </article>
        <MarketContextEditor context={marketContext} setContext={setMarketContext} />
      </div>
      <ScoreExplanation />
    </section>
  );
}

export default function App() {
  const [activeTab, setActiveTab] = useState("chain");
  const [expiries, setExpiries] = useState([]);
  const [expiry, setExpiry] = useState("");
  const [farExpiry, setFarExpiry] = useState("");
  const [chain, setChain] = useState(null);
  const [strategyData, setStrategyData] = useState({ candidates: [] });
  const [selected, setSelected] = useState(null);
  const [report, setReport] = useState(null);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [strategyLoading, setStrategyLoading] = useState(false);
  const [error, setError] = useState("");
  const [strategyError, setStrategyError] = useState("");
  const [search, setSearch] = useState("");
  const [range, setRange] = useState("500");
  const [otherSelection, setOtherSelection] = useState({
    "others-1": "jade-lizard",
    "others-2": "fence",
  });
  const [portfolio, setPortfolio] = useState({ units: 5000, average_cost: 250, current_price: 285 });
  const [marketContext, setMarketContext] = useState({
    short_term_trend: "Unavailable",
    medium_term_trend: "Unavailable",
    momentum: "Unavailable",
    volatility_regime: "Unavailable",
    global_macro_context: [],
    upcoming_events: [],
    sources: [],
    stale: true,
  });

  const loadChain = async (selectedExpiry, refresh = false) => {
    if (!selectedExpiry) return;
    setLoading(true);
    setError("");
    try {
      setChain(await api.chain(selectedExpiry, refresh));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      try {
        const data = await api.expiries();
        setExpiries(data.expiries);
        setExpiry(data.expiries[0]);
        setFarExpiry(data.expiries[1] || "");
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    api.marketContext()
      .then(setMarketContext)
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadChain(expiry);
  }, [expiry]);

  useEffect(() => {
    const groupedStrategy = otherGroups[activeTab] ? otherSelection[activeTab] : null;
    const strategyId = groupedStrategy || activeTab;
    if ((!Object.keys(strategyCopy).includes(activeTab) && !groupedStrategy) || !expiry) return;
    if (["fence", "collar"].includes(strategyId)) {
      (async () => {
        setStrategyLoading(true);
        setStrategyError("");
        try {
          setStrategyData(await api.portfolioStrategy({
            expiry,
            strategy: strategyId,
            ...portfolio,
            limit: 10,
          }));
        } catch (err) {
          setStrategyError(err.message);
        } finally {
          setStrategyLoading(false);
        }
      })();
      return;
    }
    (async () => {
      setStrategyLoading(true);
      setStrategyError("");
      try {
        setStrategyData(await api.strategy(strategyId, expiry, farExpiry));
      } catch (err) {
        setStrategyError(err.message);
      } finally {
        setStrategyLoading(false);
      }
    })();
  }, [activeTab, expiry, farExpiry, otherSelection]);

  const setGroupedStrategy = (group, strategy) => {
    setOtherSelection((current) => ({ ...current, [group]: strategy }));
    setStrategyData({ candidates: [] });
    setStrategyError("");
  };

  const scanPortfolio = async () => {
    const strategy = otherSelection["others-2"];
    if (!["fence", "collar"].includes(strategy)) return;
    setStrategyLoading(true);
    setStrategyError("");
    try {
      setStrategyData(await api.portfolioStrategy({
        expiry,
        strategy,
        ...portfolio,
        limit: 10,
      }));
    } catch (err) {
      setStrategyError(err.message);
    } finally {
      setStrategyLoading(false);
    }
  };

  const selectCandidate = (candidate) => {
    setSelected(candidate);
    setReport(null);
    setAnalysisOpen(true);
  };
  const closeAnalysis = useCallback(() => setAnalysisOpen(false), []);

  return (
    <div className="app-shell">
      <header className="site-header">
        <a href="https://trading-simplified.com/" className="brand">
          <span className="brand-mark">TS</span>
          <span>Trading Simplified<small>NIFTY Strategy Desk</small></span>
        </a>
      </header>

      <main>
        <section className="hero">
          <div>
            <p className="eyebrow">NIFTY · Options · Defined risk</p>
            <h1>See the chain.<br /><em>Shape the trade.</em></h1>
            <p>Live option structure, ranked spreads and grounded trade analysis in one focused desk.</p>
          </div>
          <div className="hero-stat">
            <span>Underlying</span>
            <strong>{chain ? number(chain.underlying_value) : "—"}</strong>
            <small>{expiry || "Loading expiry…"}</small>
          </div>
        </section>

        <StatusBar chain={chain} loading={loading} onRefresh={() => loadChain(expiry, true)} />
        {error && <div className="alert">{error}</div>}
        {chain?.stale && <div className="alert warning">NSE is unavailable. Showing cached data; verify prices before making any decision.</div>}

        <section className="controls">
          <label>Near expiry
            <select value={expiry} onChange={(event) => setExpiry(event.target.value)}>
              {expiries.map((item) => <option key={item}>{item}</option>)}
            </select>
          </label>
          {(activeTab === "calendar" || activeTab === "diagonal" || (activeTab === "others-2" && otherSelection["others-2"] === "poor-mans-covered-call")) && (
            <label>Far expiry
              <select value={farExpiry} onChange={(event) => setFarExpiry(event.target.value)}>
                {expiries.filter((item) => item !== expiry).map((item) => <option key={item}>{item}</option>)}
              </select>
            </label>
          )}
        </section>

        <nav className="tabs" aria-label="Options tools">
          {tabs.map(([id, label]) => (
            <button key={id} className={activeTab === id ? "active" : ""} onClick={() => setActiveTab(id)}>
              {label}
              {id === "report" && selected && <span className="tab-dot" />}
            </button>
          ))}
        </nav>

        {activeTab === "chain" && chain && <ChainTable chain={chain} search={search} setSearch={setSearch} range={range} setRange={setRange} />}
        {activeTab === "ai-recommends" && <AIRecommendsPanel expiry={expiry} farExpiry={farExpiry} chain={chain} />}
        {Object.entries(strategyCopy).map(([id, [title, description]]) => activeTab === id && (
          <StrategyPanel key={id} title={title} description={description} guidance={strategyGuidance[id]} candidates={strategyData.candidates || []} loading={strategyLoading} error={strategyError} selected={selected} setSelected={selectCandidate} spot={chain?.underlying_value || 0} lotSize={chain?.lot_size || 1} />
        ))}
        {activeTab === "covered" && <>
          <SuitabilityGuide title="Covered Call Proxy" guidance={strategyGuidance.covered} />
          <CoveredCallForm expiry={expiry} onResults={setStrategyData} setLoading={setStrategyLoading} setError={setStrategyError} />
          <StrategyPanel title="Call Overwrite Candidates" description="Premium choices compared with your entered NIFTYBEES exposure." candidates={strategyData.candidates || []} loading={strategyLoading} error={strategyError} selected={selected} setSelected={selectCandidate} spot={chain?.underlying_value || 0} lotSize={chain?.lot_size || 1} />
        </>}
        {Object.keys(otherGroups).map((group) => activeTab === group && (
          <OtherStrategiesPanel
            key={group}
            group={group}
            selectedStrategy={otherSelection[group]}
            setSelectedStrategy={(strategy) => setGroupedStrategy(group, strategy)}
            candidates={strategyData.candidates || []}
            loading={strategyLoading}
            error={strategyError}
            selected={selected}
            setSelected={selectCandidate}
            spot={chain?.underlying_value || 0}
            lotSize={chain?.lot_size || 1}
            portfolio={portfolio}
            setPortfolio={setPortfolio}
            scanPortfolio={scanPortfolio}
          />
        ))}
        {activeTab === "report" && <ReportPanel selected={selected} chain={chain} expiry={expiry} farExpiry={farExpiry} report={report} setReport={setReport} marketContext={marketContext} setMarketContext={setMarketContext} />}
      </main>

      <AnalysisModal
        candidate={selected}
        chain={chain}
        expiry={expiry}
        farExpiry={farExpiry}
        open={analysisOpen}
        onClose={closeAnalysis}
        report={report}
        setReport={setReport}
        marketContext={marketContext}
        setMarketContext={setMarketContext}
      />

      <footer>
        <p><b>Educational use only.</b> This tool is not investment advice and does not provide execution-grade market data. The author is not SEBI registered.</p>
        <a href="https://trading-simplified.com/">Back to Trading Simplified</a>
      </footer>
    </div>
  );
}
