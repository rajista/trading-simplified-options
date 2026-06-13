# Trading Simplified NIFTY Strategy Desk

NIFTY option-chain, strategy scanner, payoff-analysis, and AI trade-report
application for:

`https://options.trading-simplified.com`

The React frontend and FastAPI backend are deployed together as one Render web
service.

## Main Features

- NIFTY option chain with expiry selection, LTP, OI, volume, IV, bid, and ask
- Debit and credit spreads, condors, butterflies, calendars, diagonals, and
  grouped complex strategies
- Interactive multi-leg payoff charts
- Five standard and two speculative AI recommendation slots
- Fast preview cards followed by concise AI commentary
- NIFTY indicators, option-chain structure, global markets, events, and
  contextual RSS headlines
- Detailed AI trade reports with deterministic rules-based fallback
- LTP-based calculations and configurable NIFTY lot size
- Responsive desktop and mobile interface

## Requirements

Install these before running the project:

- Python 3.12
- Node.js 20 or newer
- Git

Check the installations in Command Prompt:

```bat
python --version
node --version
npm --version
git --version
```

## First-Time Local Setup

Open **Command Prompt** and move into the project:

```bat
cd "C:\Users\rajis\OneDrive\Desktop\python-files\Codex-test"
```

Create the Python virtual environment:

```bat
python -m venv .venv
```

Install the backend packages:

```bat
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Install the frontend packages:

```bat
cd frontend
npm install
cd ..
```

You only need to complete this setup again when dependencies change or the
`.venv`/`node_modules` folders are removed.

## Configure Gemini Locally

The backend automatically reads the `.env` file from the project root.

If `.env` does not exist, create it from the example:

```bat
copy .env.example .env
```

Open `.env` in Notepad:

```bat
notepad .env
```

Set your private Gemini key:

```text
GEMINI_API_KEY=your_actual_key_here
GEMINI_ANALYSIS_MODE=quality
GEMINI_QUALITY_MODEL=gemini-3.5-flash
GEMINI_FAST_MODEL=gemini-2.5-flash-lite
GEMINI_TIMEOUT_SECONDS=180
```

Create a key at [Google AI Studio](https://aistudio.google.com/app/apikey).

Never commit or upload `.env`. It is already excluded by `.gitignore`.

## Run Locally - Recommended

This method builds React and serves the complete application from FastAPI on
port `8000`. Only one Command Prompt window is required.

From the project root, build the frontend:

```bat
cd "C:\Users\rajis\OneDrive\Desktop\python-files\Codex-test\frontend"
npm run build
```

Return to the project root and start the server:

```bat
cd "C:\Users\rajis\OneDrive\Desktop\python-files\Codex-test"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Open:

`http://127.0.0.1:8000`

Check backend health:

`http://127.0.0.1:8000/api/health`

Keep the Command Prompt window open while using the app. Press `Ctrl+C` to
stop it.

After changing React files, run `npm run build` again and restart FastAPI.

## Run in Development Mode

Use this method when actively changing React code. It provides automatic
frontend refresh.

Open the first Command Prompt for FastAPI:

```bat
cd "C:\Users\rajis\OneDrive\Desktop\python-files\Codex-test"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000
```

Open a second Command Prompt for React:

```bat
cd "C:\Users\rajis\OneDrive\Desktop\python-files\Codex-test\frontend"
npm run dev
```

Open:

`http://localhost:5173`

Both Command Prompt windows must remain open.

## Common Local Problems

### `No module named uvicorn`

Use the virtual-environment Python instead of the system Python:

```bat
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

### Port 8000 is already in use

Find the process:

```bat
netstat -ano | findstr :8000
```

Stop only the displayed process ID:

```bat
taskkill /PID PROCESS_ID /F
```

Replace `PROCESS_ID` with the number shown by `netstat`, then start the app
again.

### AI uses the rules fallback

1. Confirm `GEMINI_API_KEY` is present in the root `.env`.
2. Restart FastAPI after editing `.env`.
3. Open `/api/health` and confirm:

```json
{
  "ai_reports_configured": true,
  "ai_provider": "gemini"
}
```

Gemini quota, timeout, or structured-output failures automatically use the
rules-based fallback.

## Run Tests

From the project root:

```bat
set PYTHONPATH=backend
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Build the production frontend:

