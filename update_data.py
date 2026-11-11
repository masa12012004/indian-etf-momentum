import yfinance as yf
import pandas as pd
import os

# Config Paths
MW_ETF_PATH = "MW-ETF-22-May-2026-NoSilver.csv"
GOLD_ETF_PATH = "gold_etf.csv"
ETF_OUTPUT_PATH = "ETF_OHLCV.xlsx"
NIFTY_OUTPUT_PATH = "nifty50_ohlcv.csv"

def extract_symbols():
    symbols = set()
    if os.path.exists(MW_ETF_PATH):
        try:
            df = pd.read_csv(MW_ETF_PATH)
            if "SYMBOL" in df.columns: symbols.update(df["SYMBOL"].dropna().astype(str).str.strip().tolist())
        except Exception as e: print(f"[WARNING] Error reading MW-ETF: {e}")

    if os.path.exists(GOLD_ETF_PATH):
        try:
            df = pd.read_csv(GOLD_ETF_PATH)
            if "SYMBOL" in df.columns: symbols.update(df["SYMBOL"].dropna().astype(str).str.strip().tolist())
        except Exception as e: print(f"[WARNING] Error reading Gold ETF: {e}")
            
    return list(symbols)

def fetch_data():
    print("============================================================")
    print(" DAILY MARKET DATA UPDATER (Yahoo Finance)")
    print("============================================================")
    
    # 1. Fetch Nifty 50
    print("[INFO] Fetching Nifty 50 (^NSEI)...")
    nifty = yf.Ticker("^NSEI").history(period="max", interval="1d", auto_adjust=True)
    if not nifty.empty:
        nifty = nifty[["Open", "High", "Low", "Close", "Volume"]]
        nifty.index = pd.to_datetime(nifty.index).tz_localize(None).normalize()
        nifty.index.name = "Date"
        nifty = nifty.reset_index()
        nifty[["Open", "High", "Low", "Close"]] = nifty[["Open", "High", "Low", "Close"]].round(2)
        nifty.dropna(subset=["Close"], inplace=True)
        nifty.to_csv(NIFTY_OUTPUT_PATH, index=False)
        print(f"[SUCCESS] Nifty 50 saved to {NIFTY_OUTPUT_PATH}")
    
    # 2. Fetch ETFs
    symbols = extract_symbols()
    if not symbols:
        print("[ERROR] No ETF symbols found.")
        return
        
    print(f"\n[INFO] Fetching {len(symbols)} ETFs...")
    etf_data = {}
    
    for idx, sym in enumerate(symbols, 1):
        if not sym or sym == "nan": continue
        yf_sym = f"{sym}.NS"
        print(f"[{idx}/{len(symbols)}] Fetching {yf_sym}...")
        try:
            df = yf.Ticker(yf_sym).history(period="max", interval="1d", auto_adjust=True)
            if not df.empty:
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                df.index.name = "Date"
                df = df.reset_index()
                df[["Open", "High", "Low", "Close"]] = df[["Open", "High", "Low", "Close"]].round(2)
                df.dropna(subset=["Close"], inplace=True)
                etf_data[sym] = df
        except Exception as e:
            print(f"  -> Failed: {e}")

    if etf_data:
        print(f"\n[INFO] Saving {len(etf_data)} ETFs to Excel...")
        with pd.ExcelWriter(ETF_OUTPUT_PATH, engine='openpyxl') as writer:
            for sym, df in etf_data.items():
                df.to_excel(writer, sheet_name=sym[:31], index=False)
        print(f"[SUCCESS] ETF Data saved to {ETF_OUTPUT_PATH}")

if __name__ == "__main__":
    fetch_data()
