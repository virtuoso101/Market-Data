"""
Daily EOD Stock Data Fetcher
Pulls end-of-day data from Yahoo Finance via yfinance and appends it to a Google Sheet.
Designed to run as a GitHub Actions cron job.
"""

import os
import json
from datetime import datetime, timedelta

import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TICKERS = [
    "BARC.L",   # Barclays (London)
    "GOOG",     # Alphabet (US)
    "7013.T",   # IHI Corporation (Tokyo)
    "5803.T",   # Fujikura (Tokyo)
    "ENR.DE",   # Siemens Energy (Xetra)
    "SEMU.L",   # Amundi Semiconductors ETF (London)
    "DFNG.L",   # VanEck Defense ETF (London)
]

TICKER_NAMES = {
    "BARC.L": "Barclays",
    "GOOG": "Alphabet",
    "7013.T": "IHI Corporation",
    "5803.T": "Fujikura",
    "ENR.DE": "Siemens Energy",
    "SEMU.L": "Amundi Semiconductors ETF",
    "DFNG.L": "VanEck Defense ETF",
}

# Google Sheets settings
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "EOD Market Data")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Daily")

# How many days back to fetch (for backfill on first run or catch-up)
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))

# ---------------------------------------------------------------------------
# Google Sheets Authentication
# ---------------------------------------------------------------------------

def get_gsheet_client():
    """Authenticate with Google Sheets using a service account."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise EnvironmentError(
            "GOOGLE_CREDENTIALS environment variable not set. "
            "It should contain the JSON key for your Google service account."
        )

    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)


def get_or_create_worksheet(client):
    """Open the spreadsheet and worksheet, creating headers if needed."""
    spreadsheet = client.open(SPREADSHEET_NAME)

    try:
        worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=WORKSHEET_NAME, rows=1000, cols=10)

    # Check if headers exist; if not, add them
    existing = worksheet.row_values(1)
    headers = ["Date", "Ticker", "Name", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Currency"]

    if not existing or existing[0] != "Date":
        worksheet.update("A1:J1", [headers])
        worksheet.format("A1:J1", {"textFormat": {"bold": True}})

    return worksheet


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_eod_data(lookback_days=1):
    """Fetch EOD data for all tickers for the given lookback period."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days + 1)  # +1 buffer for weekends

    rows = []

    for ticker_symbol in TICKERS:
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(start=start_date.strftime("%Y-%m-%d"),
                                 end=end_date.strftime("%Y-%m-%d"))

            if hist.empty:
                print(f"  ⚠ No data returned for {ticker_symbol}")
                continue

            # Get currency from ticker info
            info = ticker.fast_info
            currency = getattr(info, "currency", "N/A")

            for date_idx, row in hist.iterrows():
                date_str = date_idx.strftime("%Y-%m-%d")
                rows.append([
                    date_str,
                    ticker_symbol,
                    TICKER_NAMES.get(ticker_symbol, ticker_symbol),
                    round(row["Open"], 4),
                    round(row["High"], 4),
                    round(row["Low"], 4),
                    round(row["Close"], 4),
                    round(row.get("Close", row["Close"]), 4),  # Adj Close
                    int(row["Volume"]),
                    currency,
                ])

            print(f"  ✓ {ticker_symbol}: {len(hist)} day(s) fetched")

        except Exception as e:
            print(f"  ✗ {ticker_symbol}: Error - {e}")

    return rows


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def get_existing_keys(worksheet):
    """Get set of (date, ticker) pairs already in the sheet to avoid duplicates."""
    all_values = worksheet.get_all_values()
    keys = set()
    for row in all_values[1:]:  # Skip header
        if len(row) >= 2:
            keys.add((row[0], row[1]))
    return keys


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"EOD Data Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Tickers: {', '.join(TICKERS)}")
    print(f"Lookback: {LOOKBACK_DAYS} day(s)")
    print()

    # Fetch data from Yahoo Finance
    print("Fetching data from Yahoo Finance...")
    rows = fetch_eod_data(lookback_days=LOOKBACK_DAYS)

    if not rows:
        print("No data fetched. Markets may be closed today.")
        return

    print(f"\nTotal rows fetched: {len(rows)}")

    # Connect to Google Sheets
    print("\nConnecting to Google Sheets...")
    client = get_gsheet_client()
    worksheet = get_or_create_worksheet(client)

    # Deduplicate against existing data
    existing_keys = get_existing_keys(worksheet)
    new_rows = [r for r in rows if (r[0], r[1]) not in existing_keys]

    if not new_rows:
        print("All data already exists in the sheet. Nothing to append.")
        return

    print(f"New rows to append: {len(new_rows)}")

    # Append new rows
    worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    print(f"✓ Successfully appended {len(new_rows)} rows to '{SPREADSHEET_NAME}'")


if __name__ == "__main__":
    main()
