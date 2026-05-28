"""Comparison VaR / Expected Shortfall estimators.

The production pipeline uses Historical Simulation (the FRTB-aligned, assumption-
free method). This module adds two alternative estimators so the methodology can
be compared head-to-head:

  * parametric (variance-covariance, Normal) - fast and closed-form, but its
    thin Gaussian tail systematically understates tail risk, and
  * Monte Carlo on a fitted Student-t - captures the fat tails that real return
    series exhibit.

The comparison is the point: on the same window, Normal VaR/ES is typically the
smallest, while Historical and Student-t are larger and closer to each other,
illustrating why an assumption-free or fat-tailed measure is preferred for
capital. All functions take a 1-D array of periodic returns (losses negative)
and return VaR and ES as POSITIVE loss magnitudes, matching the rest of the
project's sign convention.
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def historical_var_es(returns: np.ndarray, alpha: float = 0.025) -> tuple[float, float]:
    """Historical Simulation VaR and ES (the production method).

    Inputs:
        returns: 1-D array of periodic returns.
        alpha:   tail probability (0.025 for 97.5%).
    Output:
        (var, es) as positive loss magnitudes: VaR is the negated empirical
        alpha-quantile; ES is the negated mean of returns at or below it.
    """
    returns = np.asarray(returns, dtype=float)
    q = np.quantile(returns, alpha)
    tail = returns[returns <= q]
    es = -tail.mean() if tail.size else -q
    return (-float(q), float(es))


def parametric_var_es(returns: np.ndarray, alpha: float = 0.025) -> tuple[float, float]:
    """Variance-covariance (Normal) VaR and ES.

    Assumes returns are Gaussian. Closed-form and fast, but the thin Normal tail
    understates risk when returns are leptokurtic (which they almost always are).

    Inputs:
        returns: 1-D array of periodic returns.
        alpha:   tail probability.
    Output:
        (var, es) as positive loss magnitudes.
    """
    returns = np.asarray(returns, dtype=float)
    mu = returns.mean()
    sigma = returns.std(ddof=1)
    z = stats.norm.ppf(alpha)
    var = -(mu + sigma * z)
    es = -(mu - sigma * stats.norm.pdf(z) / alpha)
    return (float(var), float(es))


def monte_carlo_t_var_es(returns: np.ndarray, alpha: float = 0.025,
                         n_sims: int = 100_000, seed: int = 42
                         ) -> tuple[float, float]:
    """Monte Carlo VaR and ES from a fitted Student-t distribution.

    Fits a Student-t (heavy-tailed) to the returns, simulates ``n_sims`` draws,
    and reads VaR/ES off the simulated distribution. Captures fat tails that the
    Normal model misses.

    Inputs:
        returns: 1-D array of periodic returns.
        alpha:   tail probability.
        n_sims:  number of Monte Carlo draws.
        seed:    RNG seed for reproducibility.
    Output:
        (var, es) as positive loss magnitudes.
    """
    returns = np.asarray(returns, dtype=float)
    df, loc, scale = stats.t.fit(returns)
    rng = np.random.default_rng(seed)
    sims = stats.t.rvs(df, loc=loc, scale=scale, size=n_sims, random_state=rng)
    q = np.quantile(sims, alpha)
    tail = sims[sims <= q]
    return (-float(q), float(-tail.mean()))


def compare_methods(returns: np.ndarray, alpha: float = 0.025) -> dict[str, dict[str, float]]:
    """Run all three estimators on one window and return their VaR/ES.

    Inputs:
        returns: 1-D array of periodic returns.
        alpha:   tail probability.
    Output:
        dict keyed by method name -> {'var': ..., 'es': ...} (positive losses).
    """
    h_var, h_es = historical_var_es(returns, alpha)
    p_var, p_es = parametric_var_es(returns, alpha)
    m_var, m_es = monte_carlo_t_var_es(returns, alpha)
    return {
        "Historical": {"var": h_var, "es": h_es},
        "Parametric (Normal)": {"var": p_var, "es": p_es},
        "Monte Carlo (Student-t)": {"var": m_var, "es": m_es},
    }
