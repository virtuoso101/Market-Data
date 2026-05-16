"""
Technical Indicator Calculator

Reads EOD data from the 'Daily' tab of the Google Sheet, computes technical
indicators, and writes results to two tabs:
  - 'Indicators': 90 days of daily indicator values per ticker
  - 'Signals': Weekly historical signals (one row per ticker per week, 13 weeks)

Indicators calculated:
  - Candle patterns (body size, upper/lower wick, bullish/bearish)
  - Volume (current, 20-day avg, volume ratio)
  - Guppy Multiple Moving Averages (6 short EMAs + 6 long EMAs)
  - Stochastic Momentum Index (SMI)
  - True Range / ATR (14-period)
  - RSI (14-period)
  - On-Balance Volume (OBV)
  - Money Flow Index (MFI, 14-period)

Designed to run as a GitHub Actions step after fetch_eod_data.py.
"""

import os
import json
import math
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


def _sanitize(val):
    """Replace NaN/Inf float values with empty string for JSON safety."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return ""
    if isinstance(val, np.floating) and (np.isnan(val) or np.isinf(val)):
        return ""
    return val


def write_worksheet(spreadsheet, name, headers, rows):
    """Clear and rewrite a worksheet with headers + rows in one batch."""
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=max(len(rows) + 1, 100), cols=len(headers))

    ws.clear()

    # Sanitize all values to remove NaN/Inf before sending to the Sheets API
    safe_rows = [[_sanitize(cell) for cell in row] for row in rows]
    all_data = [headers] + safe_rows

    ws.update(all_data, "A1", value_input_option="USER_ENTERED")
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

    short_cols = [f"Guppy_S{p}" for p in short_periods]
    long_cols = [f"Guppy_L{p}" for p in long_periods]

    df["Guppy_Short_Avg"] = round(df[short_cols].mean(axis=1), 4)
    df["Guppy_Long_Avg"] = round(df[long_cols].mean(axis=1), 4)
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


def calc_obv(df):
    """On-Balance Volume — running total of volume, signed by price direction."""
    sign = np.where(df["Close"] > df["Close"].shift(1), 1,
                    np.where(df["Close"] < df["Close"].shift(1), -1, 0))
    obv = (df["Volume"] * sign).cumsum()
    df["OBV"] = obv.round(0)
    df["OBV_SMA20"] = obv.rolling(20).mean().round(0)
    return df


def calc_mfi(df, period=14):
    """Money Flow Index — volume-weighted RSI."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    raw_money_flow = typical_price * df["Volume"]

    positive_flow = raw_money_flow.where(typical_price > typical_price.shift(1), 0.0)
    negative_flow = raw_money_flow.where(typical_price < typical_price.shift(1), 0.0)

    positive_sum = positive_flow.rolling(period).sum()
    negative_sum = negative_flow.rolling(period).sum()

    money_ratio = positive_sum / negative_sum.replace(0, np.nan)
    mfi = 100 - (100 / (1 + money_ratio))
    df["MFI_14"] = round(mfi, 2)
    return df


def calculate_all_indicators(df):
    """Apply all indicator calculations to a single ticker's DataFrame."""
    df = calc_candle(df)
    df = calc_volume(df)
    df = calc_guppy(df)
    df = calc_smi(df)
    df = calc_true_range(df)
    df = calc_rsi(df)
    df = calc_obv(df)
    df = calc_mfi(df)
    return df

# ---------------------------------------------------------------------------
# Signal Generation
# ---------------------------------------------------------------------------

