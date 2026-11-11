import pandas as pd
import numpy as np
import datetime
import os
import sys
import warnings
import matplotlib.pyplot as plt

warnings.filterwarnings('ignore')

try:
    import docx
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    print("[ERROR] 'python-docx' library is required to generate the Word document.")
    print("Please install it by running: pip install python-docx")
    sys.exit(1)

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
# 3. ZERODHA SNAPSHOT ENGINE
# ==========================================
def get_price(etfs, sym, date):
    df = etfs[sym]
    df['Date'] = pd.to_datetime(df['Date'])
    row = df[df['Date'] <= pd.to_datetime(date)].sort_values('Date').tail(1)
    if row.empty: return 0
    return float(row['Close'].iloc[0])

def generate_zerodha_snapshots(start_date, end_date, initial_capital, ptype, nifty, etfs, eq, neq, umap, aum_data):
    dates = pd.date_range(start=start_date, end=end_date, freq='W-FRI')
    N = 5 if ptype == "Conservative" else (8 if ptype == "Balanced" else 10)

    cash = initial_capital
    holdings = {} 
    
    snapshots = {}
    high_water_mark = initial_capital

    for d in dates:
        actual_date = get_last_trading_day(d, nifty)
        
        current_eq_value = 0
        current_prices = {}
        for sym, pos in holdings.items():
            price = get_price(etfs, sym, actual_date)
            current_prices[sym] = price
            current_eq_value += pos['qty'] * price
            
        port_val = cash + current_eq_value
        
        scores, regime = compute_metrics(actual_date, nifty, etfs, aum_data)
        targets = build_portfolio(scores, regime, ptype, eq, neq, umap, list(holdings.keys()))
        
        if targets: 
            target_w = 1.0 / N
            sells, buys = [], []
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
                
            for sym, diff_w, price in sells:
                exec_w = abs(diff_w) * sell_scale
                if sym not in targets: exec_w = abs(diff_w) 
                sell_qty = int((exec_w * port_val) // price)
                if sell_qty > 0 and sym in holdings:
                    sell_qty = min(sell_qty, holdings[sym]['qty'])
                    holdings[sym]['qty'] -= sell_qty
                    txn_cost_sell = (sell_qty * price) * 0.001
                    cash += (sell_qty * price) - txn_cost_sell
                    if holdings[sym]['qty'] == 0: del holdings[sym]
                        
            for sym, diff_w, price in buys:
                buy_qty = int((min(diff_w * port_val, cash)) // price)
                if buy_qty > 0:
                    txn_cost_buy = (buy_qty * price) * 0.001
                    cash -= (buy_qty * price) + txn_cost_buy
                    if sym not in holdings: holdings[sym] = {'qty': 0, 'avg_price': price}
                    old_val = holdings[sym]['qty'] * holdings[sym]['avg_price']
                    new_val = buy_qty * price
                    holdings[sym]['avg_price'] = (old_val + new_val) / (holdings[sym]['qty'] + buy_qty)
                    holdings[sym]['qty'] += buy_qty

        post_eq_value = 0
        holding_rows = []
        
        for sym, pos in holdings.items():
            sym_price = current_prices.get(sym) or get_price(etfs, sym, actual_date)
            inv_val = pos['qty'] * pos['avg_price']
            cur_val = pos['qty'] * sym_price
            pnl = cur_val - inv_val
            pnl_pct = (pnl / inv_val) * 100 if inv_val > 0 else 0
            post_eq_value += cur_val
            
            theme = umap.get(sym, "Unknown")
            
            holding_rows.append({
                'Instrument': sym,
                'Theme': theme,
                'Qty.': pos['qty'],
                'Avg. cost': pos['avg_price'],
                'LTP': sym_price,
                'Cur. val': cur_val,
                'Inv. val': inv_val,
                'P&L': pnl,
                'Net chg.': pnl_pct
            })
            
        total_account_val = cash + post_eq_value
        
        if total_account_val > high_water_mark: high_water_mark = total_account_val
        drawdown_pct = ((total_account_val - high_water_mark) / high_water_mark) * 100
            
        snapshots[actual_date] = {
            'date': actual_date.strftime('%Y-%m-%d'),
            'regime': regime,
            'cash': cash,
            'equity_val': post_eq_value,
            'total_val': total_account_val,
            'drawdown_pct': drawdown_pct,
            'holdings': holding_rows
        }
        
    return snapshots

# ==========================================
# 4. WORD EXPORT FUNCTION
# ==========================================
def export_to_word(all_snapshots, initial_capital):
    print(f"\nBuilding Word Document (portfolio_w.docx) for all Strategies...")
    doc = docx.Document()
    
    title = doc.add_heading('Zerodha-Style Portfolio Holdings - Multi-Strategy Report', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    doc.add_paragraph(f"Execution Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    doc.add_paragraph(f"Initial Capital (Per Strategy): ₹{initial_capital:,.2f}")
    doc.add_paragraph("-" * 80)
    
    for ptype, snapshots in all_snapshots.items():
        doc.add_heading(f"{ptype} Strategy Holdings", level=1)
        sorted_dates = sorted(snapshots.keys())
        
        for date_key in sorted_dates:
            snap = snapshots[date_key]
            
            doc.add_heading(f"Snapshot: {snap['date']} | Regime: {snap['regime']}", level=2)
            
            p = doc.add_paragraph()
            p.add_run(f"Total Account Value: ").bold = True
            p.add_run(f"₹{snap['total_val']:,.2f}\n")
            
            p.add_run(f"Invested Value: ").bold = True
            p.add_run(f"₹{snap['equity_val']:,.2f}    ")
            
            p.add_run(f"Cash Balance: ").bold = True
            p.add_run(f"₹{snap['cash']:,.2f}\n")
            
            dd = snap['drawdown_pct']
            status_text = "🟢 All-Time High / Healthy" if dd >= -1.0 else f"🔴 In Drawdown ({dd:.2f}%)"
            p.add_run(f"Account Health: ").bold = True
            p.add_run(status_text)
            
            if not snap['holdings']:
                doc.add_paragraph("No active positions. Portfolio is 100% Cash.", style='Intense Quote')
                doc.add_paragraph("\n")
                continue
                
            table = doc.add_table(rows=1, cols=8)
            table.style = 'Light Grid Accent 1' 
            
            hdr_cells = table.rows[0].cells
            headers = ['Instrument', 'Theme', 'Qty.', 'Avg. cost', 'LTP', 'Inv. val', 'Cur. val', 'P&L']
            for i, header in enumerate(headers):
                hdr_cells[i].text = header
                hdr_cells[i].paragraphs[0].runs[0].bold = True
                
            total_inv, total_cur, total_pnl = 0, 0, 0
            
            for h in snap['holdings']:
                row_cells = table.add_row().cells
                row_cells[0].text = h['Instrument']
                row_cells[1].text = h['Theme']
                row_cells[2].text = f"{h['Qty.']:,}"
                row_cells[3].text = f"₹{h['Avg. cost']:,.2f}"
                row_cells[4].text = f"₹{h['LTP']:,.2f}"
                row_cells[5].text = f"₹{h['Inv. val']:,.2f}"
                row_cells[6].text = f"₹{h['Cur. val']:,.2f}"
                
                pnl_str = f"₹{h['P&L']:,.2f} ({h['Net chg.']:+.2f}%)"
                row_cells[7].text = pnl_str
                
                total_inv += h['Inv. val']
                total_cur += h['Cur. val']
                total_pnl += h['P&L']
                
            sum_row = table.add_row().cells
            sum_row[0].text = "TOTAL"
            sum_row[0].paragraphs[0].runs[0].bold = True
            sum_row[5].text = f"₹{total_inv:,.2f}"
            sum_row[5].paragraphs[0].runs[0].bold = True
            sum_row[6].text = f"₹{total_cur:,.2f}"
            sum_row[6].paragraphs[0].runs[0].bold = True
            
            net_pct = (total_pnl / total_inv * 100) if total_inv > 0 else 0
            sum_row[7].text = f"₹{total_pnl:,.2f} ({net_pct:+.2f}%)"
            sum_row[7].paragraphs[0].runs[0].bold = True
            doc.add_paragraph("\n") 

        doc.add_page_break()

    doc.save('portfolio_w.docx')
    print("[SUCCESS] Successfully generated 'portfolio_w.docx' covering all strategies.")

# ==========================================
# 5. EXCEL EXPORT FUNCTION 
# ==========================================
def export_to_excel(all_snapshots):
    print("Building flat Excel database of all snapshots...")
    flat_data = []
    
    for ptype, snapshots in all_snapshots.items():
        for date_key in sorted(snapshots.keys()):
            snap = snapshots[date_key]
            for h in snap['holdings']:
                row = h.copy()
                row['Strategy'] = ptype
                row['Date'] = snap['date']
                row['Regime'] = snap['regime']
                row['Portfolio Total Value'] = snap['total_val']
                flat_data.append(row)
            
    if flat_data:
        df = pd.DataFrame(flat_data)
        cols = ['Strategy', 'Date', 'Regime', 'Portfolio Total Value', 'Instrument', 'Theme', 'Qty.', 'Avg. cost', 'LTP', 'Inv. val', 'Cur. val', 'P&L', 'Net chg.']
        df = df[cols]
        df.to_excel('portfolio_snapshots_w.xlsx', index=False)
        print("[SUCCESS] Generated 'portfolio_snapshots_w.xlsx' with multi-strategy flat data.")

# ==========================================
# MAIN EXECUTION
# ==========================================
def main():
    start_date, end_date = "2022-10-01", "2026-03-31"
    initial_cap = 1000000
    
    print("============================================================")
    print(" ZERODHA-STYLE HOLDINGS SNAPSHOT GENERATOR ")
    print("============================================================")
    
    eq, neq, umap, aum_data = load_metadata()
    nifty, etfs = load_data()
    
    portfolios = ["Conservative", "Balanced", "Aggressive"]
    all_snapshots = {}

    for ptype in portfolios:
        print(f"Tracking Zerodha-Holdings for {ptype}...")
        snaps = generate_zerodha_snapshots(start_date, end_date, initial_cap, ptype, nifty, etfs, eq, neq, umap, aum_data)
        all_snapshots[ptype] = snaps
        
    export_to_word(all_snapshots, initial_cap)
    export_to_excel(all_snapshots) 

    print("============================================================")
    print(f"All processing complete. Check your root directory for files.")

if __name__ == "__main__":
    main()
