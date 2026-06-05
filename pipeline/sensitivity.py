"""Sensitivity analysis: marginal / component VaR and a parameter grid.

Where the Scenario Lab asks "what if a big shock hits?", sensitivity analysis
asks the calmer, everyday-desk question: "how does my risk number *respond* to
small changes?" Two views:

  * Marginal & component VaR - how much portfolio VaR moves if you add a little
    to one position (marginal), and how much of today's total VaR each position
    is responsible for (component). Component VaR is additive: the per-asset
    pieces sum back to the portfolio VaR, so it cleanly answers "who owns the
    risk?".

  * Parameter grid - how the headline VaR/ES move as the two main model knobs
    change: the confidence level and the look-back window. This exposes how much
    the number depends on modelling choices rather than the market.

All computations reuse the Historical-Simulation VaR/ES helper and only touch
the trailing window, so they are fast enough to render live on the dashboard.
"""
from __future__ import annotations

import pandas as pd

from pipeline.calculate_risk import var_es_from_window
from pipeline.config import ALPHA, VAR_WINDOW, WEIGHTS


def _portfolio_var(wide_returns: pd.DataFrame, weights: dict[str, float],
                   alpha: float, window: int) -> float:
    """Historical-Simulation VaR of the weighted book over the trailing window."""
    port = (wide_returns * pd.Series(weights)).sum(axis=1).to_numpy()
    var, _ = var_es_from_window(port[-window:], alpha)
    return float(var)


def marginal_component_var(wide_returns: pd.DataFrame,
                           weights: dict[str, float] = WEIGHTS,
                           alpha: float = ALPHA, window: int = VAR_WINDOW,
                           bump: float = 1e-4) -> tuple[pd.DataFrame, float]:
    """Marginal and component VaR per asset.

    Marginal VaR is the numerical derivative of portfolio VaR with respect to a
    small increase in one position's weight (bump-and-recompute). Component VaR
    is weight x marginal VaR; by Euler's theorem for a positively-homogeneous
    risk measure these sum back to the portfolio VaR, so they partition today's
    risk across the book.

    Inputs:
        wide_returns: wide per-asset return DataFrame (date index).
        weights:      portfolio weights per ticker.
        alpha:        tail probability (0.025 for 97.5%).
        window:       trailing look-back length.
        bump:         weight increment used for the numerical derivative.
    Output:
        (DataFrame[ticker, weight, marginal_var, component_var, pct_of_var],
         base_var) sorted by component VaR descending.
    """
    base_var = _portfolio_var(wide_returns, weights, alpha, window)

    rows = []
    for ticker in wide_returns.columns:
        bumped = dict(weights)
        bumped[ticker] = bumped[ticker] + bump
        var_up = _portfolio_var(wide_returns, bumped, alpha, window)
        mvar = (var_up - base_var) / bump
        rows.append({
            "ticker": ticker,
            "weight": weights[ticker],
            "marginal_var": mvar,
            "component_var": weights[ticker] * mvar,
        })

    df = pd.DataFrame(rows)
    total_component = df["component_var"].sum()
    df["pct_of_var"] = df["component_var"] / total_component if total_component else 0.0
    df = df.sort_values("component_var", ascending=False).reset_index(drop=True)
    return df, base_var


def parameter_grid(wide_returns: pd.DataFrame,
                   weights: dict[str, float] = WEIGHTS,
                   confidences: tuple[float, ...] = (0.95, 0.975, 0.99),
                   windows: tuple[int, ...] = (126, 252, 504)) -> pd.DataFrame:
    """VaR and ES across a grid of confidence levels and look-back windows.

    Inputs:
        wide_returns: wide per-asset return DataFrame (date index).
        weights:      portfolio weights per ticker.
        confidences:  confidence levels to evaluate (e.g. 0.95 -> alpha 0.05).
        windows:      trailing look-back lengths in trading days.
    Output:
        DataFrame[window, confidence, var, es] - one row per (window, confidence)
        cell, as positive loss magnitudes.
    """
    port = (wide_returns * pd.Series(weights)).sum(axis=1).to_numpy()
    rows = []
    for window in windows:
        for confidence in confidences:
            var, es = var_es_from_window(port[-window:], 1.0 - confidence)
            rows.append({
                "window": window,
                "confidence": confidence,
                "var": float(var),
                "es": float(es),
            })
    return pd.DataFrame(rows)
