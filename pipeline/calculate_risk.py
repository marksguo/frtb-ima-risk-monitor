"""Step 2 of the pipeline: compute FRTB IMA risk metrics and store them.

Reads the full return history from ``price_history`` and computes, for every
date that has a complete 252-day trailing window:

  * 97.5% Historical Simulation VaR
  * 97.5% Expected Shortfall (ES)
  * stress-calibrated ES
  * liquidity-horizon-adjusted ES (sqrt-of-time scaling per FRTB)
  * volatility-regime label ('normal' / 'elevated' / 'stressed')

The full historical series is recomputed and upserted on every run. This means
the dashboard and the weekly backtest have a rich history from the very first
run instead of waiting for days to accumulate.

SIGN CONVENTION
    VaR and ES are stored as POSITIVE loss magnitudes (e.g. 0.021 means a 2.1%
    loss). The spec's percentile/tail-mean are negative returns; we negate them
    so that (a) the numbers read naturally as losses and (b) the Acerbi-Szekely
    backtest's ``1(R_t < -VaR_t)`` indicator works as written.

COMPONENT ADDITIVITY
    Because portfolio return = sum_i weight_i * return_i, the per-asset ES and
    VaR contributions computed here sum exactly to the portfolio ES and VaR.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline.config import (
    ALPHA,
    ASSETS,
    BASE_LIQUIDITY_HORIZON,
    REGIME_ELEVATED_MAX,
    REGIME_NORMAL_MAX,
    TICKERS,
    VAR_WINDOW,
    VOL_WINDOW,
    WEIGHTS,
)
from database.db_utils import get_engine, run_query, upsert_dataframe


# ---------------------------------------------------------------------------
# Pure numerical helpers (no database access -> unit-testable in isolation).
# ---------------------------------------------------------------------------
def var_es_from_window(window: np.ndarray, alpha: float = ALPHA) -> tuple[float, float]:
    """Compute Historical Simulation VaR and ES from a window of returns.

    Inputs:
        window: 1-D array of periodic returns (signed; losses negative).
        alpha:  tail probability (0.025 for 97.5% confidence).
    Output:
        (var, es) as POSITIVE loss magnitudes. VaR is the negated alpha-quantile
        of returns; ES is the negated mean of all returns at or below that
        quantile (the average loss on the worst alpha-fraction of days).
    """
    if len(window) == 0:
        return (np.nan, np.nan)
    threshold = np.quantile(window, alpha)        # negative return at the tail
    tail = window[window <= threshold]
    var = -threshold
    es = -tail.mean() if tail.size else -threshold
    return (var, es)


def rolling_var_es(returns: pd.Series, window: int = VAR_WINDOW,
                   alpha: float = ALPHA) -> pd.DataFrame:
    """Compute rolling VaR and ES for a return series.

    Inputs:
        returns: pandas Series of periodic returns indexed by date.
        window:  lookback length in observations (252 = one trading year).
        alpha:   tail probability.
    Output:
        DataFrame indexed by date with columns ['var', 'es'] as positive loss
        magnitudes. The first ``window - 1`` rows are NaN (insufficient data).
    """
    var = returns.rolling(window).apply(
        lambda w: -np.quantile(w, alpha), raw=True
    )
    es = returns.rolling(window).apply(
        lambda w: -w[w <= np.quantile(w, alpha)].mean(), raw=True
    )
    return pd.DataFrame({"var": var, "es": es})


def volatility_regime(returns: pd.Series, vol_window: int = VOL_WINDOW
                      ) -> pd.Series:
    """Classify each date into a volatility regime.

    Compares the trailing ``vol_window``-day realised volatility against the
    full-history average of that same rolling volatility.

    Inputs:
        returns:    pandas Series of portfolio returns indexed by date.
        vol_window: rolling window for realised volatility (20 days).
    Output:
        A pandas Series of regime labels ('normal' / 'elevated' / 'stressed')
        aligned to ``returns``. Dates without a full vol window are NaN.
    """
    rolling_vol = returns.rolling(vol_window).std()
    hist_avg = rolling_vol.mean()

    def classify(v: float) -> object:
        if np.isnan(v):
            return np.nan
        if v < REGIME_NORMAL_MAX * hist_avg:
            return "normal"
        if v <= REGIME_ELEVATED_MAX * hist_avg:
            return "elevated"
        return "stressed"

    return rolling_vol.apply(classify)


def liquidity_adjusted_es(asset_returns: pd.DataFrame, window: int = VAR_WINDOW,
                          alpha: float = ALPHA) -> pd.Series:
    """Compute the portfolio liquidity-horizon-adjusted ES series.

    For each asset, ES is scaled by sqrt(liquidity_horizon / base_horizon) and
    weighted by the asset's portfolio weight; the scaled, weighted asset ES
    values are summed.

    Inputs:
        asset_returns: wide DataFrame of per-asset returns (date index, one
                       column per ticker).
        window:        rolling lookback length.
        alpha:         tail probability.
    Output:
        A pandas Series (date index) of the portfolio liquidity-adjusted ES as
        a positive loss magnitude.
    """
    total = None
    for ticker in asset_returns.columns:
        es = asset_returns[ticker].rolling(window).apply(
            lambda w: -w[w <= np.quantile(w, alpha)].mean(), raw=True
        )
        horizon = ASSETS[ticker]["liquidity_horizon"]
        scaled = WEIGHTS[ticker] * es * np.sqrt(horizon / BASE_LIQUIDITY_HORIZON)
        total = scaled if total is None else total + scaled
    return total


def asset_contributions(asset_returns: pd.DataFrame, port_returns: pd.Series,
                        as_of: pd.Timestamp, window: int = VAR_WINDOW,
                        alpha: float = ALPHA) -> pd.DataFrame:
    """Decompose portfolio VaR and ES into additive per-asset contributions.

    On the trailing window ending at ``as_of``: the ES contribution of an asset
    is its weighted mean return over the portfolio's tail days; the VaR
    contribution is its weighted return on the single VaR-scenario day (the
    k-th worst portfolio day, k = floor(alpha * window)). These sum to the
    portfolio ES and VaR respectively.

    Inputs:
        asset_returns: wide per-asset return DataFrame (date index).
        port_returns:  portfolio return Series (date index).
        as_of:         the date to compute contributions for.
        window:        rolling lookback length.
        alpha:         tail probability.
    Output:
        DataFrame indexed by ticker with columns
        ['daily_return', 'var_contribution', 'es_contribution'] (contributions
        are positive loss magnitudes), or empty if the window is incomplete.
    """
    port_window = port_returns.loc[:as_of].tail(window)
    if len(port_window) < window:
        return pd.DataFrame()

    threshold = np.quantile(port_window.to_numpy(), alpha)
    tail_dates = port_window.index[port_window <= threshold]

    # VaR-scenario day: the k-th worst portfolio day in the window.
    k = max(int(np.floor(alpha * window)), 1)
    var_scenario_date = port_window.nsmallest(k).index[-1]

    asset_window = asset_returns.loc[port_window.index]
    rows = {}
    for ticker in asset_returns.columns:
        weight = WEIGHTS[ticker]
        es_contrib = -weight * asset_window.loc[tail_dates, ticker].mean()
        var_contrib = -weight * asset_window.loc[var_scenario_date, ticker]
        rows[ticker] = {
            "daily_return": float(asset_returns.loc[as_of, ticker]),
            "var_contribution": float(var_contrib),
            "es_contribution": float(es_contrib),
        }
    return pd.DataFrame.from_dict(rows, orient="index")


# ---------------------------------------------------------------------------
# Orchestration: compute the full metric set from a wide return matrix.
# ---------------------------------------------------------------------------
def compute_all(wide_returns: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Compute daily portfolio metrics and the latest-date asset breakdown.

    Inputs:
        wide_returns: DataFrame of per-asset returns (date index, one column
                      per ticker) with no missing values (assets aligned).
    Output:
        (daily_df, asset_df, meta) where
          daily_df  : rows for daily_risk_metrics (date, var_975, es_975,
                      es_stressed, liquidity_adjusted_es, volatility_regime),
          asset_df  : rows for asset_risk on the latest date,
          meta      : dict of run-level facts (stress_period_es, latest_date).
    """
    port = (wide_returns * pd.Series(WEIGHTS)).sum(axis=1)
    port.name = "portfolio"

    base = rolling_var_es(port)
    stress_period_es = float(base["es"].max())
    es_stressed = np.maximum(base["es"], 0.5 * stress_period_es + 0.5 * base["es"])
    liq_es = liquidity_adjusted_es(wide_returns)
    regime = volatility_regime(port)

    daily = pd.DataFrame({
        "date": base.index,
        "var_975": base["var"].to_numpy(),
        "es_975": base["es"].to_numpy(),
        "es_stressed": es_stressed.to_numpy(),
        "liquidity_adjusted_es": liq_es.to_numpy(),
        "volatility_regime": regime.to_numpy(),
    })
    # Keep only fully-formed rows (complete 252-day window).
    daily = daily.dropna(subset=["var_975", "es_975"]).reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])
    for col in ["var_975", "es_975", "es_stressed", "liquidity_adjusted_es"]:
        daily[col] = daily[col].astype(float).round(6)

    latest_date = daily["date"].max()
    contrib = asset_contributions(wide_returns, port, latest_date)
    asset_df = pd.DataFrame()
    if not contrib.empty:
        asset_df = contrib.reset_index().rename(columns={"index": "ticker"})
        asset_df["date"] = latest_date
        asset_df["liquidity_horizon"] = asset_df["ticker"].map(
            lambda t: ASSETS[t]["liquidity_horizon"]
        )
        asset_df["is_nmrf"] = False
        for col in ["daily_return", "var_contribution", "es_contribution"]:
            asset_df[col] = asset_df[col].astype(float).round(6)
        asset_df = asset_df[[
            "date", "ticker", "daily_return", "var_contribution",
            "es_contribution", "liquidity_horizon", "is_nmrf",
        ]]

    meta = {
        "stress_period_es": round(stress_period_es, 6),
        "latest_date": latest_date,
        "n_dates": len(daily),
    }
    return daily, asset_df, meta