def _safe_float(val):
    """Return val if it's a valid finite number, otherwise None."""
    try:
        f = float(val)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def generate_signals(ticker, name, df, pos=-1):
    """Generate a summary signal row from indicator values at a given position."""
    if pos < 0:
        pos = len(df) + pos

    if pos < 1 or pos >= len(df):
        return None

    df_slice = df.iloc[:pos + 1]
    if len(df_slice) < 2:
        return None

    latest = df_slice.iloc[-1]
    prev = df_slice.iloc[-2]

    # --- RSI Signal ---
    rsi = _safe_float(latest.get("RSI_14"))
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
    smi = _safe_float(latest.get("SMI"))
    smi_sig = _safe_float(latest.get("SMI_Signal"))
    prev_smi = _safe_float(prev.get("SMI"))
    prev_smi_sig = _safe_float(prev.get("SMI_Signal"))

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
    short_avg = _safe_float(latest.get("Guppy_Short_Avg"))
    long_avg = _safe_float(latest.get("Guppy_Long_Avg"))
    short_spread = _safe_float(latest.get("Guppy_Short_Spread"))
    prev_short_spread = _safe_float(prev.get("Guppy_Short_Spread"))

    if all(v is not None for v in [short_avg, long_avg, short_spread, prev_short_spread]):
        trend = "Bullish" if short_avg > long_avg else "Bearish"
        momentum = "Expanding" if short_spread > prev_short_spread else "Compressing"
        guppy_signal = f"{trend}, {momentum}"
    else:
        guppy_signal = "N/A"

    # --- Volume Signal ---
    vol_ratio = _safe_float(latest.get("Vol_Ratio"))
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
    atr_pct = _safe_float(latest.get("ATR_Pct"))
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
    body_pct = _safe_float(latest.get("Candle_Body_Pct"))
    upper_wick = _safe_float(latest.get("Upper_Wick_Pct"))
    lower_wick = _safe_float(latest.get("Lower_Wick_Pct"))

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

    # --- OBV Signal ---
    if len(df_slice) >= 20:
        price_20d_change = df_slice["Close"].iloc[-1] - df_slice["Close"].iloc[-20]
        obv_20d_change = df_slice["OBV"].iloc[-1] - df_slice["OBV"].iloc[-20]
        if price_20d_change <= 0 and obv_20d_change > 0:
            obv_signal = "Bullish Divergence (Accumulation)"
        elif price_20d_change >= 0 and obv_20d_change < 0:
            obv_signal = "Bearish Divergence (Distribution)"
        elif obv_20d_change > 0:
            obv_signal = "Confirming Up"
        else:
            obv_signal = "Confirming Down"
    else:
        obv_signal = "N/A"

    # --- MFI Signal ---
    mfi = _safe_float(latest.get("MFI_14"))
    if mfi is not None:
        if mfi >= 80:
            mfi_signal = "Overbought"
        elif mfi <= 20:
            mfi_signal = "Oversold"
        elif rsi is not None and mfi > 60 and rsi < 50:
            mfi_signal = "Volume Accumulation (MFI/RSI divergence)"
        elif rsi is not None and mfi < 40 and rsi > 50:
            mfi_signal = "Volume Distribution (MFI/RSI divergence)"
        elif mfi >= 50:
            mfi_signal = "Bullish"
        else:
            mfi_signal = "Bearish"
    else:
        mfi_signal = "N/A"

    # --- Price context ---
    close = _safe_float(latest.get("Close")) or 0
    prev_close = _safe_float(prev.get("Close"))
    change_1d = round(((close / prev_close) - 1) * 100, 2) if prev_close else "N/A"

    change_5d = round(((close / _safe_float(df_slice.iloc[-5]["Close"])) - 1) * 100, 2) \
        if len(df_slice) >= 5 and _safe_float(df_slice.iloc[-5]["Close"]) else "N/A"

    change_20d = round(((close / _safe_float(df_slice.iloc[-20]["Close"])) - 1) * 100, 2) \
        if len(df_slice) >= 20 and _safe_float(df_slice.iloc[-20]["Close"]) else "N/A"

    volume_val = _safe_float(latest.get("Volume"))

    return [
        latest.name.strftime("%Y-%m-%d"),
        ticker,
        name,
        round(close, 4),
        change_1d,
        change_5d,
        change_20d,
        round(rsi, 2) if rsi is not None else "N/A",
        rsi_signal,
        round(smi, 2) if smi is not None else "N/A",
        round(smi_sig, 2) if smi_sig is not None else "N/A",
        smi_signal,
        guppy_signal,
        round(short_spread, 4) if short_spread is not None else "N/A",
        int(volume_val) if volume_val is not None else "N/A",
        round(vol_ratio, 2) if vol_ratio is not None else "N/A",
        vol_signal,
        round(_safe_float(latest.get("ATR_14")) or 0, 4),
        atr_pct if atr_pct is not None else "N/A",
        atr_signal,
        candle_signal,
        obv_signal,
        round(mfi, 2) if mfi is not None else "N/A",
        mfi_signal,
        latest.get("Currency", "N/A"),
    ]


