import pandas as pd
import numpy as np
import datetime
import os
import sys
import json
import warnings
warnings.filterwarnings('ignore')

UPLOADS_DIR = "."
ETF_PATH = "ETF_OHLCV.xlsx"
NIFTY_PATH = "nifty50_ohlcv.csv"
AUM_PATH = "aum.csv"
STATE_FILE = "state_paper_m.json"

IS_SL_ENABLED = False
SL_DROP_PCT = -0.025
STARTING_CAPITAL = 1000000.0

def load_metadata():
    equity, non_equity, umap = set(), set(), {}
    def parse_csv(filename, is_eq):
        path = os.path.join(UPLOADS_DIR, filename) if 'uploads' not in filename else filename
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
    
    aum_data = {}
    if os.path.exists(AUM_PATH):
        try:
            aum_df = pd.read_csv(AUM_PATH)
            sym_col = next((c for c in aum_df.columns if "SYMBOL" in c.upper()), None)
            aum_col = next((c for c in aum_df.columns if "AUM" in c.upper()), None)
            if sym_col and aum_col:
                for _, row in aum_df.iterrows():
                    aum_data[str(row[sym_col]).strip()] = float(row[aum_col])
        except: pass
    return equity, non_equity, umap, aum_data

def load_data():
    if not os.path.exists(NIFTY_PATH) or not os.path.exists(ETF_PATH):
        print("[ERROR] Real OHLCV files not found. Run update_data.py first.")
        sys.exit(1)
    nifty = pd.read_csv(NIFTY_PATH)
    etfs = pd.read_excel(ETF_PATH, sheet_name=None)
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
        if aum_data and sym in aum_data and aum_data[sym] < 500: continue 
        df = df.copy()
        df['Date'] = pd.to_datetime(df['Date'])
        df = df[df['Date'] <= eval_dt].sort_values('Date')
        
        if len(df) < 504: continue
        close, vol = df['Close'].values, df['Volume'].values
        if (close[-20:] * vol[-20:]).mean() < 50000000: continue
            
        current_close = close[-1]
        ma_200 = close[-200:].mean()
        ret_6m = current_close / close[-126] - 1
        ret_3m = current_close / close[-63] - 1
        ret_12m = current_close / close[-252] - 1
        
        daily_returns = np.diff(close[-64:]) / close[-64:-1]
        vol_3m = np.std(daily_returns) * np.sqrt(252)
        
        if current_close > ma_200 and ret_6m > 0:
            results.append({'Symbol': sym, 'Close': current_close, 'ret_3m': ret_3m, 'ret_6m': ret_6m, 'ret_12m': ret_12m, 'vol_3m': vol_3m})
            
    if not results: return pd.DataFrame(), regime
    res_df = pd.DataFrame(results).set_index('Symbol')
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

def build_portfolio(scores_df, regime, ptype, eq_syms, neq_syms, umap, prev_holdings):
    if scores_df.empty: return []
    N = 5 if ptype == "Conservative" else (8 if ptype == "Balanced" else 10)
    max_eq = (2 if regime == "BEAR" else 5) if ptype == "Conservative" else ((3 if regime == "BEAR" else 8) if ptype == "Balanced" else (5 if regime == "BEAR" else 10))
        
    selected, underlying_counts, equity_count = [], {}, 0
    def add_etf(sym):
        nonlocal equity_count
        raw_theme = umap.get(sym, "Unknown")
        is_gold = "GOLD" in sym.upper() or "GOLD" in str(raw_theme).upper()
        is_eq = sym in eq_syms or (sym not in neq_syms and not is_gold and "LIQUID" not in sym.upper() and "GILT" not in sym.upper())
        
        if is_gold: theme = "Gold"
        elif not is_eq: theme = "Liquid"
        else: theme = raw_theme
            
        if regime == "BEAR" and is_eq and equity_count >= max_eq: return False
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

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    # Master state holds all 3 portfolios independently
    return {
        "Conservative": {"cash": STARTING_CAPITAL, "holdings": {}, "open_lots": {}, "last_rebalance_date": None},
        "Balanced": {"cash": STARTING_CAPITAL, "holdings": {}, "open_lots": {}, "last_rebalance_date": None},
        "Aggressive": {"cash": STARTING_CAPITAL, "holdings": {}, "open_lots": {}, "last_rebalance_date": None}
    }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def export_trades(trade_log, excel_name):
    if not trade_log: return
    df = pd.DataFrame(trade_log)
    if os.path.exists(excel_name):
        try:
            existing_df = pd.read_excel(excel_name)
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df.drop_duplicates(subset=['Date Bought', 'Symbol', 'Date Sold'], keep='last', inplace=True)
            combined_df.to_excel(excel_name, index=False)
        except Exception as e:
            df.to_excel(excel_name, index=False)
    else:
        df.to_excel(excel_name, index=False)

