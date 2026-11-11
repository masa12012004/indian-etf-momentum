import pandas as pd
import numpy as np
import datetime
import sys
import os
import warnings
warnings.filterwarnings('ignore')

# Config paths
ETF_PATH = "ETF_OHLCV.xlsx"
NIFTY_PATH = "nifty50_ohlcv.csv"

UPLOADS_DIR = "."
AUM_PATH = "aum.csv" # Create a simple CSV with SYMBOL, AUM columns to enforce this rule

def get_evaluation_month(dt):
    if pd.isna(dt): dt = datetime.date.today()
    if isinstance(dt, str): dt = pd.to_datetime(dt).date()
    if isinstance(dt, datetime.datetime): dt = dt.date()
    return (pd.to_datetime(dt) + pd.offsets.MonthEnd(0)).date()

def get_last_trading_day(target_date, nifty_df):
    if nifty_df is None or nifty_df.empty: return target_date
    df = nifty_df.copy()
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    valid_dates = df[df['Date'] <= target_date]
    if not valid_dates.empty:
        return valid_dates['Date'].max()
    return target_date

def load_metadata():
    equity = set()
    non_equity = set()
    umap = {}
    
    def parse_csv(filename, is_eq):
        path = os.path.join(UPLOADS_DIR, filename)
        if os.path.exists(path):
            df = pd.read_csv(path)
            if "SYMBOL" in df.columns:
                for _, row in df.iterrows():
                    sym = str(row["SYMBOL"]).strip()
                    asset = str(row["UNDERLYING ASSET"]).strip() if "UNDERLYING ASSET" in df.columns else "Unknown"
                    umap[sym] = asset
                    if is_eq is True: equity.add(sym)
                    elif is_eq is False: non_equity.add(sym)
                    
    parse_csv("equity_etfs.csv", True)
    parse_csv("gold_etf.csv", False)
    parse_csv("non_equity_etfs.csv", False)
    
    # Load AUM data if available
    aum_data = {}
    if os.path.exists(AUM_PATH):
        try:
            aum_df = pd.read_csv(AUM_PATH)
            # Find symbol and aum columns dynamically
            sym_col = next((col for col in aum_df.columns if "SYMBOL" in col.upper()), None)
            aum_col = next((col for col in aum_df.columns if "AUM" in col.upper()), None)
            
            if sym_col and aum_col:
                for _, row in aum_df.iterrows():
                    aum_data[str(row[sym_col]).strip()] = float(row[aum_col])
        except Exception as e:
            print(f"[WARNING] Could not parse AUM file: {e}")
            
    return equity, non_equity, umap, aum_data

def load_data():
    nifty = None
    etfs = None
    
    # Check for Nifty
    if os.path.exists(NIFTY_PATH):
        nifty = pd.read_csv(NIFTY_PATH)
    elif os.path.exists(NIFTY_PATH.replace("csv", "xlsx")):
        nifty = pd.read_excel(NIFTY_PATH.replace("csv", "xlsx"))
        
    # Check for ETFs
    if os.path.exists(ETF_PATH):
        try:
            etfs = pd.read_excel(ETF_PATH, sheet_name=None)
        except Exception as e:
            print(f"Error reading ETF excel: {e}")
            
    if nifty is None or etfs is None:
        print("[ERROR] Required OHLCV files not found. Ensure ETF_OHLCV.xlsx and nifty CSV/Excel exist.")
        sys.exit(1)
        
    return nifty, etfs