def load_returns_from_db() -> pd.DataFrame:
    """Read price_history and return an aligned wide per-asset return matrix.

    Inputs:  none (reads the price_history table).
    Output:  wide DataFrame (date index, one column per ticker) of daily
             returns with rows present only where all assets have data.
    """
    df = run_query(
        "SELECT date, ticker, daily_return FROM price_history ORDER BY date"
    )
    if df.empty:
        raise RuntimeError("price_history is empty; run fetch_prices first.")
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot(index="date", columns="ticker", values="daily_return")
    wide = wide[[t for t in TICKERS if t in wide.columns]]
    return wide.dropna(how="any").astype(float)


def main() -> None:
    """Run the risk-calculation step end to end and store results.

    Inputs:  none.
    Output:  None. Side effects: daily_risk_metrics and asset_risk are
             populated/updated; a one-line summary is printed.
    """
    wide = load_returns_from_db()
    daily, asset_df, meta = compute_all(wide)

    engine = get_engine()
    try:
        n_daily = upsert_dataframe(
            daily, "daily_risk_metrics", conflict_cols=["date"], engine=engine
        )
        n_asset = 0
        if not asset_df.empty:
            n_asset = upsert_dataframe(
                asset_df, "asset_risk",
                conflict_cols=["date", "ticker"],
                update_cols=["daily_return", "var_contribution",
                             "es_contribution", "liquidity_horizon"],
                engine=engine,
            )
    finally:
        engine.dispose()

    latest = daily.iloc[-1]
    print(
        f"[calculate_risk] {meta['latest_date'].date()} | "
        f"VaR {latest['var_975']:.4f} | ES {latest['es_975']:.4f} | "
        f"Stressed ES {latest['es_stressed']:.4f} | "
        f"LiqAdj ES {latest['liquidity_adjusted_es']:.4f} | "
        f"Regime {latest['volatility_regime']}"
    )
    print(
        f"[calculate_risk] Upserted {n_daily} daily rows, {n_asset} asset rows. "
        f"Stress-period ES {meta['stress_period_es']:.4f}."
    )


if __name__ == "__main__":
    main()
