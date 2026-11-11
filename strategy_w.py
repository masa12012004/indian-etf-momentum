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

def get_evaluation_friday(dt):
    if pd.isna(dt): dt = datetime.date.today()
    if isinstance(dt, str): dt = pd.to_datetime(dt).date()
    if isinstance(dt, datetime.datetime): dt = dt.date()
    offset = (dt.weekday() - 4) % 7
    return dt - datetime.timedelta(days=offset)

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
    return equity, non_equity, umap

def load_data():
    if os.path.exists(NIFTY_PATH) and os.path.exists(ETF_PATH):
        try:
            nifty = pd.read_csv(NIFTY_PATH)
            etfs = pd.read_excel(ETF_PATH, sheet_name=None)
            return nifty, etfs
        except Exception as e:
            print(f"Error reading excel: {e}")
    print("[ERROR] Required Excel files not found in the current directory.")
    sys.exit(1)

def compute_metrics(eval_date, nifty_df, etf_dfs):
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
        # Better fallback mapping if umap fails (using name hints)
        theme = umap.get(sym, None)
        if theme is None:
            if "GOLD" in sym.upper(): theme = "Gold"
            elif "LIQUID" in sym.upper(): theme = "Liquid"
            else: theme = "Unknown Equity"

        is_eq = sym in eq_syms or (sym not in neq_syms and "LIQUID" not in sym.upper() and "GOLD" not in sym.upper())
        
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
            
    # REMOVED THE BLIND FALLBACK LOOP: If we can't find N ETFs that satisfy rules, we hold cash/liquid.
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
    
    # MISSING RULE 3: 40% Turnover limit tracker
    turnover = 0.0 
    
    for sym in set(prev_holdings + new_holdings):
        curr_w = normalized_weights.get(sym, 0.0)
        target_w = target_weight if sym in new_holdings else 0.0
        diff = target_w - curr_w
        
        # Track Turnover (sum of sells)
        if diff < 0:
            turnover += abs(diff)
            
        if sym not in prev_holdings:
            actions.append(f"BUY  {sym:<15} | New Entry    | Target: {target_w*100:4.1f}%")
        elif sym not in new_holdings:
            actions.append(f"SELL {sym:<15} | Removed      | Prev Wt: {curr_w*100:4.1f}%")
        else:
            if abs(diff) >= 0.05:
                action = "BUY " if diff > 0 else "SELL"
                actions.append(f"{action} {sym:<15} | Rebalance    | {curr_w*100:4.1f}% -> {target_w*100:4.1f}%")
            else:
                actions.append(f"IGN  {sym:<15} | < 5% Change  | Curr Wt: {curr_w*100:4.1f}%, Target: {target_w*100:4.1f}%")
    
    if turnover > 0.40:
        actions.insert(0, f"[WARNING] Turnover is {turnover*100:.1f}%, which EXCEEDS the 40% maximum rule!")
        
    return actions

def main():
    target_date = datetime.date.today()
    if len(sys.argv) > 1:
        try: target_date = pd.to_datetime(sys.argv[1]).date()
        except: pass
        
    eval_friday = get_evaluation_friday(target_date)
    prev_friday = eval_friday - datetime.timedelta(days=7)
    
    eq, neq, umap = load_metadata()
    nifty, etfs = load_data()
    
    actual_eval_date = get_last_trading_day(eval_friday, nifty)
    actual_prev_date = get_last_trading_day(prev_friday, nifty)
    
    print("="*65)
    print(" ETF SMART PORTFOLIO REBALANCING ENGINE (Weekly Execution)")
    print(f" Requested Date        : {target_date}")
    print(f" Actual Execution Date : {actual_eval_date}")
    print(f" Base Execution Date   : {actual_prev_date}")
    print("="*65)
    
    prev_scores, prev_regime = compute_metrics(actual_prev_date, nifty, etfs)
    curr_scores, curr_regime = compute_metrics(actual_eval_date, nifty, etfs)
    
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