def get_weekly_endpoints(df, num_weeks=13):
    """Find the last trading day of each week for the past num_weeks weeks."""
    if df.empty:
        return []

    df_with_pos = df.copy()
    df_with_pos["_pos"] = range(len(df_with_pos))
    df_with_pos["_year_week"] = (
        df_with_pos.index.isocalendar().year.astype(str) + "-" +
        df_with_pos.index.isocalendar().week.astype(str).str.zfill(2)
    )

    weekly = df_with_pos.groupby("_year_week")["_pos"].last()
    weekly = weekly.sort_values()
    return weekly.tail(num_weeks).tolist()


def generate_weekly_signals(ticker, name, df, num_weeks=13):
    """Generate signal rows for the last trading day of each of the past num_weeks weeks."""
    positions = get_weekly_endpoints(df, num_weeks)
    rows = []
    for pos in positions:
        row = generate_signals(ticker, name, df, pos=pos)
        if row:
            rows.append(row)
    return rows

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
    # OBV
    "OBV", "OBV_SMA20",
    # MFI
    "MFI_14",
]

SIGNALS_HEADERS = [
    "Date", "Ticker", "Name", "Close",
    "Change_1D_%", "Change_5D_%", "Change_20D_%",
    "RSI_14", "RSI_Signal",
    "SMI", "SMI_Signal_Line", "SMI_Signal",
    "Guppy_Signal", "Guppy_Short_Spread",
    "Volume", "Vol_Ratio", "Vol_Signal",
    "ATR_14", "ATR_Pct", "ATR_Signal",
    "Candle_Signal",
    "OBV_Signal",
    "MFI_14", "MFI_Signal",
    "Currency",
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
                    if isinstance(val, (float, np.floating)):
                        if pd.isna(val):
                            indicator_row.append("")
                        elif "Guppy" in col or col in ["True_Range", "ATR_14", "Candle_Body"]:
                            indicator_row.append(round(float(val), 4))
                        else:
                            indicator_row.append(round(float(val), 2))
                    else:
                        indicator_row.append(val)
                all_indicator_rows.append(indicator_row)

            # Generate weekly signal rows
            weekly_rows = generate_weekly_signals(ticker, name, df, num_weeks=13)
            all_signal_rows.extend(weekly_rows)

            print(f"  ✓ {ticker}: {len(recent)} indicator days, {len(weekly_rows)} weekly signals")

        except Exception as e:
            print(f"  ✗ {ticker}: Error - {e}")

    print(f"\nTotal indicator rows: {len(all_indicator_rows)}")
    print(f"Total signal rows: {len(all_signal_rows)} ({len(all_signal_rows) // max(len(ticker_dfs), 1)} weeks × {len(ticker_dfs)} tickers)")

    # Write Indicators tab
    print("\nWriting Indicators tab...")
    write_worksheet(spreadsheet, INDICATORS_WORKSHEET, INDICATOR_HEADERS, all_indicator_rows)
    print(f"  ✓ Written {len(all_indicator_rows)} rows")

    time.sleep(5)

    # Sort signal rows by date then ticker
    all_signal_rows.sort(key=lambda r: (r[0], r[1]))

    # Write Signals tab
    print("Writing Signals tab...")
    write_worksheet(spreadsheet, SIGNALS_WORKSHEET, SIGNALS_HEADERS, all_signal_rows)
    print(f"  ✓ Written {len(all_signal_rows)} rows")

    print(f"\n✓ Indicator calculation complete")


if __name__ == "__main__":
    main()