def main():
    print("=====================================================")
    print(f" MULTI-PROFILE PAPER TRADING ENGINE: paper_m")
    print("=====================================================")
    
    master_state = load_state()
    eq, neq, umap, aum_data = load_metadata()
    nifty, etfs = load_data()
    
    nifty['Date'] = pd.to_datetime(nifty['Date']).dt.date
    actual_date = nifty['Date'].max()
    print(f"[INFO] Evaluating Market State for: {actual_date}")
    
    # Market data is exactly the same for all 3 portfolios today
    scores, regime = compute_metrics(actual_date, nifty, etfs, aum_data)
    
    portfolios = ["Conservative", "Balanced", "Aggressive"]
    
    for ptype in portfolios:
        print(f"\n--- Running Engine for {ptype.upper()} ---")
        N = 5 if ptype == "Conservative" else (8 if ptype == "Balanced" else 10)
        EXCEL_OUTPUT = f"paperTrade_paper_m_{ptype}.xlsx"
        
        state = master_state[ptype]
        cash = state['cash']
        holdings = state['holdings']
        open_lots = state['open_lots']
        last_reb = state['last_rebalance_date']
        trade_log = []
        
        is_scheduled = False
        
        # Monthly Logic (Business Month End)
        is_scheduled = (actual_date == (pd.to_datetime(actual_date) + pd.offsets.MonthEnd(0)).date())
        if is_scheduled and last_reb == str(actual_date): is_scheduled = False

        
        if float(cash) >= STARTING_CAPITAL and len(holdings) == 0:
            is_scheduled = True
            print(f"[INFO] {ptype} Portfolio is empty. Forcing initial deployment today!")

        is_emergency = False
        if IS_SL_ENABLED and not is_scheduled and holdings:
            for sym in holdings:
                df_sl = etfs.get(sym)
                if df_sl is not None:
                    df_past = df_sl[pd.to_datetime(df_sl['Date']).dt.date <= actual_date].sort_values('Date').tail(4)
                    if len(df_past) == 4:
                        closes = df_past['Close'].values
                        opens = df_past['Open'].values
                        if closes[0] > 0 and closes[1] > 0 and closes[2] > 0:
                            ret_t2 = (closes[1] / closes[0]) - 1
                            ret_t1 = (closes[2] / closes[1]) - 1
                            gap_t0 = (opens[3] / closes[2]) - 1
                            if ret_t2 <= SL_DROP_PCT and ret_t1 <= SL_DROP_PCT and gap_t0 < 0:
                                is_emergency = True
                                print(f"[EMERGENCY SL] {sym} triggered Drop rules. Forcing Rebalance!")
                                break
                                
        if not is_scheduled and not is_emergency:
            print(f"[INFO] No action required for {ptype} today.")
            continue
            
        print(f"[ACTION] Calculating trades for {ptype} (Scheduled: {is_scheduled}, Emergency: {is_emergency})")
        
        current_eq_value = 0
        current_prices = {}
        for sym, qty in holdings.items():
            df_sl = etfs.get(sym)
            row = df_sl[pd.to_datetime(df_sl['Date']).dt.date <= actual_date].sort_values('Date').tail(1) if df_sl is not None else pd.DataFrame()
            price = float(row['Close'].iloc[0]) if not row.empty else 0.0
            current_prices[sym] = price
            current_eq_value += qty * price
            
        port_val = cash + current_eq_value
        print(f"[PORTFOLIO] Total Value: ₹{port_val:,.2f} | Cash: ₹{cash:,.2f}")
        
        targets = build_portfolio(scores, regime, ptype, eq, neq, umap, list(holdings.keys()))
        
        target_w = 1.0 / N
        sells, buys = [], []
        scores_dict = scores.to_dict('index') if not scores.empty else {}
        
        for sym in list(holdings.keys()) + targets:
            sym_price = current_prices.get(sym)
            if sym_price is None:
                df_sl = etfs.get(sym)
                row = df_sl[pd.to_datetime(df_sl['Date']).dt.date <= actual_date].sort_values('Date').tail(1) if df_sl is not None else pd.DataFrame()
                sym_price = float(row['Close'].iloc[0]) if not row.empty else 0.0
                
            if sym_price == 0: continue
            
            curr_w = (holdings.get(sym, 0) * sym_price) / port_val if sym in holdings else 0.0
            tgt_w = target_w if sym in targets else 0.0
            diff = tgt_w - curr_w
            
            if sym not in holdings and sym in targets: buys.append((sym, diff, sym_price))
            elif sym in holdings and sym not in targets: sells.append((sym, diff, sym_price))
            elif abs(diff) >= 0.05:
                if diff < 0: sells.append((sym, diff, sym_price))
                else: buys.append((sym, diff, sym_price))

        total_sell_w = sum(abs(item[1]) for item in sells)
        sell_scale = 1.0
        if total_sell_w > 0.40: 
            sell_scale = 0.40 / total_sell_w
            print(f"[CAP] 40% Turnover rule enforced for {ptype}.")
            
        # Execute Sells
        for sym, diff_w, price in sells:
            exec_w = abs(diff_w) * sell_scale
            if sym not in targets: exec_w = abs(diff_w)
            
            sell_qty = int((exec_w * port_val) // price)
            if sell_qty > 0 and sym in holdings:
                sell_qty = min(sell_qty, holdings[sym])
                pre_sell_weight = (holdings[sym] * price) / port_val * 100
                sell_score = scores_dict.get(sym, {}).get('Final_Score', "N/A")
                sell_rank = scores_dict.get(sym, {}).get('Rank', "N/A")
                
                holdings[sym] -= sell_qty
                txn_cost_sell = (sell_qty * price) * 0.001
                cash += (sell_qty * price) - txn_cost_sell
                print(f"  -> SELL {sell_qty} shares of {sym} at ₹{price}")
                
                qty_to_match = sell_qty
                while qty_to_match > 0 and open_lots.get(sym):
                    lot = open_lots[sym][0]
                    cat = "EQUITY" if sym in eq else "NON_EQUITY"
                    if lot['qty'] <= qty_to_match:
                        trade_log.append({
                            'Date Bought': lot['date'], 'Buy Score': lot.get('score', 'N/A'), 'Buy Rank': lot.get('rank', 'N/A'), 'Buy Weight (%)': lot.get('weight', 0),
                            'Symbol': sym, 'Category': cat, 'Buy Price': round(lot['price'],2), 
                            'Qty': lot['qty'], 'Invested Amount': round(lot['qty']*lot['price'],2),
                            'Brokerage (Buy)': round(lot['brokerage'],2),
                            'Date Sold': actual_date.strftime('%Y-%m-%d'), 'Sell Score': round(sell_score, 4) if isinstance(sell_score, float) else sell_score,
                            'Sell Rank': int(sell_rank) if isinstance(sell_rank, float) else sell_rank, 'Sell Weight (%)': round(pre_sell_weight, 2),
                            'Sell Price': round(price,2), 'Sale Proceeds': round(lot['qty']*price,2),
                            'Gross P&L': round((lot['qty']*price) - (lot['qty']*lot['price']),2),
                            'Transaction Cost': round(lot['brokerage'] + (lot['qty']*price*0.001),2)
                        })
                        qty_to_match -= lot['qty']
                        open_lots[sym].pop(0)
                    else:
                        pro_rata_brokerage = lot['brokerage'] * (qty_to_match / lot['qty'])
                        trade_log.append({
                            'Date Bought': lot['date'], 'Buy Score': lot.get('score', 'N/A'), 'Buy Rank': lot.get('rank', 'N/A'), 'Buy Weight (%)': lot.get('weight', 0),
                            'Symbol': sym, 'Category': cat, 'Buy Price': round(lot['price'],2), 
                            'Qty': qty_to_match, 'Invested Amount': round(qty_to_match*lot['price'],2),
                            'Brokerage (Buy)': round(pro_rata_brokerage,2),
                            'Date Sold': actual_date.strftime('%Y-%m-%d'), 'Sell Score': round(sell_score, 4) if isinstance(sell_score, float) else sell_score,
                            'Sell Rank': int(sell_rank) if isinstance(sell_rank, float) else sell_rank, 'Sell Weight (%)': round(pre_sell_weight, 2),
                            'Sell Price': round(price,2), 'Sale Proceeds': round(qty_to_match*price,2),
                            'Gross P&L': round((qty_to_match*price) - (qty_to_match*lot['price']),2),
                            'Transaction Cost': round(pro_rata_brokerage + (qty_to_match*price*0.001),2)
                        })
                        lot['qty'] -= qty_to_match
                        lot['brokerage'] -= pro_rata_brokerage
                        qty_to_match = 0
                if holdings[sym] == 0: del holdings[sym]

        # Buys
        for sym, diff_w, price in buys:
            buy_qty = int((min(diff_w * port_val, cash)) // price)
            if buy_qty > 0:
                txn_cost_buy = (buy_qty * price) * 0.001
                cash -= (buy_qty * price) + txn_cost_buy
                buy_weight = round((buy_qty * price) / port_val * 100, 2)
                buy_score = scores_dict.get(sym, {}).get('Final_Score', "N/A")
                if isinstance(buy_score, float): buy_score = round(buy_score, 4)
                buy_rank = scores_dict.get(sym, {}).get('Rank', "N/A")
                
                holdings[sym] = holdings.get(sym, 0) + buy_qty
                if sym not in open_lots: open_lots[sym] = []
                open_lots[sym].append({
                    'date': actual_date.strftime('%Y-%m-%d'), 'price': price, 
                    'qty': buy_qty, 'brokerage': txn_cost_buy,
                    'score': buy_score, 'rank': buy_rank, 'weight': buy_weight
                })
                print(f"  -> BUY {buy_qty} shares of {sym} at ₹{price}")

        # Save individual state
        state['cash'] = cash
        state['holdings'] = holdings
        state['open_lots'] = open_lots
        state['last_rebalance_date'] = str(actual_date)
        master_state[ptype] = state
        
        export_trades(trade_log, EXCEL_OUTPUT)

    save_state(master_state)
    print("[SUCCESS] Master State Updated for all portfolios.")

if __name__ == "__main__":
    main()
