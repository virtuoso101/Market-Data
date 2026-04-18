"""
Daily EOD Stock Data Fetcher
Pulls end-of-day data from Yahoo Finance via yfinance and appends it to a Google Sheet.
Designed to run as a GitHub Actions cron job.

Asset list is managed in an 'Assets' tab (Ticker, Name).
- New tickers are automatically backfilled with 365 days of history.
- Removed tickers have their data purged from the Daily tab.
"""

import os
import json
import time
from datetime import datetime, timedelta

import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Google Sheets settings
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "EOD Market Data")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Daily")
ASSETS_WORKSHEET_NAME = os.environ.get("ASSETS_WORKSHEET_NAME", "Assets")

# How many days back to fetch for daily runs
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))

# How many days to backfill when a new ticker is added
BACKFILL_DAYS = int(os.environ.get("BACKFILL_DAYS", "365"))

DAILY_HEADERS = ["Date", "Ticker", "Name", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Currency"]
ASSETS_HEADERS = ["Ticker", "Name"]

DEFAULT_ASSETS = [
    ["BARC.L", "Barclays"],
    ["GOOG", "Alphabet"],
    ["7013.T", "IHI Corporation"],
    ["5803.T", "Fujikura"],
    ["ENR.DE", "Siemens Energy"],
    ["SEMU.L", "Amundi Semiconductors ETF"],
    ["DFNG.L", "VanEck Defense ETF"],
]


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


def get_or_create_worksheet(spreadsheet, worksheet_name, headers):
    """Open a worksheet by name, creating it with headers if needed."""
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=len(headers))

    existing = worksheet.row_values(1)
    if not existing or existing[0] != headers[0]:
        end_col = chr(ord("A") + len(headers) - 1)
        worksheet.update(f"A1:{end_col}1", [headers])
        worksheet.format(f"A1:{end_col}1", {"textFormat": {"bold": True}})

    return worksheet


# ---------------------------------------------------------------------------
# Asset List Management
# ---------------------------------------------------------------------------

def load_assets(spreadsheet):
    """
    Load the ticker list from the 'Assets' worksheet.
    Expected columns: Ticker, Name
    Returns a dict of {ticker: name} for all assets.
    """
    ws = get_or_create_worksheet(spreadsheet, ASSETS_WORKSHEET_NAME, ASSETS_HEADERS)
    all_values = ws.get_all_values()

    if len(all_values) <= 1:
        # Sheet is empty — seed with defaults
        ws.append_rows(DEFAULT_ASSETS, value_input_option="USER_ENTERED")
        print(f"  Seeded '{ASSETS_WORKSHEET_NAME}' tab with {len(DEFAULT_ASSETS)} default assets.")
        return {row[0]: row[1] for row in DEFAULT_ASSETS}

    assets = {}
    for row in all_values[1:]:
        if len(row) >= 2:
            ticker = row[0].strip()
            name = row[1].strip()
            if ticker:
                assets[ticker] = name

    print(f"  Loaded {len(assets)} asset(s) from '{ASSETS_WORKSHEET_NAME}' tab.")
    return assets


def get_existing_tickers(daily_ws):
    """Get the set of tickers that currently have data in the Daily tab."""
    all_values = daily_ws.get_all_values()
    tickers = set()
    for row in all_values[1:]:
        if len(row) >= 2:
            tickers.add(row[1])
    return tickers


def get_existing_keys(daily_ws):
    """Get set of (date, ticker) pairs already in the sheet to avoid duplicates."""
    all_values = daily_ws.get_all_values()
    keys = set()
    for row in all_values[1:]:
        if len(row) >= 2:
            keys.add((row[0], row[1]))
    return keys


# ---------------------------------------------------------------------------
# Sync: Remove Data for Deleted Tickers
# ---------------------------------------------------------------------------

