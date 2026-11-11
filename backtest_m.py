import pandas as pd
import numpy as np
import datetime
import os
import sys
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# Config paths
ETF_PATH = "ETF_OHLCV.xlsx"
NIFTY_PATH = "nifty50_ohlcv.csv"
UPLOADS_DIR = "."
AUM_PATH = "aum.csv"

# ==========================================
# 1. DATA LOADING 
# ==========================================
def get_last_trading_day(target_date, nifty_df):
    if nifty_df is None or nifty_df.empty: return target_date
    df = nifty_df.copy()
    df['Date'] = pd.to_datetime(df['Date']).dt.date
    valid_dates = df[df['Date'] <= pd.to_datetime(target_date).date()]
    if not valid_dates.empty: return valid_dates['Date'].max()
    return target_date

def load_metadata():
    equity, non_equity, umap = set(), set(), {}
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
    nifty, etfs = None, None
    if os.path.exists(NIFTY_PATH): 
        nifty = pd.read_csv(NIFTY_PATH)
    elif os.path.exists(NIFTY_PATH.replace('csv', 'xlsx')): 
        nifty = pd.read_excel(NIFTY_PATH.replace('csv', 'xlsx'))
        
    if os.path.exists(ETF_PATH):
        try: 
            etfs = pd.read_excel(ETF_PATH, sheet_name=None)
        except Exception as e: 
            print(f"[ERROR] Failed reading ETF_OHLCV.xlsx: {e}")
            sys.exit(1)
            
    if nifty is None or etfs is None:
        print("[ERROR] STRICT ENFORCEMENT: Real OHLCV files not found.")
        print("Please ensure 'ETF_OHLCV.xlsx' and 'nifty50_ohlcv.csv' exist.")
        sys.exit(1)
        
    return nifty, etfs

# ==========================================
# 2. STRATEGY LOGIC
# ==========================================
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

def build_portfolio(scores_df, regime, port_type, eq_syms, neq_syms, umap, prev_holdings):
    if scores_df.empty: return []
    if port_type == "Conservative": N, max_eq = 5, (2 if regime == "BEAR" else 5)
    elif port_type == "Balanced": N, max_eq = 8, (3 if regime == "BEAR" else 8)
    else: N, max_eq = 10, (5 if regime == "BEAR" else 10)
        
    selected, underlying_counts, equity_count = [], {}, 0
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

# ==========================================
# 3. BACKTEST ENGINE
# ==========================================
def get_price(etfs, sym, date):
    df = etfs[sym]
    df['Date'] = pd.to_datetime(df['Date'])
    row = df[df['Date'] <= pd.to_datetime(date)].sort_values('Date').tail(1)
    if row.empty: return 0
    return float(row['Close'].iloc[0])

