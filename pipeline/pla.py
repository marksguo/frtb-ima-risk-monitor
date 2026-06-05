"""FRTB P&L Attribution (PLA) test - MAR32.16.

The PLA test is a required IMA-eligibility gate: it checks whether a desk's risk
model explains the same P&L the front-office pricing model produces. Two series
are compared over a rolling ~12-month window:

  * HPL  (Hypothetical P&L)        - full-revaluation P&L holding positions
                                      fixed: here the actual weighted book return.
  * RTPL (Risk-Theoretical P&L)    - P&L explained by the risk factors the model
                                      *retains*. We represent the risk model by a
                                      reduced factor set (equity = SPY, rates =
                                      TLT) and take each asset's factor-explained
                                      return (OLS fit on the window). Credit, EM,
                                      commodity and FX idiosyncratic moves are
                                      only partly spanned, which is the realistic
                                      source of HPL/RTPL divergence.

Two statistics decide the desk's traffic-light zone:

  * Spearman rank correlation of RTPL vs HPL (do they move together?).
  * Kolmogorov-Smirnov statistic between the RTPL and HPL distributions (are the
    distributions close?).

Zone thresholds follow MAR32.16: the desk's zone is the WORSE of the two metric
zones. Green -> IMA-eligible; amber -> eligible with a capital surcharge; red ->
the desk fails PLA and falls back to the Standardised Approach.

SIMPLIFICATION (stated honestly): a real RTPL comes from the risk engine's daily
risk-factor P&L vectors, not an in-window OLS projection. The factor-projection
proxy here reproduces the *mechanism* the test polices (omitted/!approximated
risk factors widen the gap) on a synthetic book, not a bank's exact RTPL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline.config import (
    PLA_FACTOR_TICKERS,
    PLA_KS_AMBER,
    PLA_KS_GREEN,
    PLA_OBS_WINDOW,
    PLA_SPEARMAN_AMBER,
    PLA_SPEARMAN_GREEN,
    WEIGHTS,
)
from database.db_utils import get_engine, run_query, upsert_dataframe

FRIDAY = 4
_ZONE_RANK = {"green": 0, "amber": 1, "red": 2}
# Cap the per-Friday backfill so a re-run stays fast; ~3 years of weekly points
# is plenty for the dashboard trend.
MAX_PLA_FRIDAYS = 156


def factor_pnl(wide_window: pd.DataFrame,
               factor_tickers: list[str] = PLA_FACTOR_TICKERS
               ) -> tuple[pd.Series, pd.Series]:
    """Split a return window into HPL and reduced-factor RTPL portfolio series.

    Inputs:
        wide_window:    wide per-asset return DataFrame over the observation
                        window (date index, one column per ticker).
        factor_tickers: the systematic factors the risk model retains.
    Output:
        (hpl, rtpl) portfolio return Series aligned to wide_window's index.
        hpl is the actual weighted book return; rtpl is the weighted sum of each
        asset's factor-explained (OLS-fitted) return.
    """
    weights = pd.Series(WEIGHTS)
    hpl = (wide_window * weights).sum(axis=1)

    # Design matrix: the retained factors plus an intercept.
    factors = wide_window[factor_tickers].to_numpy()
    design = np.column_stack([np.ones(len(wide_window)), factors])

    rtpl_assets = pd.DataFrame(index=wide_window.index)
    for ticker in wide_window.columns:
        y = wide_window[ticker].to_numpy()
        coefs, *_ = np.linalg.lstsq(design, y, rcond=None)
        rtpl_assets[ticker] = design @ coefs        # factor-explained return

    rtpl = (rtpl_assets * weights).sum(axis=1)
    return hpl, rtpl


def spearman_corr(hpl: pd.Series, rtpl: pd.Series) -> float:
    """Spearman rank correlation between HPL and RTPL (nan-safe)."""
    if len(hpl) < 2:
        return float("nan")
    rho = stats.spearmanr(hpl.to_numpy(), rtpl.to_numpy()).correlation
    return float(rho)


def ks_stat(hpl: pd.Series, rtpl: pd.Series) -> float:
    """Two-sample Kolmogorov-Smirnov statistic between the HPL and RTPL dists."""
    if len(hpl) < 2:
        return float("nan")
    return float(stats.ks_2samp(hpl.to_numpy(), rtpl.to_numpy()).statistic)


def zone_spearman(rho: float) -> str:
    """Map a Spearman correlation to its PLA zone (higher is better)."""
    if rho >= PLA_SPEARMAN_GREEN:
        return "green"
    if rho >= PLA_SPEARMAN_AMBER:
        return "amber"
    return "red"


def zone_ks(ks: float) -> str:
    """Map a KS statistic to its PLA zone (lower is better)."""
    if ks < PLA_KS_GREEN:
        return "green"
    if ks < PLA_KS_AMBER:
        return "amber"
    return "red"


def overall_zone(spearman_zone: str, ks_zone: str) -> str:
    """The desk's zone is the worse of the two metric zones (MAR32.16)."""
    worst = max(_ZONE_RANK[spearman_zone], _ZONE_RANK[ks_zone])
    return {v: k for k, v in _ZONE_RANK.items()}[worst]