def remove_tickers(daily_ws, tickers_to_remove):
    """Delete all rows from the Daily tab for tickers no longer in the Assets list.

    Rebuilds the sheet without the removed tickers in a single batch write
    to avoid hitting Google Sheets API rate limits.
    """
    if not tickers_to_remove:
        return

    all_values = daily_ws.get_all_values()
    if len(all_values) <= 1:
        return  # Only header, nothing to remove

    header = all_values[0]
    kept_rows = [row for row in all_values[1:] if len(row) >= 2 and row[1] not in tickers_to_remove]
    removed_count = len(all_values) - 1 - len(kept_rows)

    if removed_count == 0:
        return

    # Clear the entire sheet and rewrite with kept data
    daily_ws.clear()
    daily_ws.update("A1", [header] + kept_rows, value_input_option="USER_ENTERED")

    # Re-bold the header
    end_col = chr(ord("A") + len(header) - 1)
    daily_ws.format(f"A1:{end_col}1", {"textFormat": {"bold": True}})

    removed_str = ", ".join(sorted(tickers_to_remove))
    print(f"  🗑 Removed {removed_count} rows for deleted tickers: {removed_str}")


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_eod_data(assets, lookback_days=1):
    """Fetch EOD data for the given assets over the lookback period.

    Args:
        assets: Dict of {ticker: name}.
        lookback_days: Number of days of history to fetch.
    Returns:
        List of row lists ready to append to the Daily sheet.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days + 1)  # +1 buffer for weekends

    rows = []

    for ticker_symbol, asset_name in assets.items():
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(start=start_date.strftime("%Y-%m-%d"),
                                 end=end_date.strftime("%Y-%m-%d"))

            if hist.empty:
                print(f"  ⚠ No data returned for {ticker_symbol}")
                continue

            info = ticker.fast_info
            currency = getattr(info, "currency", "N/A")

            for date_idx, row in hist.iterrows():
                date_str = date_idx.strftime("%Y-%m-%d")
                rows.append([
                    date_str,
                    ticker_symbol,
                    asset_name,
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
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"EOD Data Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    # Connect to Google Sheets
    print("Connecting to Google Sheets...")
    client = get_gsheet_client()
    spreadsheet = client.open(SPREADSHEET_NAME)

    # Load assets from the Assets tab
    print("Loading asset list...")
    assets = load_assets(spreadsheet)

    if not assets:
        print("No assets found in the Assets tab. Add rows with Ticker and Name columns.")
        return

    print(f"  Assets: {', '.join(assets.keys())}\n")

    # Get or create the Daily data worksheet
    daily_ws = get_or_create_worksheet(spreadsheet, WORKSHEET_NAME, DAILY_HEADERS)

    # --- Sync step: detect additions and removals ---
    existing_tickers = get_existing_tickers(daily_ws)
    asset_tickers = set(assets.keys())

    new_tickers = asset_tickers - existing_tickers
    removed_tickers = existing_tickers - asset_tickers

    # 1. Remove data for deleted tickers
    if removed_tickers:
        print("Removing data for deleted tickers...")
        remove_tickers(daily_ws, removed_tickers)

    # 2. Determine what to fetch
    #    - New tickers: backfill BACKFILL_DAYS
    #    - Existing tickers: fetch LOOKBACK_DAYS
    existing_assets = {t: n for t, n in assets.items() if t not in new_tickers}
    new_assets = {t: n for t, n in assets.items() if t in new_tickers}

    all_new_rows = []

    if new_assets:
        new_str = ", ".join(new_assets.keys())
        print(f"New tickers detected: {new_str}")
        print(f"  Backfilling {BACKFILL_DAYS} days of history...")
        backfill_rows = fetch_eod_data(new_assets, lookback_days=BACKFILL_DAYS)
        all_new_rows.extend(backfill_rows)

    if existing_assets:
        print(f"Fetching latest data ({LOOKBACK_DAYS} day(s)) for existing tickers...")
        daily_rows = fetch_eod_data(existing_assets, lookback_days=LOOKBACK_DAYS)
        all_new_rows.extend(daily_rows)

    if not all_new_rows:
        print("\nNo new data fetched. Markets may be closed today.")
        return

    print(f"\nTotal rows fetched: {len(all_new_rows)}")

    # Deduplicate against existing data
    existing_keys = get_existing_keys(daily_ws)
    unique_rows = [r for r in all_new_rows if (r[0], r[1]) not in existing_keys]

    if not unique_rows:
        print("All data already exists in the sheet. Nothing to append.")
        return

    print(f"New rows to append: {len(unique_rows)}")

    # Append in batches (Sheets API allows 60 write requests/min)
    BATCH_SIZE = 1000
    total_batches = (len(unique_rows) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(0, len(unique_rows), BATCH_SIZE):
        batch = unique_rows[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        daily_ws.append_rows(batch, value_input_option="USER_ENTERED")
        print(f"  Appended batch {batch_num}/{total_batches} ({len(batch)} rows)")
        if batch_num < total_batches:
            time.sleep(5)  # Pause between batches to stay within rate limits

    print(f"\n✓ Successfully appended {len(unique_rows)} rows to '{SPREADSHEET_NAME}'")


if __name__ == "__main__":
    main()
