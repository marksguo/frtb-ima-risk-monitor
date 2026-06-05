"""Shared configuration for the FRTB Risk Monitor pipeline.

Centralising the asset universe and risk-model constants here keeps fetch,
calculate_risk, nmrf_checker and backtest in agreement. Changing a liquidity
horizon or the confidence level happens in exactly one place.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Synthetic trading book: 6 ETFs, one per FRTB asset class, with the FRTB
# liquidity horizon (in business days) assigned to each.
# ---------------------------------------------------------------------------
ASSETS: dict[str, dict] = {
    "SPY": {"asset_class": "Large cap equity",      "liquidity_horizon": 10},
    "TLT": {"asset_class": "Interest rates",        "liquidity_horizon": 60},
    "HYG": {"asset_class": "Credit (high yield)",   "liquidity_horizon": 40},
    "EEM": {"asset_class": "Emerging market equity","liquidity_horizon": 20},
    "GLD": {"asset_class": "Commodities",           "liquidity_horizon": 20},
    "UUP": {"asset_class": "FX",                     "liquidity_horizon": 10},
}

TICKERS: list[str] = list(ASSETS.keys())

# Equal notional weights (1/6 each). Portfolio P&L is the weighted mean of
# asset daily returns.
WEIGHT: float = 1.0 / len(ASSETS)
WEIGHTS: dict[str, float] = {ticker: WEIGHT for ticker in ASSETS}

# ---------------------------------------------------------------------------
# Risk-model constants.
# ---------------------------------------------------------------------------
HISTORY_START = "2007-01-01"   # full history for stress-window search
VAR_WINDOW = 252               # rolling lookback (one trading year)
CONFIDENCE = 0.975             # 97.5% confidence
ALPHA = 1.0 - CONFIDENCE       # 0.025 tail probability
VOL_WINDOW = 20                # rolling window for the volatility regime
BASE_LIQUIDITY_HORIZON = 10    # horizon that the sqrt-time scaling is relative to

# Volatility-regime thresholds, expressed as multiples of full-history average
# realised volatility.
REGIME_NORMAL_MAX = 0.8        # current_vol < 0.8 * hist_avg  -> 'normal'
REGIME_ELEVATED_MAX = 1.3      # 0.8..1.3 * hist_avg          -> 'elevated', else 'stressed'

# Acerbi-Szekely backtest threshold: Z2 below this fails.
BACKTEST_FAIL_THRESHOLD = -0.2

# ---------------------------------------------------------------------------
# P&L Attribution (PLA) test (FRTB MAR32.16).
# The risk model is represented by a reduced factor set (the systematic factors
# it retains); RTPL is the P&L explained by those factors, HPL is the full-
# revaluation P&L. The two FRTB metrics over a rolling observation window decide
# the desk's traffic-light zone.
# ---------------------------------------------------------------------------
PLA_OBS_WINDOW = 250                  # observation window (~12 months) in days
PLA_FACTOR_TICKERS = ["SPY", "TLT"]   # systematic risk factors the model keeps
# Spearman correlation zones (higher is better): >= green -> green, >= amber -> amber.
PLA_SPEARMAN_GREEN = 0.80
PLA_SPEARMAN_AMBER = 0.70
# Kolmogorov-Smirnov zones (lower is better): < green -> green, < amber -> amber.
PLA_KS_GREEN = 0.09
PLA_KS_AMBER = 0.12

# ---------------------------------------------------------------------------
# Scenario / stress lab. The interactive dashboard recomputes risk under a
# user-chosen volatility shock (applied to the current risk window) plus
# instantaneous directional shocks by FRTB risk class.
# ---------------------------------------------------------------------------
SCENARIO_STRESS_WINDOW = VAR_WINDOW   # the recent window whose vol is scaled
