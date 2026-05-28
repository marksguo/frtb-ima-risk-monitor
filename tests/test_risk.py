"""Unit tests for the pure risk functions (no database or network required)."""

import numpy as np
import pytest
from scipy import stats

from pipeline.var_methods import (
    historical_var_es, parametric_var_es, monte_carlo_t_var_es, compare_methods,
)
from pipeline.var_backtests import kupiec_pof, christoffersen, breaches_from
from pipeline.calculate_risk import var_es_from_window
from pipeline.backtest import acerbi_szekely_z2
from pipeline.nmrf_checker import classify_nmrf
import pandas as pd


# --------------------------------------------------------------------------
# Historical Simulation VaR / ES
# --------------------------------------------------------------------------
def test_var_es_from_window_hand_computed():
    """Matches a hand-computed VaR/ES on a small, known return array."""
    returns = np.array([-0.08, -0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05])
    var, es = var_es_from_window(returns, alpha=0.1)
    # 0.1-quantile (linear interp) = -0.053 -> VaR; only -0.08 is at/below it.
    assert var == pytest.approx(0.053, abs=1e-6)
    assert es == pytest.approx(0.08, abs=1e-6)


def test_historical_es_ge_var():
    """ES is never smaller than VaR (it is a deeper-tail average)."""
    rng = np.random.default_rng(0)
    returns = rng.normal(0, 0.01, 2000)
    var, es = historical_var_es(returns)
    assert es >= var > 0


# --------------------------------------------------------------------------
# Parametric (Normal) VaR / ES
# --------------------------------------------------------------------------
def test_parametric_matches_normal_theory():
    """On a large standard-normal sample, parametric VaR/ES match closed form."""
    rng = np.random.default_rng(1)
    returns = rng.normal(0, 1, 400_000)
    var, es = parametric_var_es(returns, alpha=0.025)
    z = stats.norm.ppf(0.025)
    assert var == pytest.approx(-z, abs=0.02)                       # ~1.96
    assert es == pytest.approx(stats.norm.pdf(z) / 0.025, abs=0.02)  # ~2.338
    assert es > var


# --------------------------------------------------------------------------
# Method comparison: Normal understates fat tails
# --------------------------------------------------------------------------
def test_normal_understates_fat_tailed_es():
    """On heavy-tailed data, Normal ES < Student-t Monte Carlo ES."""
    rng = np.random.default_rng(2)
    returns = stats.t.rvs(3, loc=0, scale=0.01, size=5000, random_state=rng)
    _, p_es = parametric_var_es(returns)
    _, m_es = monte_carlo_t_var_es(returns)
    assert p_es < m_es


def test_compare_methods_shape():
    rng = np.random.default_rng(3)
    res = compare_methods(rng.normal(0, 0.01, 1000))
    assert set(res) == {"Historical", "Parametric (Normal)", "Monte Carlo (Student-t)"}
    for m in res.values():
        assert m["es"] >= m["var"] > 0


# --------------------------------------------------------------------------
# Acerbi-Szekely Z2
# --------------------------------------------------------------------------
def test_acerbi_szekely_calibrated_near_zero():
    rng = np.random.default_rng(42)
    T, sigma, alpha = 5000, 0.01, 0.025
    R = rng.normal(0, sigma, T)
    z = stats.norm.ppf(alpha)
    var = np.full(T, -z * sigma)
    es = np.full(T, sigma * stats.norm.pdf(z) / alpha)
    assert abs(acerbi_szekely_z2(R, es, var, alpha)) < 0.2  # PASS band


def test_acerbi_szekely_underestimated_es_fails():
    rng = np.random.default_rng(42)
    T, sigma, alpha = 5000, 0.01, 0.025
    R = rng.normal(0, sigma, T)
    z = stats.norm.ppf(alpha)
    var = np.full(T, -z * sigma)
    es_too_small = np.full(T, (sigma * stats.norm.pdf(z) / alpha) / 2)
    assert acerbi_szekely_z2(R, es_too_small, var, alpha) < -0.5  # clear FAIL


# --------------------------------------------------------------------------
# Kupiec POF
# --------------------------------------------------------------------------
def test_kupiec_well_calibrated_not_rejected():
    breaches = np.zeros(2000, dtype=bool)
    breaches[::40] = True  # exactly 2.5% breach rate
    out = kupiec_pof(breaches, alpha=0.025)
    assert out["breach_rate"] == pytest.approx(0.025, abs=1e-6)
    assert out["p_value"] > 0.05


def test_kupiec_too_many_breaches_rejected():
    breaches = np.zeros(2000, dtype=bool)
    breaches[:200] = True  # 10% breach rate, far above 2.5%
    out = kupiec_pof(breaches, alpha=0.025)
    assert out["p_value"] < 0.01


# --------------------------------------------------------------------------
# Christoffersen independence
# --------------------------------------------------------------------------
def test_christoffersen_clustering_rejected():
    breaches = np.zeros(2000, dtype=bool)
    breaches[1000:1020] = True  # 20 consecutive breaches -> clustered
    out = christoffersen(breaches, alpha=0.025)
    assert out["p_value_ind"] < 0.05


def test_christoffersen_returns_valid_probabilities():
    rng = np.random.default_rng(7)
    breaches = rng.random(2000) < 0.025
    out = christoffersen(breaches, alpha=0.025)
    assert 0.0 <= out["p_value_ind"] <= 1.0
    assert 0.0 <= out["p_value_cc"] <= 1.0


def test_breaches_from():
    returns = np.array([-0.05, -0.01, 0.02, -0.03])
    var_pred = np.array([0.02, 0.02, 0.02, 0.02])
    np.testing.assert_array_equal(breaches_from(returns, var_pred),
                                  np.array([True, False, False, True]))


# --------------------------------------------------------------------------
# NMRF classification
# --------------------------------------------------------------------------
def test_nmrf_dense_is_modellable():
    dense = pd.bdate_range("2025-05-27", "2026-05-27")
    is_nmrf, n_obs, max_gap = classify_nmrf(dense)
    assert is_nmrf is False and n_obs > 200 and max_gap <= 4


def test_nmrf_large_gap_flagged():
    dates = pd.bdate_range("2025-05-27", "2026-05-27").delete(slice(100, 135))
    is_nmrf, _, max_gap = classify_nmrf(dates)
    assert is_nmrf is True and max_gap > 30


def test_nmrf_too_few_observations_flagged():
    sparse = pd.to_datetime([f"2025-{m:02d}-01" for m in range(6, 13)]
                            + [f"2026-{m:02d}-01" for m in range(1, 6)])
    is_nmrf, n_obs, _ = classify_nmrf(sparse)
    assert is_nmrf is True and n_obs < 24


def test_nmrf_empty_flagged():
    is_nmrf, n_obs, _ = classify_nmrf(pd.DatetimeIndex([]))
    assert is_nmrf is True and n_obs == 0
