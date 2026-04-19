# Market Data

Automated daily pipeline that fetches end-of-day stock and ETF prices from Yahoo Finance, calculates technical indicators, and writes everything to a Google Sheet. Runs on GitHub Actions — no server required.

## What it does

Each night at **10pm UTC**, a scheduled GitHub Actions workflow runs two scripts in sequence:

1. **`fetch_eod_data.py`** — pulls the latest closing prices for every ticker in the Google Sheet's Assets tab via `yfinance`, deduplicates, and appends new rows to the Daily tab.
2. **`calculate_indicators.py`** — reads the Daily tab, computes technical indicators across all tickers, and writes the results to two additional tabs:
   - **Indicators** — 90 days of daily values (Candle patterns, Volume analysis, Guppy EMAs, Stochastic Momentum Index, True Range/ATR, RSI)
   - **Signals** — weekly historical signals (one row per ticker per week, 13 weeks), with plain-language signals (e.g. "RSI: Overbought", "Guppy: Bullish, Expanding")

## Google Sheet structure

| Tab | Contents | Updated |
|-----|----------|---------|
| **Assets** | Editable watchlist (Ticker, Name) | By you |
| **Daily** | Raw OHLCV data, appended daily | Every run |
| **Indicators** | 90 days of daily indicator values per ticker | Every run (full rewrite) |
| **Signals** | Weekly signals per ticker (13 weeks of history) | Every run (full rewrite) |

## Managing the watchlist

Edit the **Assets** tab directly in Google Sheets. No code changes needed.

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
| `fetch_eod_data.py` | Fetches EOD price data from Yahoo Finance and writes to the Daily tab |
| `calculate_indicators.py` | Computes technical indicators and writes Indicators + Signals tabs |
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

## Using with LLMs

The Google Sheet is designed as a source of truth for AI-assisted analysis. Point your agent at the **Signals** tab for a quick weekly overview, or the **Indicators** tab when deeper analysis is needed for a specific ticker.

Works with Claude, ChatGPT, and Gemini via Google Sheets URL, CSV export, or native integrations.
