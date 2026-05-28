"""Step 3 of the pipeline: classify Non-Modellable Risk Factors (NMRFs).

Under FRTB, a risk factor is *modellable* only if, over the trailing year, it
has at least 24 real price observations AND no gap between observations longer
than one month. Failing either test makes it *non-modellable* (an NMRF), which
attracts a punitive stressed-capital add-on.

This module applies that test per asset for the latest date and writes the
result into ``asset_risk.is_nmrf``. For the liquid ETFs in this synthetic book
the test will essentially never trigger, which is the correct and expected
outcome: the point is that the classification infrastructure exists, is run
daily, and is documented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline.config import TICKERS
from database.db_utils import get_engine, run_query

# FRTB modellability thresholds (simplified).
MIN_OBS_PER_YEAR = 24
MAX_GAP_DAYS = 30
LOOKBACK_DAYS = 365


def classify_nmrf(obs_dates: pd.DatetimeIndex) -> tuple[bool, int, int]:
    """Decide whether a set of observation dates implies a non-modellable factor.

    Inputs:
        obs_dates: DatetimeIndex of dates on which a real price was observed,
                   already restricted to the trailing lookback year.
    Output:
        (is_nmrf, n_obs, max_gap_days) where is_nmrf is True if there are fewer
        than 24 observations OR any gap between consecutive observations exceeds
        30 calendar days.
    """
    n_obs = len(obs_dates)
    if n_obs == 0:
        return (True, 0, LOOKBACK_DAYS)
    ordered = obs_dates.sort_values()
    gaps = ordered.to_series().diff().dropna().dt.days
    max_gap = int(gaps.max()) if not gaps.empty else 0
    is_nmrf = (n_obs < MIN_OBS_PER_YEAR) or (max_gap > MAX_GAP_DAYS)
    return (is_nmrf, n_obs, max_gap)


def evaluate_assets(as_of: pd.Timestamp, engine=None) -> pd.DataFrame:
    """Run the NMRF test for every ticker as of a given date.

    Inputs:
        as_of:  the date to classify (uses observations in the trailing year).
        engine: optional SQLAlchemy engine; one is created/closed if None.
    Output:
        DataFrame indexed by ticker with columns
        ['is_nmrf', 'n_obs', 'max_gap_days'].
    """
    start = as_of - pd.Timedelta(days=LOOKBACK_DAYS)
    prices = run_query(
        "SELECT date, ticker FROM price_history "
        "WHERE date > :start AND date <= :as_of",
        params={"start": start.date(), "as_of": as_of.date()},
        engine=engine,
    )
    prices["date"] = pd.to_datetime(prices["date"])

    rows = {}
    for ticker in TICKERS:
        obs = pd.DatetimeIndex(prices.loc[prices["ticker"] == ticker, "date"])
        is_nmrf, n_obs, max_gap = classify_nmrf(obs)
        rows[ticker] = {
            "is_nmrf": is_nmrf,
            "n_obs": n_obs,
            "max_gap_days": max_gap,
        }
    return pd.DataFrame.from_dict(rows, orient="index")


def update_flags(as_of: pd.Timestamp, flags: pd.DataFrame, engine=None) -> int:
    """Write is_nmrf flags into asset_risk for the given date.

    Inputs:
        as_of:  the date whose asset_risk rows should be updated.
        flags:  DataFrame indexed by ticker with an 'is_nmrf' column.
        engine: optional SQLAlchemy engine; one is created/closed if None.
    Output:
        The number of (date, ticker) rows updated.
    """
    own_engine = engine is None
    engine = engine or get_engine()
    statement = text(
        "UPDATE asset_risk SET is_nmrf = :is_nmrf "
        "WHERE date = :date AND ticker = :ticker"
    )
    records = [
        {"is_nmrf": bool(row["is_nmrf"]), "date": as_of.date(), "ticker": ticker}
        for ticker, row in flags.iterrows()
    ]
    try:
        with engine.begin() as conn:
            result = conn.execute(statement, records)
    finally:
        if own_engine:
            engine.dispose()
    return result.rowcount if result.rowcount is not None else len(records)


def main() -> None:
    """Run the NMRF classification step for the latest available date.

    Inputs:  none.
    Output:  None. Side effect: asset_risk.is_nmrf flags are updated.
    """
    engine = get_engine()
    try:
        latest = run_query(
            "SELECT MAX(date) AS d FROM price_history", engine=engine
        )["d"].iloc[0]
        if latest is None:
            raise RuntimeError("price_history is empty; run fetch_prices first.")
        as_of = pd.to_datetime(latest)
        flags = evaluate_assets(as_of, engine=engine)
        updated = update_flags(as_of, flags, engine=engine)
    finally:
        engine.dispose()

    n_nmrf = int(flags["is_nmrf"].sum())
    print(f"[nmrf_checker] {as_of.date()} | {n_nmrf}/{len(flags)} assets flagged NMRF "
          f"| updated {updated} asset_risk rows.")
    print(flags.to_string())


if __name__ == "__main__":
    main()