def compute_metrics(eval_date, nifty_df, etf_dfs, aum_data):
    eval_dt = pd.to_datetime(eval_date)
    ndf = nifty_df.copy()
    ndf['Date'] = pd.to_datetime(ndf['Date'])
    ndf = ndf[ndf['Date'] <= eval_dt].sort_values('Date')
    
    if len(ndf) < 200: return pd.DataFrame(), "UNKNOWN"
        
    n_close = ndf['Close'].values[-1]
    n_ma200 = ndf['Close'].values[-200:].mean()
    regime = "BULL" if n_close > n_ma200 else "BEAR"
    n_ret6m = (n_close / ndf['Close'].values[-126] - 1) if len(ndf) >= 126 else 0
    
    results = []
    for sym, df in etf_dfs.items():
        # AUM Filter: Minimum 500 Crores
        if aum_data and sym in aum_data:
            if aum_data[sym] < 500:
                continue 
        
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        df = df[df['Date'] <= eval_dt].sort_values('Date')
        
        # MISSING RULE 1: At least 2 years of price history (~504 trading days)
        if len(df) < 504:
            continue
            
        close = df['Close'].values
        vol = df['Volume'].values
        current_close = close[-1]
        
        # MISSING RULE 2: Average daily traded value >= 5 Crore (Using last 20 days avg)
        avg_traded_value = (close[-20:] * vol[-20:]).mean()
        if avg_traded_value < 50000000:
            continue
            
        ma_200 = close[-200:].mean()
        ret_6m = current_close / close[-126] - 1
        ret_3m = current_close / close[-63] - 1
        ret_12m = current_close / close[-252] - 1
        
        daily_returns = np.diff(close[-64:]) / close[-64:-1]
        vol_3m = np.std(daily_returns) * np.sqrt(252)
        
        # ELIGIBILITY FILTER: Close > 200DMA and 6M Return > 0
        if current_close > ma_200 and ret_6m > 0:
            results.append({
                'Symbol': sym, 'Close': current_close,
                'ret_3m': ret_3m, 'ret_6m': ret_6m, 'ret_12m': ret_12m,
                'vol_3m': vol_3m
            })
            
    if not results: return pd.DataFrame(), regime
        
    res_df = pd.DataFrame(results).set_index('Symbol')
    
    # SCORING LOGIC
    res_df['mom_3m_rank'] = res_df['ret_3m'].rank(pct=True, ascending=True)
    res_df['mom_6m_rank'] = res_df['ret_6m'].rank(pct=True, ascending=True)
    res_df['mom_12m_rank'] = res_df['ret_12m'].rank(pct=True, ascending=True)
    res_df['Momentum'] = 0.4 * res_df['mom_6m_rank'] + 0.3 * res_df['mom_3m_rank'] + 0.3 * res_df['mom_12m_rank']
    res_df['rs_val'] = res_df['ret_6m'] - n_ret6m
    res_df['RS_Rank'] = res_df['rs_val'].rank(pct=True, ascending=True)
    res_df['Risk_Rank'] = res_df['vol_3m'].rank(pct=True, ascending=False)
    
    res_df['Final_Score'] = 0.6 * res_df['Momentum'] + 0.25 * res_df['RS_Rank'] + 0.15 * res_df['Risk_Rank']
    res_df = res_df.sort_values('Final_Score', ascending=False)
    res_df['Rank'] = np.arange(1, len(res_df) + 1)
    
    return res_df, regime

def build_portfolio(scores_df, regime, port_type, eq_syms, neq_syms, umap, prev_holdings=[]):
    if scores_df.empty: return []
        
    if port_type == "Conservative": N, max_eq = 5, (2 if regime == "BEAR" else 5)
    elif port_type == "Balanced": N, max_eq = 8, (3 if regime == "BEAR" else 8)
    else: N, max_eq = 10, (5 if regime == "BEAR" else 10)
        
    selected = []
    underlying_counts = {}
    equity_count = 0
    
    def add_etf(sym):
        nonlocal equity_count
        raw_theme = umap.get(sym, "Unknown")
        
        is_gold = "GOLD" in sym.upper() or "GOLD" in str(raw_theme).upper()
        is_eq = sym in eq_syms or (sym not in neq_syms and not is_gold and "LIQUID" not in sym.upper() and "GILT" not in sym.upper())
        
        if is_gold:
            theme = "Gold"
        elif not is_eq:
            theme = "Liquid"
        else:
            theme = raw_theme
            
        if regime == "BEAR" and is_eq and equity_count >= max_eq: return False
        
        # STRICT 35% concentration check
        if (underlying_counts.get(theme, 0) + 1) / N > 0.35: return False
        
        selected.append(sym)
        underlying_counts[theme] = underlying_counts.get(theme, 0) + 1
        if is_eq: equity_count += 1
        return True

    for sym in prev_holdings:
        if sym in scores_df.index and scores_df.loc[sym, 'Rank'] <= N + 2:
            add_etf(sym)
            if len(selected) == N: break
            
    for sym in scores_df.index:
        if len(selected) == N: break
        if sym not in selected: add_etf(sym)
            
    return selected