```bat
cd frontend
npm run build
```

## Upload to GitHub

The configured repository is:

`https://github.com/rajista/trading-simplified-options`

From the project root:

```bat
git status
git add .env.example README.md render.yaml Dockerfile backend frontend
git commit -m "Update NIFTY strategy application"
git push origin main
```

Do not add `.env` to Git.

## Deploy to Render

1. Sign in to [Render](https://dashboard.render.com/).
2. Select **New**, then **Blueprint**.
3. Connect GitHub and select `rajista/trading-simplified-options`.
4. Render reads `render.yaml` and creates `trading-simplified-options`.
5. Enter `GEMINI_API_KEY` when Render requests the secret.
6. Apply the Blueprint and wait for deployment to finish.
7. Open the generated `onrender.com` URL.
8. Verify `/api/health`.

The Docker image builds React and serves it through FastAPI. Do not create
separate frontend and backend services.

Important Render environment variables:

```text
GEMINI_API_KEY=your_actual_key
GEMINI_ANALYSIS_MODE=quality
GEMINI_QUALITY_MODEL=gemini-3.5-flash
GEMINI_FAST_MODEL=gemini-2.5-flash-lite
GEMINI_TIMEOUT_SECONDS=180
NIFTY_LOT_SIZE=65
CORS_ORIGINS=https://options.trading-simplified.com
```

Optionally set `MARKET_EVENTS_JSON` to a one-line JSON array of verified
scheduled events:

```json
[{"id":"rbi-mpc-2026-08","date":"2026-08-07","title":"RBI MPC decision","importance":"high","source":"RBI published schedule","source_url":"https://www.rbi.org.in/","verified":true}]
```

## Connect the Custom Domain

In the Render service:

1. Open **Settings**.
2. Find **Custom Domains**.
3. Add `options.trading-simplified.com`.
4. Copy the DNS target supplied by Render.

In WordPress.com DNS management, add:

```text
Type: CNAME
Name: options
Points to: trading-simplified-options.onrender.com
TTL: 3600
```

Use the exact Render target displayed in your service. Do not include
`https://` or a trailing `/`.

Return to Render, verify the domain, and wait for the HTTPS certificate. Then
open:

`https://options.trading-simplified.com`

## Add the App to the WordPress Menu

For a WordPress block theme:

1. Open **Appearance > Editor**.
2. Open the site header.
3. Select the **Navigation** block.
4. Click `+` and choose **Custom Link**.
5. Use:

```text
Label: Options Strategy Finder
URL: https://options.trading-simplified.com
```

6. Save the header.

For a classic theme:

1. Open **Appearance > Customize > Menus**.
2. Select the primary menu.
3. Choose **Add Items > Custom Links**.
4. Enter the same label and URL.
5. Publish the changes.

Optional homepage button:

```html
<a class="wp-block-button__link wp-element-button"
   href="https://options.trading-simplified.com/">
  Open Options Strategy Finder
</a>
```

## Production Checklist

- GitHub contains the latest code but not `.env`.
- Render deployment is successful.
- `/api/health` reports Gemini as configured.
- The option chain and strategy tabs load.
- AI cards appear before commentary is generated.
- `https://options.trading-simplified.com` has valid HTTPS.
- The WordPress menu opens the application.
- Desktop and mobile layouts work correctly.

## Market-Data and Risk Notice

The app uses best-effort public market inputs that may be delayed or
unavailable. Confirm NSE licensing and redistribution requirements before a
public production launch.

All calculations, scanners, AI recommendations, and reports are educational.
They are not personalized investment advice or execution-grade market data.
