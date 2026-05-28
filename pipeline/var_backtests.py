"""Classical VaR backtests: Kupiec POF and Christoffersen.

These complement the Acerbi-Szekely ES test in backtest.py. Where Acerbi-Szekely
tests the *magnitude* of tail losses against predicted ES, these tests examine
the *VaR exceedance sequence*:

  * Kupiec POF (proportion of failures) - is the observed breach rate consistent
    with the model's stated coverage (here 2.5%)? Unconditional coverage.
  * Christoffersen independence - are breaches independent across time, or do
    they cluster (a sign the model fails to react to volatility)?
  * Conditional coverage - the joint test (correct rate AND independent), the
    sum of the two statistics above.

All three are likelihood-ratio tests; the statistic is asymptotically chi-square
and a small p-value rejects the model. Inputs are a boolean breach sequence
(True where the realised loss exceeded VaR). ``scipy.special.xlogy`` is used so
the 0*log(0)=0 boundary cases are handled cleanly.
"""

from __future__ import annotations

import numpy as np
from scipy import stats
from scipy.special import xlogy


def kupiec_pof(breaches: np.ndarray, alpha: float = 0.025) -> dict[str, float]:
    """Kupiec proportion-of-failures (unconditional coverage) test.

    Inputs:
        breaches: boolean array, True where the loss exceeded VaR.
        alpha:    the model's expected breach probability (0.025).
    Output:
        dict with x (breaches), T (obs), breach_rate, LR_pof, p_value.
        A small p_value rejects the hypothesis that the true breach rate = alpha.
    """
    breaches = np.asarray(breaches, dtype=bool)
    T = breaches.size
    x = int(breaches.sum())
    pi_hat = x / T if T else np.nan
    ll_null = xlogy(T - x, 1 - alpha) + xlogy(x, alpha)
    ll_alt = xlogy(T - x, 1 - pi_hat) + xlogy(x, pi_hat)
    lr = -2.0 * (ll_null - ll_alt)
    return {
        "x": x, "T": T, "breach_rate": float(pi_hat),
        "LR_pof": float(lr), "p_value": float(stats.chi2.sf(lr, 1)),
    }


def _transition_counts(breaches: np.ndarray) -> tuple[int, int, int, int]:
    """Count consecutive-state transitions in the breach sequence.

    Inputs:  breaches - boolean array.
    Output:  (n00, n01, n10, n11) transition counts between no-breach (0) and
             breach (1) states.
    """
    prev = breaches[:-1].astype(int)
    curr = breaches[1:].astype(int)
    n00 = int(np.sum((prev == 0) & (curr == 0)))
    n01 = int(np.sum((prev == 0) & (curr == 1)))
    n10 = int(np.sum((prev == 1) & (curr == 0)))
    n11 = int(np.sum((prev == 1) & (curr == 1)))
    return n00, n01, n10, n11


def christoffersen(breaches: np.ndarray, alpha: float = 0.025) -> dict[str, float]:
    """Christoffersen independence and conditional-coverage tests.

    Inputs:
        breaches: boolean array, True where the loss exceeded VaR.
        alpha:    the model's expected breach probability (for conditional
                  coverage, combined with Kupiec POF).
    Output:
        dict with LR_ind, p_value_ind (independence) and LR_cc, p_value_cc
        (conditional coverage = POF + independence, chi-square 2 df). Small
        p-values reject independence / correct conditional coverage.
    """
    breaches = np.asarray(breaches, dtype=bool)
    n00, n01, n10, n11 = _transition_counts(breaches)

    pi01 = n01 / (n00 + n01) if (n00 + n01) else 0.0
    pi11 = n11 / (n10 + n11) if (n10 + n11) else 0.0
    pi = (n01 + n11) / (n00 + n01 + n10 + n11) if breaches.size > 1 else 0.0

    ll_null = xlogy(n00 + n10, 1 - pi) + xlogy(n01 + n11, pi)
    ll_alt = (xlogy(n00, 1 - pi01) + xlogy(n01, pi01)
              + xlogy(n10, 1 - pi11) + xlogy(n11, pi11))
    lr_ind = -2.0 * (ll_null - ll_alt)

    lr_pof = kupiec_pof(breaches, alpha)["LR_pof"]
    lr_cc = lr_pof + lr_ind
    return {
        "LR_ind": float(lr_ind), "p_value_ind": float(stats.chi2.sf(lr_ind, 1)),
        "LR_cc": float(lr_cc), "p_value_cc": float(stats.chi2.sf(lr_cc, 2)),
    }


def breaches_from(returns: np.ndarray, var_pred: np.ndarray) -> np.ndarray:
    """Build the VaR breach sequence from realised returns and predicted VaR.

    Inputs:
        returns:  realised periodic returns (losses negative).
        var_pred: predicted VaR as positive loss magnitudes, aligned to returns.
    Output:
        Boolean array, True where the realised loss exceeded VaR (R < -VaR).
    """
    returns = np.asarray(returns, dtype=float)
    var_pred = np.asarray(var_pred, dtype=float)
    return returns < -var_pred
