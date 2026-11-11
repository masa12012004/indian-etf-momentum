# Local data requirements

Run from the repository root. Filenames below match constants in the supplied scripts; change the constants if you use other paths.

| File | Expected content |
|---|---|
| `ETF_OHLCV.xlsx` | One worksheet per ETF symbol, with `Date`, `Open`, `High`, `Low`, `Close`, `Volume` columns |
| `nifty50_ohlcv.csv` | NIFTY benchmark daily OHLCV using the same column names |
| `equity_etfs.csv` | Equity ETF metadata with `SYMBOL`; `UNDERLYING ASSET` supplies theme labels where present |
| `gold_etf.csv` | Gold ETF metadata with `SYMBOL`; also read by the downloader |
| `non_equity_etfs.csv` | Non-equity ETF metadata with `SYMBOL` |
| `aum.csv` | Optional AUM metadata; relevant scripts locate columns containing `SYMBOL` and `AUM` |
| `MW-ETF-22-May-2026-NoSilver.csv` | Downloader universe with a `SYMBOL` column; the filename is a legacy default |

Use chronological daily observations with parseable dates and numeric OHLCV values. Ensure sufficient lookback history for the momentum and 200-day regime calculations. Missing classification inputs can change portfolio construction even if a script continues without an error.

`update_data.py` appends `.NS` to ETF symbols and requests adjusted daily history. Its benchmark is `^NSEI`. It writes timezone-naive normalized dates and rounds prices to two decimals. Check the effects of adjustment and rounding against your intended research assumptions. Excel sheet names are limited to 31 characters; ensure symbols remain unique after truncation.

The downloader does not create the classification or universe lists. Supply them from an authorized source and document the effective membership dates. The date embedded in the default universe filename does not establish point-in-time validity.

A minimal illustrative classification file has this shape (replace the dummy values with your authorized metadata):

```csv
SYMBOL,UNDERLYING ASSET
EXAMPLEETF,Example equity index
```

No actual market dataset is bundled. Do not publish downloaded prices or metadata without checking redistribution terms. Generated files, account state and reports should remain local.
