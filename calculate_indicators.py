"""
Technical Indicator Calculator
Reads EOD data from the 'Daily' tab of the Google Sheet, computes technical
indicators, and writes results to two tabs:
  - 'Indicators': 90 days of daily indicator values per ticker
  - 'Signals':    Latest snapshot with derived buy/sell signals per ticker

Indicators calculated:
  - Candle patterns (body size, upper/lower wick, bullish/bearish)
  - Volume (current, 20-day avg, volume ratio)
  - Guppy Multiple Moving Averages (6 short EMAs + 6 long EMAs)
  - Stochastic Momentum Index (SMI)
  - True Range / ATR (14-period)
  - RSI (14-period)

Designed to run as a GitHub Actions step after fetch_eod_data.py.
"""

import os
import json
import time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "EOD Market Data")
DAILY_WORKSHEET = os.environ.get("WORKSHEET_NAME", "Daily")
INDICATORS_WORKSHEET = "Indicators"
SIGNALS_WORKSHEET = "Signals"

# How many days of indicator history to keep in the Indicators tab
INDICATOR_DAYS = int(os.environ.get("INDICATOR_DAYS", "90"))

# We need extra historical data for EMAs to stabilise (longest Guppy EMA = 60)
WARMUP_DAYS = 80


# ---------------------------------------------------------------------------
# Google Sheets Authentication (same as fetch_eod_data.py)
# ---------------------------------------------------------------------------

def get_gsheet_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS environment variable not set.")
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(credentials)


def write_worksheet(spreadsheet, name, headers, rows):
    """Clear and rewrite a worksheet with headers + rows in one batch."""
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=max(len(rows) + 1, 100), cols=len(headers))

    ws.clear()
    all_data = [headers] + rows
    ws.update("A1", all_data, value_input_option="USER_ENTERED")
    ws.format(f"A1:{chr(ord('A') + len(headers) - 1)}1", {"textFormat": {"bold": True}})
    return ws


# ---------------------------------------------------------------------------
# Load Daily Data into DataFrames
# ---------------------------------------------------------------------------

def load_daily_data(spreadsheet):
    """Read the Daily tab and return a dict of {ticker: DataFrame}."""
    ws = spreadsheet.worksheet(DAILY_WORKSHEET)
    all_values = ws.get_all_values()

    if len(all_values) <= 1:
        return {}

    headers = all_values[0]
    rows = all_values[1:]

    df = pd.DataFrame(rows, columns=headers)

    # Convert numeric columns
    for col in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"])
    df = df.sort_values(["Ticker", "Date"])

    # Split into per-ticker DataFrames
    ticker_dfs = {}
    for ticker, group in df.groupby("Ticker"):
        group = group.set_index("Date").sort_index()
        ticker_dfs[ticker] = group

    return ticker_dfs


# ---------------------------------------------------------------------------
# Indicator Calculations
# ---------------------------------------------------------------------------

def calc_candle(df):
    """Candle pattern metrics."""
    body = df["Close"] - df["Open"]
    body_size = body.abs()
    full_range = df["High"] - df["Low"]
    upper_wick = df["High"] - df[["Open", "Close"]].max(axis=1)
    lower_wick = df[["Open", "Close"]].min(axis=1) - df["Low"]

    df["Candle_Body"] = round(body, 4)
    df["Candle_Body_Pct"] = round((body_size / full_range.replace(0, np.nan)) * 100, 2)
    df["Upper_Wick_Pct"] = round((upper_wick / full_range.replace(0, np.nan)) * 100, 2)
    df["Lower_Wick_Pct"] = round((lower_wick / full_range.replace(0, np.nan)) * 100, 2)
    df["Candle_Type"] = np.where(body > 0, "Bullish", np.where(body < 0, "Bearish", "Doji"))
    return df


def calc_volume(df):
    """Volume analysis: current, 20-day SMA, and ratio."""
    df["Vol_SMA20"] = df["Volume"].rolling(20).mean().round(0)
    df["Vol_Ratio"] = round(df["Volume"] / df["Vol_SMA20"].replace(0, np.nan), 2)
    return df