def run_backtest(start_date, end_date, initial_capital, ptype, nifty, etfs, eq, neq, umap, aum_data):
    dates = pd.date_range(start=start_date, end=end_date, freq='BME')
    N = 5 if ptype == "Conservative" else (8 if ptype == "Balanced" else 10)

    cash = initial_capital
    holdings = {} 
    open_lots = {} 
    
    trade_log = []
    portfolio_history = []
    rebalance_months = set()
    
    for d in dates:
        actual_date = get_last_trading_day(d, nifty)
        
        current_eq_value = 0
        current_prices = {}
        for sym, pos in holdings.items():
            price = get_price(etfs, sym, actual_date)
            current_prices[sym] = price
            current_eq_value += pos['qty'] * price
            
        port_val = cash + current_eq_value
        portfolio_history.append({'Date': actual_date, 'Total_Value': port_val})
            
        scores, regime = compute_metrics(actual_date, nifty, etfs, aum_data)
        targets = build_portfolio(scores, regime, ptype, eq, neq, umap, list(holdings.keys()))
        
        if targets:
            target_w = 1.0 / N
            sells, buys = [], []
            scores_dict = scores.to_dict('index') if not scores.empty else {}
            
            for sym in list(holdings.keys()) + targets:
                sym_price = current_prices.get(sym) or get_price(etfs, sym, actual_date)
                if sym_price == 0: continue
                
                curr_w = (holdings.get(sym, {}).get('qty', 0) * sym_price) / port_val if sym in holdings else 0.0
                tgt_w = target_w if sym in targets else 0.0
                diff = tgt_w - curr_w
                
                if sym not in holdings and sym in targets: buys.append((sym, diff, sym_price))
                elif sym in holdings and sym not in targets: sells.append((sym, diff, sym_price))
                elif abs(diff) >= 0.05:
                    if diff < 0: sells.append((sym, diff, sym_price))
                    else: buys.append((sym, diff, sym_price))

            total_sell_w = sum(abs(item[1]) for item in sells)
            sell_scale = 1.0
            if total_sell_w > 0.40: sell_scale = 0.40 / total_sell_w
                
            # EXECUTE SELLS (FIFO)
            for sym, diff_w, price in sells:
                exec_w = abs(diff_w) * sell_scale
                if sym not in targets: exec_w = abs(diff_w) 
                
                sell_qty = int((exec_w * port_val) // price)
                if sell_qty > 0 and sym in holdings:
                    sell_qty = min(sell_qty, holdings[sym]['qty'])
                    
                    # Capture weight/score BEFORE reducing holdings
                    pre_sell_weight = (holdings[sym]['qty'] * price) / port_val * 100
                    sell_score = scores_dict.get(sym, {}).get('Final_Score', "N/A")
                    sell_rank = scores_dict.get(sym, {}).get('Rank', "N/A")
                    
                    holdings[sym]['qty'] -= sell_qty
                    txn_cost_sell = (sell_qty * price) * 0.001
                    cash += (sell_qty * price) - txn_cost_sell
                    
                    qty_to_match = sell_qty
                    while qty_to_match > 0 and open_lots.get(sym):
                        lot = open_lots[sym][0]
                        cat = "EQUITY" if sym in eq else "NON_EQUITY"
                        
                        if lot['qty'] <= qty_to_match:
                            trade_log.append({
                                'Portfolio': ptype, 'Symbol': sym, 'Category': cat,
                                'Date Bought': lot['date'].strftime('%Y-%m-%d'), 
                                'Buy Score': lot.get('score', 'N/A'), 'Buy Rank': lot.get('rank', 'N/A'), 'Buy Weight (%)': lot.get('weight', 0),
                                'Buy Price': round(lot['price'],2), 
                                'Qty': lot['qty'], 'Invested Amount': round(lot['qty']*lot['price'],2),
                                'Brokerage (Buy)': round(lot['brokerage'],2),
                                'Date Sold': actual_date.strftime('%Y-%m-%d'), 
                                'Sell Score': round(sell_score, 4) if isinstance(sell_score, float) else sell_score,
                                'Sell Rank': int(sell_rank) if isinstance(sell_rank, float) else sell_rank,
                                'Sell Weight (%)': round(pre_sell_weight, 2),
                                'Sell Price': round(price,2),
                                'Sale Proceeds': round(lot['qty']*price,2),
                                'Gross P&L': round((lot['qty']*price) - (lot['qty']*lot['price']),2),
                                'Transaction Cost': round(lot['brokerage'] + (lot['qty']*price*0.001),2)
                            })
                            qty_to_match -= lot['qty']
                            open_lots[sym].pop(0)
                        else:
                            pro_rata_brokerage = lot['brokerage'] * (qty_to_match / lot['qty'])
                            trade_log.append({
                                'Portfolio': ptype, 'Symbol': sym, 'Category': cat,
                                'Date Bought': lot['date'].strftime('%Y-%m-%d'), 
                                'Buy Score': lot.get('score', 'N/A'), 'Buy Rank': lot.get('rank', 'N/A'), 'Buy Weight (%)': lot.get('weight', 0),
                                'Buy Price': round(lot['price'],2), 
                                'Qty': qty_to_match, 'Invested Amount': round(qty_to_match*lot['price'],2),
                                'Brokerage (Buy)': round(pro_rata_brokerage,2),
                                'Date Sold': actual_date.strftime('%Y-%m-%d'), 
                                'Sell Score': round(sell_score, 4) if isinstance(sell_score, float) else sell_score,
                                'Sell Rank': int(sell_rank) if isinstance(sell_rank, float) else sell_rank,
                                'Sell Weight (%)': round(pre_sell_weight, 2),
                                'Sell Price': round(price,2),
                                'Sale Proceeds': round(qty_to_match*price,2),
                                'Gross P&L': round((qty_to_match*price) - (qty_to_match*lot['price']),2),
                                'Transaction Cost': round(pro_rata_brokerage + (qty_to_match*price*0.001),2)
                            })
                            lot['qty'] -= qty_to_match
                            lot['brokerage'] -= pro_rata_brokerage
                            qty_to_match = 0
                            
                    rebalance_months.add(actual_date)
                    if holdings[sym]['qty'] == 0: del holdings[sym]
                        
            # EXECUTE BUYS
            for sym, diff_w, price in buys:
                buy_qty = int((min(diff_w * port_val, cash)) // price)
                if buy_qty > 0:
                    txn_cost_buy = (buy_qty * price) * 0.001
                    cash -= (buy_qty * price) + txn_cost_buy
                    
                    buy_weight = round((buy_qty * price) / port_val * 100, 2)
                    buy_score = scores_dict.get(sym, {}).get('Final_Score', "N/A")
                    if isinstance(buy_score, float): buy_score = round(buy_score, 4)
                    buy_rank = scores_dict.get(sym, {}).get('Rank', "N/A")
                    
                    if sym not in holdings: holdings[sym] = {'qty': 0}
                    holdings[sym]['qty'] += buy_qty
                    
                    if sym not in open_lots: open_lots[sym] = []
                    open_lots[sym].append({
                        'date': actual_date, 'price': price, 
                        'qty': buy_qty, 'brokerage': txn_cost_buy,
                        'score': buy_score, 'rank': buy_rank, 'weight': buy_weight
                    })
                    rebalance_months.add(actual_date)

    return pd.DataFrame(portfolio_history), pd.DataFrame(trade_log), len(rebalance_months)

# ==========================================
# 4. PLOTTING FUNCTION
# ==========================================
def plot_multi_performance(nav_df, dd_df):
    plt.style.use('ggplot')
    fig, axes = plt.subplots(2, 1, figsize=(14, 12), gridspec_kw={'height_ratios': [2, 1]})
    
    colors = {'Conservative': 'blue', 'Balanced': 'green', 'Aggressive': 'orange', 'NIFTY NAV': 'black'}
    
    for col in nav_df.columns:
        color = colors.get(col, 'gray')
        is_bench = "NIFTY" in col
        axes[0].plot(nav_df.index, nav_df[col], label=col if not is_bench else 'NIFTY 50 (Benchmark)', 
                     linewidth=1.5 if is_bench else 2, linestyle='--' if is_bench else '-')
                     
    axes[0].set_title('ETF Smart Portfolio — Multi-Strategy NAV Growth')
    axes[0].set_ylabel('Total Value (₹)')
    axes[0].legend(loc="upper left")
    
    for col in dd_df.columns:
        color = colors.get(col, 'gray')
        axes[1].plot(dd_df.index, dd_df[col] * 100, label=col, linewidth=1.5)
        if col != 'NIFTY NAV':
            axes[1].fill_between(dd_df.index, dd_df[col] * 100, 0, color=color, alpha=0.1)
            
    axes[1].set_title('Underwater Plot (Drawdowns %)')
    axes[1].set_ylabel('Drawdown (%)')
    axes[1].set_ylim([min(dd_df.min()) * 100 * 1.1, 0])
    axes[1].legend(loc="lower left")
    
    plt.tight_layout()
    plt.savefig("backtest_metrics_m.png", dpi=150)
    print("\n[INFO] Generated 'backtest_metrics_m.png'")


# ==========================================
# 5. DASHBOARD GENERATOR
# ==========================================
def main():
    start_date, end_date = "2022-10-01", "2026-03-31"
    initial_cap = 1000000
    
    eq, neq, umap, aum_data = load_metadata()
    nifty, etfs = load_data()
    
    print("============================================================")
    print(" ETF SMART PORTFOLIO FULL BACKTEST SUITE ")
    print("============================================================")
    
    portfolios = ["Conservative", "Balanced", "Aggressive"]
    all_port_hist = {}
    all_trades = {}
    reb_counts = {}
    
    n_df = nifty.copy()
    n_df['Date'] = pd.to_datetime(n_df['Date'])
    n_df = n_df[(n_df['Date'] >= pd.to_datetime(start_date)) & (n_df['Date'] <= pd.to_datetime(end_date))].set_index('Date')
    n_nav = (n_df['Close'] / n_df['Close'].iloc[0]) * initial_cap if not n_df.empty else pd.Series()

    for ptype in portfolios:
        print(f"Running {ptype} Strategy Engine...")
        hist, trades, rebalance_count = run_backtest(start_date, end_date, initial_cap, ptype, nifty, etfs, eq, neq, umap, aum_data)
        all_port_hist[ptype] = hist
        all_trades[ptype] = trades
        reb_counts[ptype] = rebalance_count

    nav_history = pd.DataFrame(index=pd.to_datetime(all_port_hist["Balanced"]['Date']))
    nav_history.index.name = "Date"
    for p in portfolios:
        df = all_port_hist[p].set_index('Date')
        nav_history[p] = df['Total_Value']
        
    if not n_nav.empty:
        nav_history['NIFTY NAV'] = n_nav.reindex(nav_history.index, method='ffill')
    
    drawdown_hist = pd.DataFrame(index=nav_history.index)
    for col in nav_history.columns:
        peak = nav_history[col].cummax()
        drawdown_hist[col] = (nav_history[col] - peak) / peak

    summary_data = []
    for col in nav_history.columns:
        s = nav_history[col].dropna()
        if s.empty: continue
        
        initial = s.iloc[0]
        final = s.iloc[-1]
        tot_ret = (final / initial) - 1
        
        months = len(s)
        cagr = (final / initial) ** (12 / months) - 1 if months > 0 else 0
        
        monthly_returns = s.pct_change().dropna()
        ann_vol = monthly_returns.std() * np.sqrt(12)
        sharpe = (cagr - 0.05) / ann_vol if ann_vol > 0 else 0
        
        max_dd = drawdown_hist[col].min()
        calmar = cagr / abs(max_dd) if max_dd != 0 else 0
        
        win_rate = (monthly_returns > 0).mean()
        
        summary_data.append({
            'Portfolio': col if "NIFTY" not in col else "NIFTY 50 (Benchmark)",
            'Initial Capital (₹)': initial,
            'Final Value (₹)': round(final, 2),
            'Total Return (%)': round(tot_ret * 100, 2),
            'CAGR (%)': round(cagr * 100, 2),
            'Annualised Volatility (%)': round(ann_vol * 100, 2),
            'Sharpe Ratio': round(sharpe, 3),
            'Max Drawdown (%)': round(max_dd * 100, 2),
            'Calmar Ratio': round(calmar, 2),
            'Win Rate (%)': round(win_rate * 100, 2),
            '# Rebalance': reb_counts.get(col, 0)
        })
        
    summary_df = pd.DataFrame(summary_data)

    excel_filename = 'backtest_results_m.xlsx'
    with pd.ExcelWriter(excel_filename, engine='xlsxwriter') as writer:
        
        summary_df.to_excel(writer, sheet_name='Summary', index=False)
        nav_history.reset_index().to_excel(writer, sheet_name='NAV History', index=False)
        
        if not all_trades["Conservative"].empty: all_trades["Conservative"].to_excel(writer, sheet_name='Trades (Con)', index=False)
        if not all_trades["Balanced"].empty: all_trades["Balanced"].to_excel(writer, sheet_name='Trades (Bal)', index=False)
        if not all_trades["Aggressive"].empty: all_trades["Aggressive"].to_excel(writer, sheet_name='Trades (Agg)', index=False)
        
        drawdown_hist.reset_index().to_excel(writer, sheet_name='Drawdown', index=False)
        
        workbook = writer.book
        fmt_header = workbook.add_format({'bold': True, 'bg_color': '#1f497d', 'font_color': 'white', 'border': 1})
        fmt_num = workbook.add_format({'num_format': '#,##0.00'})
        
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            worksheet.set_row(0, None, fmt_header)
            worksheet.set_column('A:Z', 15, fmt_num)

    print(f"\n[SUCCESS] Extracted perfectly formatted multi-sheet Excel: {excel_filename}")
    
    os.makedirs('backtest_csvs_m', exist_ok=True)
    summary_df.to_csv('backtest_csvs_m/summary.csv', index=False)
    nav_history.reset_index().to_csv('backtest_csvs_m/nav_history.csv', index=False)
    drawdown_hist.reset_index().to_csv('backtest_csvs_m/drawdown.csv', index=False)
    if not all_trades["Conservative"].empty: all_trades["Conservative"].to_csv('backtest_csvs_m/trades_conservative.csv', index=False)
    if not all_trades["Balanced"].empty: all_trades["Balanced"].to_csv('backtest_csvs_m/trades_balanced.csv', index=False)
    if not all_trades["Aggressive"].empty: all_trades["Aggressive"].to_csv('backtest_csvs_m/trades_aggressive.csv', index=False)
    print(f"[SUCCESS] Extracted 6 raw CSV files into 'backtest_csvs_m/' directory")

    plot_multi_performance(nav_history, drawdown_hist)

    print("============================================================")
    print(summary_df[['Portfolio', 'CAGR (%)', 'Max Drawdown (%)', 'Sharpe Ratio']].to_string(index=False))

if __name__ == "__main__":
    main()
