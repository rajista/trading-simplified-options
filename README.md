# Trading Simplified NIFTY Strategy Desk

Public NIFTY option-chain and strategy analysis app for
`options.trading-simplified.com`.

## Features

- NSE NIFTY option chain with expiry, strike search, range filtering and stale-data warnings
- AI Recommends tab that combines option-chain candidates, Yahoo Finance global
  market returns, RSS headlines and Gemini/rules analysis into five educational
  trade ideas
- Balanced ranking for debit, calendar and diagonal spreads
- NIFTYBEES covered-call exposure proxy using user-entered holdings
- Configurable NIFTY lot multiplier, defaulting to 65
- LTP-based strategy calculations with bid/ask still used for liquidity checks
- Batch 1 scanners for credit spreads, iron condors, butterflies, broken-wing
  butterflies, risk reversals, straddles and strangles
- Batch 2 grouped scanners for jade lizards, box spreads, seagulls, Christmas
  trees, guts, fences, collars, poor man's covered calls, strips and straps
- Suitability guidance and a transparent ranking-score explanation in both
  Batch 2 tabs
- Interactive payoff analysis with date, IV and price-range controls
- Timestamped deterministic NIFTY trend, momentum and realized-volatility
  context, with editable sourced macro/event notes
- Market-aware Gemini trade reports with deterministic rules-based fallback
- Responsive editorial dashboard matching Trading Simplified

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
$env:PYTHONPATH="backend"
uvicorn app.main:app --reload --app-dir backend
```

In another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Tests and production build

```powershell
$env:PYTHONPATH="backend"
pytest backend\tests
cd frontend
npm install
npm run build
```

The Docker image builds the React app and serves it from FastAPI.

## Render and DNS

1. Push this repository to GitHub and create a Render Blueprint from
   `render.yaml`.
2. Create a free Gemini API key in
   [Google AI Studio](https://aistudio.google.com/app/apikey), then set
   `GEMINI_API_KEY` as a Render secret.
3. Set `GEMINI_MODEL=gemini-2.5-flash-lite`. Save the environment variables
   and redeploy. The browser never receives the key.
4. Verify `/api/health` reports `"ai_reports_configured": true` and
   `"ai_provider": "gemini"`. Generate a report and confirm the source badge
   says **Gemini**. Missing keys, exhausted quota, API failures or invalid
   structured output use the **Rules fallback** report.
5. Review `NIFTY_LOT_SIZE` whenever NSE changes the contract specification.
6. In Render, add the custom domain `options.trading-simplified.com`.
7. Add Render's requested CNAME record in the domain's DNS settings.
8. Wait for Render to issue HTTPS and verify `/api/health`.

## Report context

`GET /api/market-context` calculates short- and medium-term NIFTY trend,
five-session momentum and 20-session realized volatility from timestamped
historical `^NSEI` closes. Global macro notes and upcoming events are not
invented by Gemini: users enter verified items and attributable sources in the
report context panel. If trend data is unavailable or stale, reports identify
that limitation and lower confidence.

## WordPress integration

In the WordPress.com editor, add this Custom HTML block to the homepage, or
add the same URL as a Navigation block item:

```html
<a class="wp-block-button__link wp-element-button"
   href="https://options.trading-simplified.com/">
  Open Options Strategy Finder
</a>
```

Label the navigation item **Options Strategy Finder**. Open it in the same
browser tab so mobile and keyboard navigation remain predictable.

## Market-data and risk notice

The NSE adapter is isolated behind `MarketDataProvider`, allowing replacement
with a licensed feed without frontend changes. Confirm NSE licensing and
redistribution terms before public production use. Calculations and AI reports
are educational, not investment advice or execution-grade market data.
