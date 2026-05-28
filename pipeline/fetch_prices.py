"""Step 1 of the pipeline: fetch daily prices and store returns to PostgreSQL.

Pulls the full adjusted-close history (from config.HISTORY_START to today) for
the six-asset synthetic book via yfinance, computes daily simple returns, and
upserts one row per asset per trading day into the ``price_history`` table.

The full history is (re)loaded on every run and upserted, so the table is the
single source of truth that calculate_risk and nmrf_checker read from. yfinance
is therefore the only network dependency and it is hit exactly once per run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# Allow running this file directly (python pipeline/fetch_prices.py) as well as
# importing it from run_pipeline.py.
sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline.config import HISTORY_START, TICKERS
from database.db_utils import get_engine, upsert_dataframe


def fetch_prices(start: str = HISTORY_START) -> pd.DataFrame:
    """Download adjusted close prices and daily returns for the asset universe.

    Inputs:
        start: ISO date string for the first day of history to request.
    Output:
        A long-format DataFrame with columns
        [date (pd.Timestamp), ticker, adj_close, daily_return],
        sorted by ticker then date, with the first (NaN-return) day per ticker
        dropped. Raises RuntimeError if yfinance returns no data.
    """
    raw = yf.download(
        TICKERS,
        start=start,
        auto_adjust=True,   # 'Close' is split/dividend adjusted
        progress=False,
        group_by="column",
        threads=False,      # avoid yfinance's sqlite tz-cache lock under threads
    )

    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned no data for the requested tickers.")

    # With multiple tickers, columns are a MultiIndex (field, ticker). Pull the
    # adjusted close block as a wide (date x ticker) frame.
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    close = close.dropna(how="all")

    # Force the index name so reset_index() yields a predictable 'date' column
    # regardless of yfinance/pandas version naming.
    close.index.name = "date"
    returns = close.pct_change()
    returns.index.name = "date"

    # Reshape wide -> long and merge price with its return.
    close_long = close.reset_index().melt(
        id_vars="date", var_name="ticker", value_name="adj_close"
    )
    ret_long = returns.reset_index().melt(
        id_vars="date", var_name="ticker", value_name="daily_return"
    )
    merged = close_long.merge(ret_long, on=["date", "ticker"])

    # Drop rows with no price (pre-inception) and the first return per ticker.
    merged = merged.dropna(subset=["adj_close"])
    merged = merged.dropna(subset=["daily_return"])

    merged["date"] = pd.to_datetime(merged["date"])
    merged = merged.sort_values(["ticker", "date"]).reset_index(drop=True)
    return merged[["date", "ticker", "adj_close", "daily_return"]]


def load_prices(df: pd.DataFrame) -> int:
    """Upsert a long-format price/return DataFrame into ``price_history``.

    Inputs:
        df: output of fetch_prices() (columns date, ticker, adj_close,
            daily_return).
    Output:
        The number of rows submitted to the database.
    """
    engine = get_engine()
    try:
        rows = upsert_dataframe(
            df,
            table="price_history",
            conflict_cols=["date", "ticker"],
            update_cols=["adj_close", "daily_return"],
            engine=engine,
        )
    finally:
        engine.dispose()
    return rows


def main() -> None:
    """Run the fetch step: download prices, store them, print a short summary.

    Inputs:  none.
    Output:  None. Side effect: price_history is populated/updated.
    """
    df = fetch_prices()
    rows = load_prices(df)
    latest = df["date"].max().date()
    per_ticker = df.groupby("ticker")["date"].agg(["min", "max", "count"])
    print(f"[fetch_prices] Upserted {rows} rows. Latest date: {latest}.")
    print("[fetch_prices] Per-ticker coverage:")
    print(per_ticker.to_string())


if __name__ == "__main__":
    main()