def analyze_rebalance(prev_holdings, new_holdings, prev_scores, new_scores, N):
    actions = []
    drifted_weights = {}
    total_drift_weight = 0
    
    # Calculate drifts
    for sym in prev_holdings:
        if sym in prev_scores.index and sym in new_scores.index:
            p_close = prev_scores.loc[sym, 'Close']
            n_close = new_scores.loc[sym, 'Close']
            drifted_weights[sym] = (1/N) * (n_close / p_close)
        else:
            drifted_weights[sym] = 1/N
        total_drift_weight += drifted_weights[sym]
        
    normalized_weights = {s: w/total_drift_weight for s, w in drifted_weights.items()}
    target_weight = 1/N
    
    turnover = 0.0 
    sells = []
    buys = []
    
    # Pass 1: Identify all trades
    for sym in set(prev_holdings + new_holdings):
        curr_w = normalized_weights.get(sym, 0.0)
        target_w = target_weight if sym in new_holdings else 0.0
        diff = target_w - curr_w
        
        if sym not in prev_holdings:
            buys.append((sym, diff, curr_w, target_w))
        elif sym not in new_holdings:
            sells.append((sym, diff, curr_w, target_w, True)) # True = Fully removed
        else:
            if abs(diff) >= 0.05: # Strict 5% Rule
                if diff < 0: sells.append((sym, diff, curr_w, target_w, False))
                else: buys.append((sym, diff, curr_w, target_w))
            else:
                actions.append(f"IGN  {sym:<15} | < 5% Change  | Curr Wt: {curr_w*100:4.1f}%, Target: {target_w*100:4.1f}%")

    # Pass 2: Calculate Turnover and Scale
    total_sell_w = sum(abs(item[1]) for item in sells)
    sell_scale = 1.0
    if total_sell_w > 0.40:
        sell_scale = 0.40 / total_sell_w
        actions.insert(0, f"[WARNING] Turnover was {total_sell_w*100:.1f}%. Trades dynamically scaled down to max 40%!")

    # Pass 3: Print Sells
    for sym, diff, curr_w, target_w, fully_removed in sells:
        exec_diff = abs(diff) * sell_scale
        if fully_removed: exec_diff = abs(diff) # Always fully liquidate dropouts
        
        final_w = curr_w - exec_diff
        if fully_removed:
            actions.append(f"SELL {sym:<15} | Removed      | Prev Wt: {curr_w*100:4.1f}% -> 0.0%")
        else:
            actions.append(f"SELL {sym:<15} | Rebalance    | Prev Wt: {curr_w*100:4.1f}% -> {final_w*100:4.1f}% (Scaled: -{exec_diff*100:.1f}%)")

    # Pass 4: Print Buys (Scaled to match cash generated by sells)
    for sym, diff, curr_w, target_w in buys:
        # Assuming equal distribution of generated cash
        exec_diff = diff * sell_scale
        final_w = curr_w + exec_diff
        if curr_w == 0:
            actions.append(f"BUY  {sym:<15} | New Entry    | Target Wt: {final_w*100:4.1f}% (Scaled: +{exec_diff*100:.1f}%)")
        else:
            actions.append(f"BUY  {sym:<15} | Rebalance    | Prev Wt: {curr_w*100:4.1f}% -> {final_w*100:4.1f}% (Scaled: +{exec_diff*100:.1f}%)")

    return actions

def main():
    target_date = datetime.date.today()
    if len(sys.argv) > 1:
        try: target_date = pd.to_datetime(sys.argv[1]).date()
        except: pass
        
    eval_month = get_evaluation_month(target_date)
    prev_month = (pd.to_datetime(eval_month) - pd.DateOffset(months=1)).date()
    
    eq, neq, umap, aum_data = load_metadata()
    nifty, etfs = load_data()
    
    actual_eval_date = get_last_trading_day(eval_month, nifty)
    actual_prev_date = get_last_trading_day(prev_month, nifty)
    
    print("="*65)
    print(" ETF SMART PORTFOLIO REBALANCING ENGINE (Monthly Execution)")
    print(f" Requested Date        : {target_date}")
    print(f" Actual Execution Date : {actual_eval_date}")
    print(f" Base Execution Date   : {actual_prev_date}")
    if aum_data:
        print(f" AUM Tracker           : Active ({len(aum_data)} entries found)")
    else:
        print(f" AUM Tracker           : Inactive (aum.csv not found)")
    print("="*65)
    
    prev_scores, prev_regime = compute_metrics(actual_prev_date, nifty, etfs, aum_data)
    curr_scores, curr_regime = compute_metrics(actual_eval_date, nifty, etfs, aum_data)
    
    if prev_scores.empty or curr_scores.empty:
        print("\n[ERROR] Insufficient data to compute signals for these dates.")
        return
        
    print(f"\n>> MARKET REGIME (NIFTY 200-DMA Check)")
    print(f"   Previous Week: {prev_regime} | Current Week: {curr_regime}")
    
    port_types = {"Conservative": 5, "Balanced": 8, "Aggressive": 10}
    
    for ptype, N in port_types.items():
        print(f"\n{'-'*65}")
        print(f" {ptype.upper()} PORTFOLIO (Target Holdings: {N})")
        print(f"{'-'*65}")
        
        prev_holdings = build_portfolio(prev_scores, prev_regime, ptype, eq, neq, umap, [])
        curr_holdings = build_portfolio(curr_scores, curr_regime, ptype, eq, neq, umap, prev_holdings)
        
        print(f"Current Target List: {', '.join(curr_holdings)}\n")
        
        actions = analyze_rebalance(prev_holdings, curr_holdings, prev_scores, curr_scores, N)
        for act in sorted(actions):
            print("  " + act)
            
    print("\n" + "="*65)

if __name__ == "__main__":
    main()