def compute_pla(wide_window: pd.DataFrame) -> dict:
    """Run the PLA test on a single observation window.

    Inputs:
        wide_window: wide per-asset return DataFrame over the window.
    Output:
        dict with spearman, ks_stat, spearman_zone, ks_zone, zone, n_obs.
    """
    hpl, rtpl = factor_pnl(wide_window)
    rho = spearman_corr(hpl, rtpl)
    ks = ks_stat(hpl, rtpl)
    s_zone = zone_spearman(rho)
    k_zone = zone_ks(ks)
    return {
        "spearman": round(rho, 6),
        "ks_stat": round(ks, 6),
        "spearman_zone": s_zone,
        "ks_zone": k_zone,
        "zone": overall_zone(s_zone, k_zone),
        "n_obs": int(len(wide_window)),
    }


def compute_pla_history(wide_returns: pd.DataFrame,
                        obs_window: int = PLA_OBS_WINDOW) -> pd.DataFrame:
    """Compute the PLA test for each recent Friday with a full window.

    Inputs:
        wide_returns: full wide per-asset return DataFrame (date index).
        obs_window:   observation window length in trading days.
    Output:
        DataFrame with one row per eligible Friday: [as_of_date, spearman,
        ks_stat, spearman_zone, ks_zone, zone, n_obs], oldest first.
    """
    fridays = wide_returns.index[wide_returns.index.dayofweek == FRIDAY]
    eligible = [f for f in fridays
                if len(wide_returns.loc[:f]) >= obs_window][-MAX_PLA_FRIDAYS:]

    rows = []
    for friday in eligible:
        window = wide_returns.loc[:friday].tail(obs_window)
        result = compute_pla(window)
        result["as_of_date"] = friday
        rows.append(result)

    cols = ["as_of_date", "spearman", "ks_stat", "spearman_zone",
            "ks_zone", "zone", "n_obs"]
    return pd.DataFrame(rows, columns=cols)


def main() -> None:
    """Compute the PLA history from price_history and upsert pla_results."""
    from pipeline.calculate_risk import load_returns_from_db

    wide = load_returns_from_db()
    results = compute_pla_history(wide)
    if results.empty:
        print("[pla] Not enough history for a full PLA window; nothing stored.")
        return

    engine = get_engine()
    try:
        n = upsert_dataframe(
            results, "pla_results",
            conflict_cols=["as_of_date"],
            update_cols=["spearman", "ks_stat", "spearman_zone",
                         "ks_zone", "zone", "n_obs"],
            engine=engine,
        )
    finally:
        engine.dispose()

    latest = results.iloc[-1]
    print(
        f"[pla] As of {pd.to_datetime(latest['as_of_date']).date()} | "
        f"Spearman {latest['spearman']:.3f} ({latest['spearman_zone']}) | "
        f"KS {latest['ks_stat']:.3f} ({latest['ks_zone']}) | "
        f"zone {latest['zone'].upper()}"
    )
    print(f"[pla] Upserted {n} weekly PLA rows "
          f"({results['zone'].value_counts().to_dict()}).")


if __name__ == "__main__":
    main()