def calc_guppy(df):
    """Guppy Multiple Moving Averages: 6 short EMAs + 6 long EMAs."""
    short_periods = [3, 5, 8, 10, 12, 15]
    long_periods = [30, 35, 40, 45, 50, 60]

    for p in short_periods:
        df[f"Guppy_S{p}"] = round(df["Close"].ewm(span=p, adjust=False).mean(), 4)
    for p in long_periods:
        df[f"Guppy_L{p}"] = round(df["Close"].ewm(span=p, adjust=False).mean(), 4)

    # Short group average and long group average
    short_cols = [f"Guppy_S{p}" for p in short_periods]
    long_cols = [f"Guppy_L{p}" for p in long_periods]
    df["Guppy_Short_Avg"] = round(df[short_cols].mean(axis=1), 4)
    df["Guppy_Long_Avg"] = round(df[long_cols].mean(axis=1), 4)

    # Spread metrics
    df["Guppy_Short_Spread"] = round(df[short_cols].max(axis=1) - df[short_cols].min(axis=1), 4)
    df["Guppy_Long_Spread"] = round(df[long_cols].max(axis=1) - df[long_cols].min(axis=1), 4)

    return df


def calc_smi(df, k_length=14, d_length=3, smooth=3):
    """Stochastic Momentum Index."""
    highest_high = df["High"].rolling(k_length).max()
    lowest_low = df["Low"].rolling(k_length).min()
    midpoint = (highest_high + lowest_low) / 2
    diff = df["Close"] - midpoint
    range_hl = highest_high - lowest_low

    # Double smooth
    diff_smooth = diff.ewm(span=smooth, adjust=False).mean().ewm(span=smooth, adjust=False).mean()
    range_smooth = range_hl.ewm(span=smooth, adjust=False).mean().ewm(span=smooth, adjust=False).mean()

    smi = (diff_smooth / (range_smooth / 2).replace(0, np.nan)) * 100
    smi_signal = smi.ewm(span=d_length, adjust=False).mean()

    df["SMI"] = round(smi, 2)
    df["SMI_Signal"] = round(smi_signal, 2)
    return df


