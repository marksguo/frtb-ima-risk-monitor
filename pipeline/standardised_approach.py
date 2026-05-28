"""Simplified FRTB Standardised Approach (SBM) capital charge.

Real banks compute capital under BOTH the Internal Models Approach (IMA, the
rest of this project) and the Standardised Approach (SA); the SA acts as a
benchmark and, via the Basel III output floor, a floor on the IMA charge. This
module implements a deliberately simplified Sensitivities-Based Method (SBM)
delta charge so the two can be compared.

SCOPE AND SIMPLIFICATIONS (stated honestly)
  * Delta risk only. The six holdings are linear ETF positions with no
    optionality, so vega and curvature charges are zero by construction.
  * Each position is mapped to its FRTB risk class and given a single
    representative delta risk weight (see SA_RISK_WEIGHTS). The full framework
    uses per-vertex / per-tenor weights for rates and credit; those are out of
    scope here and noted as future work.
  * One position per risk class, so each risk-class charge reduces to
    |risk weight x sensitivity|, and the overall SBM charge is their simple sum
    (FRTB sums risk-class charges - there is no cross-class diversification).
"""

from __future__ import annotations

from pipeline.config import ASSETS, WEIGHTS

# Representative FRTB SBM delta risk weights by risk class (illustrative; the
# real schedule is per-bucket/per-vertex and recalibrated periodically).
SA_RISK_WEIGHTS: dict[str, float] = {
    "Large cap equity": 0.15,
    "Interest rates": 0.10,
    "Credit (high yield)": 0.08,
    "Emerging market equity": 0.25,
    "Commodities": 0.20,
    "FX": 0.15,
}


def sbm_delta_charge(weights: dict[str, float] = WEIGHTS,
                     notional: float = 1.0) -> tuple[float, dict[str, dict]]:
    """Compute the simplified SBM delta capital charge for the book.

    Inputs:
        weights:  portfolio weights per ticker (default: equal weights).
        notional: total portfolio notional. Charges scale linearly with it.
    Output:
        (total_charge, breakdown) where total_charge is the SA capital charge in
        the same units as notional (a fraction of notional when notional=1), and
        breakdown maps each ticker to its risk_class, risk_weight, sensitivity,
        and charge.
    """
    breakdown: dict[str, dict] = {}
    total = 0.0
    for ticker, weight in weights.items():
        asset_class = ASSETS[ticker]["asset_class"]
        rw = SA_RISK_WEIGHTS[asset_class]
        sensitivity = weight * notional          # linear position: s = position value
        charge = abs(rw * sensitivity)           # weighted sensitivity
        breakdown[ticker] = {
            "risk_class": asset_class,
            "risk_weight": rw,
            "sensitivity": sensitivity,
            "charge": charge,
        }
        total += charge
    return total, breakdown
