"""Interactive scenario / stress engine for the FRTB risk lab.

Given the historical per-asset return matrix and a user-chosen shock, this
recomputes the headline FRTB metrics (VaR, ES, stressed ES, liquidity-adjusted
ES, volatility regime) and the IMA-vs-SA capital outcome, so the dashboard can
show a live before/after. All the risk math is reused from ``calculate_risk``
and ``capital`` - this module only builds the stressed return matrix and reads
off the latest date.

The shock has two independent parts, mirroring how desks stress a book:

  * ``vol_multiplier`` scales the dispersion of the *current risk window*
    (the trailing VAR_WINDOW days) around each asset's mean. Scaling only the
    recent window - not the whole history - means realised vol rises relative to
    the long-run average, so both tail risk (VaR/ES) and the regime classifier
    respond. A uniform all-history scale would cancel out of the regime ratio.

  * ``class_shocks`` applies an instantaneous directional move to every asset in
    a given FRTB risk class, appended as one hypothetical scenario day. This is
    the classic "add a stress scenario to the historical set" overlay: the new
    day enters the tail and pulls VaR/ES toward the shock.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.calculate_risk import (
    rolling_var_es,
    var_es_from_window,
)
from pipeline.capital import capital_comparison
from pipeline.config import (
    ALPHA,
    ASSETS,
    BASE_LIQUIDITY_HORIZON,
    REGIME_ELEVATED_MAX,
    REGIME_NORMAL_MAX,
    SCENARIO_STRESS_WINDOW,
    VAR_WINDOW,
    VOL_WINDOW,
    WEIGHTS,
)
from pipeline.standardised_approach import sbm_delta_charge


def apply_shock(wide_returns: pd.DataFrame, vol_multiplier: float = 1.0,
                class_shocks: dict[str, float] | None = None,
                stress_window: int = SCENARIO_STRESS_WINDOW) -> pd.DataFrame:
    """Return a stressed copy of the per-asset return matrix.

    Inputs:
        wide_returns:   wide per-asset return DataFrame (date index, one column
                        per ticker), sorted ascending by date.
        vol_multiplier: factor (>= 0) scaling the dispersion of the trailing
                        ``stress_window`` days around each asset's window mean.
                        1.0 leaves volatility unchanged.
        class_shocks:   optional {FRTB asset_class -> return shock} applied as a
                        single appended scenario day (e.g. {'Emerging market
                        equity': -0.10}). Classes omitted move 0 that day.
        stress_window:  number of trailing days whose volatility is scaled.
    Output:
        A new DataFrame (original is not mutated). When class_shocks is given it
        has one extra trailing row, the hypothetical scenario day.
    """
    stressed = wide_returns.copy()

    if vol_multiplier != 1.0 and len(stressed) > 0:
        n = min(stress_window, len(stressed))
        tail_idx = stressed.index[-n:]
        sub = stressed.loc[tail_idx]
        stressed.loc[tail_idx] = sub.mean() + vol_multiplier * (sub - sub.mean())

    if class_shocks:
        shock_row = {ticker: 0.0 for ticker in stressed.columns}
        for asset_class, magnitude in class_shocks.items():
            for ticker in stressed.columns:
                if ASSETS[ticker]["asset_class"] == asset_class:
                    shock_row[ticker] = magnitude
        # Append the scenario as the next calendar day so it joins the tail.
        new_date = stressed.index[-1] + pd.Timedelta(days=1)
        stressed.loc[new_date] = pd.Series(shock_row)

    return stressed


def _latest_liq_adj_es(wide_returns: pd.DataFrame, window: int) -> float:
    """Liquidity-adjusted ES on the trailing window only (fast, latest date)."""
    total = 0.0
    for ticker in wide_returns.columns:
        w = wide_returns[ticker].to_numpy()[-window:]
        _, es = var_es_from_window(w)
        horizon = ASSETS[ticker]["liquidity_horizon"]
        total += WEIGHTS[ticker] * es * np.sqrt(horizon / BASE_LIQUIDITY_HORIZON)
    return float(total)


def _latest_regime(port: pd.Series) -> str:
    """Classify the latest date's volatility regime (vectorised rolling std)."""
    rolling_vol = port.rolling(VOL_WINDOW).std()
    hist_avg = rolling_vol.mean()
    v = rolling_vol.iloc[-1]
    if np.isnan(v) or np.isnan(hist_avg):
        return "unknown"
    if v < REGIME_NORMAL_MAX * hist_avg:
        return "normal"
    if v <= REGIME_ELEVATED_MAX * hist_avg:
        return "elevated"
    return "stressed"


def recompute_latest(wide_returns: pd.DataFrame,
                     stress_period_es: float | None = None) -> dict:
    """Compute the latest-date FRTB metrics and capital for a return matrix.

    Only the latest date is evaluated (trailing-window quantiles), so this is
    fast enough to drive an interactive panel even on a full price history.

    Inputs:
        wide_returns:     wide per-asset return DataFrame (date index).
        stress_period_es: the historical max ES used to floor the stressed ES.
                          When None it is computed from the full series (correct
                          but O(history)); the dashboard passes the pipeline's
                          stored value so a recompute is sub-second.
    Output:
        dict with var_975, es_975, es_stressed, liquidity_adjusted_es,
        volatility_regime, and a nested 'capital' dict (ima, sa, floor_value,
        capital, binding, liq_adj_stressed_es).
    """
    port = (wide_returns * pd.Series(WEIGHTS)).sum(axis=1)
    window = min(VAR_WINDOW, len(port))
    if window == 0:
        raise ValueError("Not enough history to compute VaR/ES.")

    var_975, es_975 = (float(x) for x in
                       var_es_from_window(port.to_numpy()[-window:], ALPHA))
    if stress_period_es is None:
        stress_period_es = float(rolling_var_es(port)["es"].max())
    es_stressed = float(max(es_975, 0.5 * stress_period_es + 0.5 * es_975))
    liq_es = _latest_liq_adj_es(wide_returns, window)
    regime_label = _latest_regime(port)

    sa_charge, _ = sbm_delta_charge()
    cap = capital_comparison(es_975, es_stressed, liq_es, sa_charge)

    return {
        "var_975": var_975,
        "es_975": es_975,
        "es_stressed": es_stressed,
        "liquidity_adjusted_es": liq_es,
        "volatility_regime": regime_label,
        "capital": cap,
    }


def scenario_result(wide_returns: pd.DataFrame, vol_multiplier: float = 1.0,
                    class_shocks: dict[str, float] | None = None,
                    stress_period_es: float | None = None) -> dict:
    """Compute base vs stressed metrics and their deltas for a scenario.

    Inputs:
        wide_returns:     wide per-asset return DataFrame (date index).
        vol_multiplier:   volatility scale for the current risk window.
        class_shocks:     {asset_class -> instantaneous return shock}.
        stress_period_es: historical max ES (see recompute_latest); pass the
                          stored value from the dashboard for a fast recompute.
    Output:
        dict with 'base' and 'stressed' metric dicts (as recompute_latest) and
        'deltas' giving the absolute change in each headline metric plus the
        IMA capital change and whether the binding approach flipped.
    """
    base = recompute_latest(wide_returns, stress_period_es)
    stressed = recompute_latest(
        apply_shock(wide_returns, vol_multiplier, class_shocks), stress_period_es
    )

    keys = ["var_975", "es_975", "es_stressed", "liquidity_adjusted_es"]
    deltas = {k: stressed[k] - base[k] for k in keys}
    deltas["capital"] = stressed["capital"]["capital"] - base["capital"]["capital"]
    deltas["regime_changed"] = (
        base["volatility_regime"] != stressed["volatility_regime"]
    )
    deltas["binding_changed"] = (
        base["capital"]["binding"] != stressed["capital"]["binding"]
    )
    return {"base": base, "stressed": stressed, "deltas": deltas}
