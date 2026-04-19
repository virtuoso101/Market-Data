# Market Data

Automated daily pipeline that fetches end-of-day stock, ETF, and index prices from Yahoo Finance, calculates technical indicators, and writes everything to Google Sheets. Runs on GitHub Actions — no server required.

## What it does

Each night at **10pm UTC**, a scheduled GitHub Actions workflow runs these steps in sequence:

1. **`fetch_eod_data.py`** (assets) — pulls the latest closing prices for every ticker in the **EOD Market Data** sheet's Assets tab via `yfinance`, deduplicates, and appends new rows to its Daily tab.
2. **`calculate_indicators.py`** — reads the Daily tab, computes technical indicators across all asset tickers, and writes the results to two additional tabs:
   - **Indicators** — 90 days of daily values (Candle patterns, Volume analysis, Guppy EMAs, Stochastic Momentum Index, True Range/ATR, RSI, OBV, MFI)
   - **Signals** — weekly historical signals (one row per ticker per week, 13 weeks), with plain-language signals (e.g. "RSI: Overbought", "Guppy: Bullish, Expanding")
3. **`export_csv.py`** (assets) — exports all tabs (Assets, Daily, Indicators, Signals) as CSV files to a Google Drive folder for easy access by LLM agents.
4. **`fetch_eod_data.py`** (indices) — pulls the latest closing prices for every ticker in the **EOD Indices Data** sheet's Indices tab and appends new rows to its Daily tab.
5. **`export_csv.py`** (indices) — exports the Indices and Daily tabs as CSVs to the same Drive folder.

## Google Sheets

The pipeline writes to two separate Google Sheets:

### EOD Market Data

| Tab | Contents | Updated |
|-----|----------|---------|
| **Assets** | Equities and ETF watchlist (Ticker, Name) | By you |
| **Daily** | Raw OHLCV data, appended daily | Every run |
| **Indicators** | 90 days of daily indicator values per ticker | Every run (full rewrite) |
| **Signals** | Weekly signals per ticker (13 weeks of history) | Every run (full rewrite) |

### EOD Indices Data

| Tab | Contents | Updated |
|-----|----------|---------|
| **Indices** | Market context watchlist — indices, currencies, commodities (Ticker, Name) | By you |
| **Daily** | Raw OHLCV data, appended daily | Every run |

## Managing the watchlists

Edit the **Assets** tab (in EOD Market Data) or the **Indices** tab (in EOD Indices Data) directly in Google Sheets. Both use the same two-column format (Ticker, Name). No code changes needed.

- **Add a ticker**: insert a row with the Yahoo Finance symbol and a display name. The next run backfills 365 days of history automatically.
- **Remove a ticker**: delete the row. The next run purges its historical data from the Daily tab.

## Scheduled run

The workflow is triggered by a cron schedule defined in `.github/workflows/fetch_eod_data.yml`:

```
cron: "0 22 * * *"
```

This runs at 22:00 UTC every day, after US market close. You can change the schedule using [crontab.guru](https://crontab.guru/) to build the expression.

The workflow can also be triggered manually from the Actions tab, with an optional `lookback_days` input for backfilling.

### Important: 60-day inactivity limit

GitHub automatically disables scheduled workflows on repositories with no recent activity (no commits, pull requests, or visits) after approximately 60 days. If this happens, the cron job silently stops running. To re-enable it, go to the **Actions** tab in the repository and click the button to re-enable the workflow.

To avoid this, either star the repo periodically, push a minor commit, or simply check the Actions tab from time to time to confirm the workflow is still active.

## Setup

See [SETUP.md](SETUP.md) for step-by-step instructions covering Google Cloud service account creation, Google Sheet setup, GitHub secrets, and first run.

## Files

| File | Purpose |
|------|---------|
| `fetch_eod_data.py` | Fetches EOD price data from Yahoo Finance and writes to a Daily tab (used for both sheets) |
| `calculate_indicators.py` | Computes technical indicators and writes Indicators + Signals tabs (assets only) |
| `export_csv.py` | Exports Google Sheet tabs as CSV files to a Google Drive folder |
| `requirements.txt` | Python dependencies |
| `.github/workflows/fetch_eod_data.yml` | GitHub Actions workflow (daily schedule + manual trigger) |
| `SETUP.md` | First-time setup guide |

## Technical indicators

| Indicator | What's calculated |
|-----------|-------------------|
| **Candle** | Body size, upper/lower wick percentages, bullish/bearish/doji classification |
| **Volume** | 20-day SMA, volume ratio (current vs average) |
| **Guppy MMA** | 6 short EMAs (3,5,8,10,12,15), 6 long EMAs (30,35,40,45,50,60), group averages and spread |
| **Stochastic Momentum (SMI)** | SMI line and signal line, crossover detection |
| **True Range / ATR** | 14-period ATR, ATR as percentage of price |
| **RSI** | 14-period RSI |
| **OBV** | On-Balance Volume with 20-day SMA, bullish/bearish divergence detection for accumulation/distribution |
| **MFI** | 14-period Money Flow Index (volume-weighted RSI), MFI/RSI divergence for institutional activity |

## Using with LLMs

The Google Sheet is designed as a source of truth for AI-assisted analysis. Point your agent at the **Signals** tab for a quick weekly overview, or the **Indicators** tab when deeper analysis is needed for a specific ticker.

Works with Claude, ChatGPT, and Gemini via Google Sheets URL, CSV export, or native integrations.
