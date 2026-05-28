"""IMA vs Standardised Approach capital, with the Basel III output floor.

Ties the two approaches together into a single, comparable capital number and
shows which one binds. This mirrors how a real desk's capital is set: the
internal model can lower capital, but only down to a floor of 72.5% of the
Standardised Approach charge.

SIMPLIFICATIONS
  * The IMA capital is approximated as multiplier x liquidity-adjusted *stressed*
    ES. The full IMCC also blends diversified/undiversified ES across liquidity-
    horizon buckets and adds the NMRF Stressed-ES add-on and the Default Risk
    Charge; those are passed in as (defaulted-to-zero) terms here.
  * Figures are illustrative and the IMA and SA charges rest on different bases,
    so treat the comparison as conceptual (which approach binds) rather than an
    exact regulatory capital number.
"""

from __future__ import annotations

# Basel III output floor: standardised-approach charges floor the modelled ones.
OUTPUT_FLOOR = 0.725
# IMA capital multiplier (regulatory minimum is 1.5; supervisors may add-on).
IMA_MULTIPLIER = 1.5


def liquidity_adjusted_stressed_es(es_975: float, es_stressed: float,
                                   liquidity_adjusted_es: float) -> float:
    """Apply the portfolio's liquidity-scaling ratio to the stressed ES.

    Inputs:
        es_975:                97.5% ES (current).
        es_stressed:           stress-calibrated ES.
        liquidity_adjusted_es: liquidity-horizon-adjusted current ES.
    Output:
        Stressed ES scaled by the same liquidity factor (liq_adj_es / es_975).
        Falls back to es_stressed if es_975 is zero.
    """
    if es_975 <= 0:
        return es_stressed
    return es_stressed * (liquidity_adjusted_es / es_975)


def ima_capital(liq_adj_stressed_es: float, multiplier: float = IMA_MULTIPLIER,
                nmrf_ses: float = 0.0, drc: float = 0.0) -> float:
    """Approximate IMA capital charge.

    Inputs:
        liq_adj_stressed_es: liquidity-adjusted stressed ES.
        multiplier:          regulatory capital multiplier (>= 1.5).
        nmrf_ses:            Non-Modellable Risk Factor stressed-ES add-on.
        drc:                 Default Risk Charge.
    Output:
        The IMA capital charge (same units as the ES inputs).
    """
    return multiplier * liq_adj_stressed_es + nmrf_ses + drc


def apply_output_floor(ima: float, sa: float, floor: float = OUTPUT_FLOOR) -> dict:
    """Apply the Basel III output floor and report which approach binds.

    Inputs:
        ima:   IMA capital charge.
        sa:    Standardised Approach capital charge.
        floor: output-floor fraction (0.725 = 72.5%).
    Output:
        dict with ima, sa, floor_value (floor * sa), capital (the binding number),
        and 'binding' naming which approach sets capital.
    """
    floor_value = floor * sa
    capital = max(ima, floor_value)
    return {
        "ima": ima,
        "sa": sa,
        "floor_value": floor_value,
        "capital": capital,
        "binding": "SA output floor" if floor_value >= ima else "Internal model (IMA)",
    }


def capital_comparison(es_975: float, es_stressed: float,
                       liquidity_adjusted_es: float, sa_charge: float,
                       multiplier: float = IMA_MULTIPLIER,
                       floor: float = OUTPUT_FLOOR) -> dict:
    """End-to-end IMA vs SA capital comparison from the daily risk metrics.

    Inputs:
        es_975, es_stressed, liquidity_adjusted_es: portfolio ES metrics.
        sa_charge: Standardised Approach charge (see standardised_approach).
        multiplier, floor: IMA multiplier and output-floor fraction.
    Output:
        The apply_output_floor dict, plus the intermediate
        liq_adj_stressed_es used for the IMA charge.
    """
    laes = liquidity_adjusted_stressed_es(es_975, es_stressed, liquidity_adjusted_es)
    ima = ima_capital(laes, multiplier)
    result = apply_output_floor(ima, sa_charge, floor)
    result["liq_adj_stressed_es"] = laes
    return result