def calc_true_range(df, period=14):
    """True Range and Average True Range."""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift(1)).abs()
    low_close = (df["Low"] - df["Close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    df["True_Range"] = round(tr, 4)
    df["ATR_14"] = round(tr.rolling(period).mean(), 4)
    df["ATR_Pct"] = round((df["ATR_14"] / df["Close"]) * 100, 2)
    return df


def calc_rsi(df, period=14):
    """Relative Strength Index."""
    delta = df["Close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    df["RSI_14"] = round(rsi, 2)
    return df


def calculate_all_indicators(df):
    """Apply all indicator calculations to a single ticker's DataFrame."""
    df = calc_candle(df)
    df = calc_volume(df)
    df = calc_guppy(df)
    df = calc_smi(df)
    df = calc_true_range(df)
    df = calc_rsi(df)
    return df


# ---------------------------------------------------------------------------
# Signal Generation
# ---------------------------------------------------------------------------

def generate_signals(ticker, name, df):
    """Generate a summary signal row from the latest indicator values."""
    if len(df) < 2:
        return None

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # --- RSI Signal ---
    rsi = latest.get("RSI_14", None)
    if rsi is not None:
        if rsi >= 70:
            rsi_signal = "Overbought"
        elif rsi <= 30:
            rsi_signal = "Oversold"
        elif rsi >= 60:
            rsi_signal = "Bullish"
        elif rsi <= 40:
            rsi_signal = "Bearish"
        else:
            rsi_signal = "Neutral"
    else:
        rsi_signal = "N/A"

    # --- SMI Signal ---
    smi = latest.get("SMI", None)
    smi_sig = latest.get("SMI_Signal", None)
    prev_smi = prev.get("SMI", None)
    prev_smi_sig = prev.get("SMI_Signal", None)

    if all(v is not None for v in [smi, smi_sig, prev_smi, prev_smi_sig]):
        if prev_smi <= prev_smi_sig and smi > smi_sig:
            smi_signal = "Bullish Crossover"
        elif prev_smi >= prev_smi_sig and smi < smi_sig:
            smi_signal = "Bearish Crossover"
        elif smi > 40:
            smi_signal = "Overbought"
        elif smi < -40:
            smi_signal = "Oversold"
        elif smi > smi_sig:
            smi_signal = "Bullish"
        else:
            smi_signal = "Bearish"
    else:
        smi_signal = "N/A"

    # --- Guppy Signal ---
    short_avg = latest.get("Guppy_Short_Avg", None)
    long_avg = latest.get("Guppy_Long_Avg", None)
    short_spread = latest.get("Guppy_Short_Spread", None)
    prev_short_spread = prev.get("Guppy_Short_Spread", None)

    if all(v is not None for v in [short_avg, long_avg, short_spread, prev_short_spread]):
        if short_avg > long_avg:
            trend = "Bullish"
        else:
            trend = "Bearish"

        if short_spread > prev_short_spread:
            momentum = "Expanding"
        else:
            momentum = "Compressing"

        guppy_signal = f"{trend}, {momentum}"
    else:
        guppy_signal = "N/A"

    # --- Volume Signal ---
    vol_ratio = latest.get("Vol_Ratio", None)
    if vol_ratio is not None:
        if vol_ratio >= 2.0:
            vol_signal = "Very High"
        elif vol_ratio >= 1.5:
            vol_signal = "High"
        elif vol_ratio >= 0.8:
            vol_signal = "Normal"
        else:
            vol_signal = "Low"
    else:
        vol_signal = "N/A"

    # --- ATR / Volatility Signal ---
    atr_pct = latest.get("ATR_Pct", None)
    if atr_pct is not None:
        if atr_pct >= 5:
            atr_signal = "Very High"
        elif atr_pct >= 3:
            atr_signal = "High"
        elif atr_pct >= 1.5:
            atr_signal = "Moderate"
        else:
            atr_signal = "Low"
    else:
        atr_signal = "N/A"

    # --- Candle Signal ---
    candle_type = latest.get("Candle_Type", "N/A")
    body_pct = latest.get("Candle_Body_Pct", None)
    upper_wick = latest.get("Upper_Wick_Pct", None)
    lower_wick = latest.get("Lower_Wick_Pct", None)

    if all(v is not None for v in [body_pct, upper_wick, lower_wick]):
        if body_pct < 10:
            candle_signal = "Doji"
        elif lower_wick > 60:
            candle_signal = "Hammer/Pin Bar"
        elif upper_wick > 60:
            candle_signal = "Shooting Star"
        elif body_pct > 70:
            candle_signal = f"Strong {candle_type}"
        else:
            candle_signal = candle_type
    else:
        candle_signal = "N/A"

    # --- Price context ---
    close = latest.get("Close", 0)
    change_1d = round(((close / prev["Close"]) - 1) * 100, 2) if prev["Close"] else 0

    # 5-day and 20-day returns
    if len(df) >= 5:
        change_5d = round(((close / df.iloc[-5]["Close"]) - 1) * 100, 2)
    else:
        change_5d = "N/A"
    if len(df) >= 20:
        change_20d = round(((close / df.iloc[-20]["Close"]) - 1) * 100, 2)
    else:
        change_20d = "N/A"

    return [
        latest.name.strftime("%Y-%m-%d"),  # Date
        ticker,
        name,
        round(close, 4),
        change_1d,
        change_5d,
        change_20d,
        # RSI
        round(rsi, 2) if rsi is not None else "N/A",
        rsi_signal,
        # SMI
        round(smi, 2) if smi is not None else "N/A",
        round(smi_sig, 2) if smi_sig is not None else "N/A",
        smi_signal,
        # Guppy
        guppy_signal,
        round(short_spread, 4) if short_spread is not None else "N/A",
        # Volume
        int(latest["Volume"]) if not pd.isna(latest["Volume"]) else "N/A",
        round(vol_ratio, 2) if vol_ratio is not None else "N/A",
        vol_signal,
        # ATR
        round(latest.get("ATR_14", 0), 4),
        atr_pct if atr_pct is not None else "N/A",
        atr_signal,
        # Candle
        candle_signal,
        latest.get("Currency", "N/A"),
    ]


# ---------------------------------------------------------------------------
# Indicator Tab Column Definitions
# ---------------------------------------------------------------------------

INDICATOR_HEADERS = [
    "Date", "Ticker", "Name",
    "Open", "High", "Low", "Close", "Volume",
    # Candle
    "Candle_Body", "Candle_Body_Pct", "Upper_Wick_Pct", "Lower_Wick_Pct", "Candle_Type",
    # Volume
    "Vol_SMA20", "Vol_Ratio",
    # Guppy Short EMAs
    "Guppy_S3", "Guppy_S5", "Guppy_S8", "Guppy_S10", "Guppy_S12", "Guppy_S15",
    # Guppy Long EMAs
    "Guppy_L30", "Guppy_L35", "Guppy_L40", "Guppy_L45", "Guppy_L50", "Guppy_L60",
    # Guppy Summary
    "Guppy_Short_Avg", "Guppy_Long_Avg", "Guppy_Short_Spread", "Guppy_Long_Spread",
    # SMI
    "SMI", "SMI_Signal",
    # True Range
    "True_Range", "ATR_14", "ATR_Pct",
    # RSI
    "RSI_14",
]

SIGNALS_HEADERS = [
    "Date", "Ticker", "Name", "Close",
    "Change_1D_%", "Change_5D_%", "Change_20D_%",
    "RSI_14", "RSI_Signal",
    "SMI", "SMI_Signal_Line", "SMI_Signal",
    "Guppy_Signal", "Guppy_Short_Spread",
    "Volume", "Vol_Ratio", "Vol_Signal",
    "ATR_14", "ATR_Pct", "ATR_Signal",
    "Candle_Signal", "Currency",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Indicator Calculator - {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print()

    print("Connecting to Google Sheets...")
    client = get_gsheet_client()
    spreadsheet = client.open(SPREADSHEET_NAME)

    # Load all daily data
    print("Loading daily data...")
    ticker_dfs = load_daily_data(spreadsheet)

    if not ticker_dfs:
        print("No daily data found. Run fetch_eod_data.py first.")
        return

    print(f"  Loaded data for {len(ticker_dfs)} tickers\n")

    # Load asset names
    try:
        assets_ws = spreadsheet.worksheet("Assets")
        assets_values = assets_ws.get_all_values()
        asset_names = {}
        for row in assets_values[1:]:
            if len(row) >= 2:
                asset_names[row[0].strip()] = row[1].strip()
    except Exception:
        asset_names = {}

    # Calculate indicators for each ticker
    print("Calculating indicators...")
    cutoff_date = datetime.now() - timedelta(days=INDICATOR_DAYS)

    all_indicator_rows = []
    all_signal_rows = []

    for ticker, df in sorted(ticker_dfs.items()):
        try:
            name = asset_names.get(ticker, ticker)
            df = calculate_all_indicators(df)

            # Trim to last INDICATOR_DAYS for the Indicators tab
            recent = df[df.index >= pd.Timestamp(cutoff_date)]

            for date_idx, row in recent.iterrows():
                indicator_row = [date_idx.strftime("%Y-%m-%d"), ticker, name]
                for col in INDICATOR_HEADERS[3:]:
                    val = row.get(col, "")
                    if isinstance(val, float):
                        if pd.isna(val):
                            indicator_row.append("")
                        else:
                            indicator_row.append(round(val, 4) if "Guppy" in col or col in ["True_Range", "ATR_14", "Candle_Body"] else round(val, 2))
                    else:
                        indicator_row.append(val if not (isinstance(val, float) and pd.isna(val)) else "")
                all_indicator_rows.append(indicator_row)

            # Generate signal summary
            signal_row = generate_signals(ticker, name, df)
            if signal_row:
                all_signal_rows.append(signal_row)

            print(f"  ✓ {ticker}: {len(recent)} indicator days")

        except Exception as e:
            print(f"  ✗ {ticker}: Error - {e}")

    print(f"\nTotal indicator rows: {len(all_indicator_rows)}")
    print(f"Total signal rows: {len(all_signal_rows)}")

    # Write Indicators tab
    print("\nWriting Indicators tab...")
    write_worksheet(spreadsheet, INDICATORS_WORKSHEET, INDICATOR_HEADERS, all_indicator_rows)
    print(f"  ✓ Written {len(all_indicator_rows)} rows")

    time.sleep(5)  # Rate limit pause

    # Write Signals tab
    print("Writing Signals tab...")
    write_worksheet(spreadsheet, SIGNALS_WORKSHEET, SIGNALS_HEADERS, all_signal_rows)
    print(f"  ✓ Written {len(all_signal_rows)} rows")

    print(f"\n✓ Indicator calculation complete")


if __name__ == "__main__":
    main()
