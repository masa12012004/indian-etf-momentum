"""
niftyohlc.py
------------
Download Nifty 50 daily (1D) OHLCV data from Yahoo Finance and save to CSV.

Usage:
    python niftyohlc.py                        # last 1 year (default)
    python niftyohlc.py --start 2020-01-01     # from date to today
    python niftyohlc.py --start 2020-01-01 --end 2023-12-31
    python niftyohlc.py --period 5y            # valid: 1d 5d 1mo 3mo 6mo 1y 2y 5y 10y ytd max
    python niftyohlc.py --output my_nifty.csv  # custom output filename

Dependencies:
    pip install yfinance pandas
"""

import argparse
import sys
from pathlib import Path
from datetime import date

try:
    import yfinance as yf
except ImportError:
    sys.exit("[ERROR] yfinance not installed. Run: pip install yfinance")

try:
    import pandas as pd
except ImportError:
    sys.exit("[ERROR] pandas not installed. Run: pip install pandas")


NIFTY_TICKER = "^NSEI"  # Yahoo Finance symbol for Nifty 50


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Nifty 50 daily OHLCV data to CSV via yfinance."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--period",
        type=str,
        default=None,
        metavar="10y",
        help="Lookback period (e.g. 1y, 2y, 5y, max). Mutually exclusive with --start/--end.",
    )
    group.add_argument(
        "--start",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Start date (inclusive). Defaults to 1 year ago if neither --period nor --start given.",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="End date (inclusive, default: today). Only used with --start.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="FILE",
        help="Output CSV filename (default: nifty50_1d_<start>_<end>.csv).",
    )
    return parser.parse_args()


def download_nifty(period=None, start=None, end=None):
    """Fetch OHLCV data from Yahoo Finance and return a cleaned DataFrame."""
    ticker = yf.Ticker(NIFTY_TICKER)

    if period:
        print(f"[INFO] Downloading Nifty 50 — period={period}, interval=1d")
        df = ticker.history(period=period, interval="1d", auto_adjust=True)
    else:
        start = start or str(date.today().replace(year=date.today().year - 1))
        end = end or str(date.today())
        print(f"[INFO] Downloading Nifty 50 — start={start}, end={end}, interval=1d")
        df = ticker.history(start=start, end=end, interval="1d", auto_adjust=True)

    if df.empty:
        sys.exit("[ERROR] No data returned. Check your date range or network connection.")

    # Keep only standard OHLCV columns (drop Dividends, Stock Splits if present)
    cols_to_keep = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in cols_to_keep if c in df.columns]]

    # Normalize index: strip timezone, keep date only
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "Date"

    # Round price columns to 2 decimal places
    price_cols = ["Open", "High", "Low", "Close"]
    df[price_cols] = df[price_cols].round(2)

    # Drop any rows where all OHLC values are NaN (occasional exchange holidays)
    df.dropna(subset=price_cols, how="all", inplace=True)

    return df


def build_filename(df):
    start_str = df.index.min().strftime("%Y%m%d")
    end_str = df.index.max().strftime("%Y%m%d")
    return f"nifty50_1d_{start_str}_{end_str}.csv"


def save_csv(df, filepath):
    df.to_csv(filepath, date_format="%Y-%m-%d")
    print(f"[INFO] Saved {len(df)} rows → {filepath}")


def main():
    args = parse_args()

    df = download_nifty(period=args.period, start=args.start, end=args.end)

    output_path = Path(args.output) if args.output else Path(build_filename(df))
    save_csv(df, output_path)

    # Quick summary
    print("\n── Summary ─────────────────────────────────")
    print(f"  Ticker   : {NIFTY_TICKER}")
    print(f"  Rows     : {len(df)}")
    print(f"  From     : {df.index.min().date()}")
    print(f"  To       : {df.index.max().date()}")
    print(f"  Close ↑  : {df['Close'].max():.2f}  ({df['Close'].idxmax().date()})")
    print(f"  Close ↓  : {df['Close'].min():.2f}  ({df['Close'].idxmin().date()})")
    print("────────────────────────────────────────────\n")
    print(df.tail())


if __name__ == "__main__":
    main()