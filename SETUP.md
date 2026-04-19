# EOD Market Data — Setup Guide

This project automatically fetches daily end-of-day stock data from Yahoo Finance and appends it to a Google Sheet. It runs on GitHub Actions at 10pm UTC each day.

## Tickers Tracked

| Ticker   | Name                        | Exchange |
|----------|-----------------------------|----------|
| BARC.L   | Barclays                    | London   |
| GOOG     | Alphabet                    | NASDAQ   |
| 7013.T   | IHI Corporation             | Tokyo    |
| 5803.T   | Fujikura                    | Tokyo    |
| ENR.DE   | Siemens Energy              | Xetra    |
| SEMU.L   | Amundi Semiconductors ETF   | London   |
| DFNG.L   | VanEck Defense ETF          | London   |

---

## Step 1: Create a Google Cloud Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the **Google Sheets API** and **Google Drive API**:
   - Navigate to **APIs & Services → Library**
   - Search for and enable both APIs
4. Create a service account:
   - Go to **APIs & Services → Credentials**
   - Click **Create Credentials → Service Account**
   - Give it a name (e.g., `eod-data-bot`)
   - Click **Done**
5. Create a JSON key:
   - Click into the service account you just created
   - Go to the **Keys** tab
   - Click **Add Key → Create New Key → JSON**
   - Download the JSON file — you'll need the contents in Step 3

## Step 2: Create and Share the Google Sheets

1. Go to [Google Sheets](https://sheets.google.com) and create two spreadsheets:
   - **EOD Market Data** — for equities and ETFs
   - **EOD Indices Data** — for market context (indices, currencies, commodities)
2. Share both spreadsheets with the service account email:
   - The email looks like: `eod-data-bot@your-project.iam.gserviceaccount.com`
   - Give it **Editor** access
   - You can find this email in the JSON key file under `client_email`

## Step 2b: Create a Google Drive Folder for CSV Exports

1. In [Google Drive](https://drive.google.com), create a folder (e.g., **Market Data CSVs**)
2. Share the folder with the same service account email — give it **Editor** access
3. Copy the folder ID from the URL: `https://drive.google.com/drive/folders/THIS_IS_THE_ID`

Each run exports the sheet tabs as CSV files into this folder, overwriting the previous day's files. The CSV files are named like `eod_market_data_signals.csv`, `eod_indices_data_daily.csv`, etc.

## Step 3: Set Up the GitHub Repository

1. Create a new GitHub repository
2. Add these files to the root of the repo:
   - `fetch_eod_data.py`
   - `calculate_indicators.py`
   - `export_csv.py`
   - `requirements.txt`
   - `.github/workflows/fetch_eod_data.yml`
3. Add the following **repository secrets** (Settings → Secrets and variables → Actions):

   | Secret Name                | Value                                                        |
   |----------------------------|--------------------------------------------------------------|
   | `GOOGLE_CREDENTIALS`       | The **entire contents** of your service account JSON key file |
   | `SPREADSHEET_NAME`         | The name of your assets sheet (e.g., `EOD Market Data`)       |
   | `INDICES_SPREADSHEET_NAME` | The name of your indices sheet (e.g., `EOD Indices Data`)     |
   | `DRIVE_FOLDER_ID`          | The Google Drive folder ID for CSV exports                    |

## Step 4: Test It

1. Go to the **Actions** tab in your GitHub repository
2. Select the **Daily EOD Market Data** workflow
3. Click **Run workflow**
4. Optionally set `lookback_days` to something like `30` for an initial backfill
5. Check both Google Sheets — you should see data appearing in each

## Step 5: You're Done

The workflow will now run automatically at 10pm UTC every day. It:
- Fetches the latest EOD data for all tickers
- Deduplicates against existing rows (safe to re-run)
- Appends new data to the sheet

---

## Using the Data with LLMs

Your Google Sheet is now a persistent source of truth. To use it with Claude, ChatGPT, or Gemini:

- **Claude**: Share the Google Sheet URL in your conversation, or download as CSV and upload it
- **ChatGPT**: Use the Google Sheets integration, or upload a CSV export
- **Gemini**: Gemini has native Google Sheets access — just reference the sheet

For best results, prompt the LLM with context like:
> "Here is my portfolio's daily EOD data. The columns are: Date, Ticker, Name, Open, High, Low, Close, Adj Close, Volume, Currency."

---

## Managing Your Asset List

Your tickers are managed in the **Assets** tab of the same Google Sheet. No code changes needed.

The tab has two columns:

| Ticker   | Name                        |
|----------|-----------------------------|
| BARC.L   | Barclays                    |
| GOOG     | Alphabet                    |
| 7013.T   | IHI Corporation             |
| 5803.T   | Fujikura                    |

**To add a ticker**: Add a new row with the Yahoo Finance symbol and a display name. On the next run, the script will automatically backfill 365 days of history for the new ticker.

**To remove a ticker**: Delete the row. On the next run, the script will automatically remove all historical data for that ticker from the Daily tab.

The script compares the Assets tab against what's already in the Daily tab on every run, so changes take effect automatically.

> **First run note:** If the Assets tab doesn't exist yet, the script will create it and seed it with 7 default tickers.

---

## Customisation

**Change the schedule**: Edit the cron expression in `.github/workflows/fetch_eod_data.yml`. Use [crontab.guru](https://crontab.guru/) to build your schedule.

**Change backfill depth**: Set the `BACKFILL_DAYS` environment variable in the workflow (default is 365).

**Manual backfill**: Trigger the workflow manually and set `lookback_days` to override the default for all tickers (this applies to existing tickers too, not just new ones).
