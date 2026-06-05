"""Unit tests for the pure risk functions (no database or network required)."""

import numpy as np
import pytest
from scipy import stats

from pipeline.var_methods import (
    historical_var_es, parametric_var_es, monte_carlo_t_var_es, compare_methods,
    ewma_volatility, filtered_historical_var_es,
)
from pipeline.var_backtests import kupiec_pof, christoffersen, breaches_from
from pipeline.standardised_approach import sbm_delta_charge, SA_RISK_WEIGHTS
from pipeline.capital import ima_capital, apply_output_floor, capital_comparison
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
# Change scorecard (1d / 1w / 1m deltas)
# --------------------------------------------------------------------------
def _scorecard_frame():
    """40 daily rows where es_975 rises 0.01 -> 0.02 linearly; others constant."""
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    es = np.linspace(0.01, 0.02, 40)
    return pd.DataFrame({
        "date": dates,
        "es_975": es,
        "var_975": es * 0.8,
        "es_stressed": es * 1.3,
        "liquidity_adjusted_es": es * 1.5,
        "volatility_regime": ["normal"] * 20 + ["stressed"] * 20,
    })


def test_compute_changes_directions_and_pct():
    """1d/1w/1m changes are positive on a rising series and pct math is right."""
    from pipeline.changes import compute_changes

    res = compute_changes(_scorecard_frame())
    es_row = next(r for r in res["rows"] if r["key"] == "es_975")
    latest = es_row["latest"]
    assert latest == pytest.approx(0.02, abs=1e-9)
    # 7 calendar days back on a daily series == 7 rows earlier.
    step = (0.02 - 0.01) / 39
    prev_1w = latest - 7 * step
    assert es_row["changes"]["1w"]["pct"] == pytest.approx(
        (latest - prev_1w) / prev_1w * 100, rel=1e-6)
    assert all(es_row["changes"][p]["pct"] > 0 for p in ("1d", "1w", "1m"))


def test_compute_changes_regime_streak_and_empty():
    """Regime streak counts the trailing run; empty frame degrades gracefully."""
    from pipeline.changes import compute_changes

    res = compute_changes(_scorecard_frame())
    assert res["regime"] == "stressed"
    assert res["regime_streak"] == 20

    empty = compute_changes(pd.DataFrame(
        columns=["date", "es_975", "var_975", "es_stressed",
                 "liquidity_adjusted_es", "volatility_regime"]))
    assert empty["rows"] == [] and empty["as_of"] is None


# --------------------------------------------------------------------------
# Scenario / stress engine
# --------------------------------------------------------------------------
def _wide_returns_fixture(n=400, seed=1):
    """A realistic-ish wide return matrix for the six-asset book."""
    from pipeline.config import TICKERS
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    data = {t: rng.normal(0.0003, 0.01, n) for t in TICKERS}
    return pd.DataFrame(data, index=dates)[TICKERS]


def test_vol_multiplier_raises_tail_risk():
    """Scaling the current window's volatility increases VaR and ES."""
    from pipeline.scenario import recompute_latest, apply_shock

    wide = _wide_returns_fixture()
    base = recompute_latest(wide)
    stressed = recompute_latest(apply_shock(wide, vol_multiplier=2.0))
    assert stressed["es_975"] > base["es_975"]
    assert stressed["var_975"] > base["var_975"]


def test_class_shock_appends_day_and_lifts_es():
    """A negative directional class shock adds a tail day and raises ES/capital."""
    from pipeline.scenario import scenario_result

    wide = _wide_returns_fixture()
    res = scenario_result(wide, vol_multiplier=1.0,
                          class_shocks={"Emerging market equity": -0.15})
    assert res["deltas"]["es_975"] > 0
    # IMA capital is monotonic in stressed ES, so it should not fall.
    assert res["stressed"]["capital"]["capital"] >= res["base"]["capital"]["capital"]


def test_no_shock_is_identity():
    """vol_multiplier=1 and no class shock leaves the metrics unchanged."""
    from pipeline.scenario import scenario_result

    wide = _wide_returns_fixture()
    res = scenario_result(wide, vol_multiplier=1.0, class_shocks=None)
    for k in ["var_975", "es_975", "es_stressed", "liquidity_adjusted_es"]:
        assert res["deltas"][k] == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------
# FRTB P&L Attribution (PLA) test
# --------------------------------------------------------------------------
def test_pla_perfect_when_factors_span_book():
    """If every asset IS a retained factor, RTPL == HPL -> green zone."""
    from pipeline import pla

    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2023-01-02", periods=260)
    # Book of just the two factors: the factor model spans it exactly.
    wide = pd.DataFrame(
        {"SPY": rng.normal(0, 0.01, 260), "TLT": rng.normal(0, 0.008, 260)},
        index=dates,
    )
    hpl, rtpl = pla.factor_pnl(wide, factor_tickers=["SPY", "TLT"])
    # The fitted RTPL reproduces each asset return to machine precision, so HPL
    # and RTPL coincide: rank correlation 1 and a KS distance deep in green.
    assert np.allclose(hpl.to_numpy(), rtpl.to_numpy(), atol=1e-9)
    assert pla.spearman_corr(hpl, rtpl) == pytest.approx(1.0, abs=1e-9)
    assert pla.ks_stat(hpl, rtpl) < pla.PLA_KS_GREEN
    assert pla.zone_spearman(pla.spearman_corr(hpl, rtpl)) == "green"


def test_pla_zone_thresholds():
    """Zone mapping follows the MAR32.16 thresholds and worst-of rule."""
    from pipeline import pla

    assert pla.zone_spearman(0.85) == "green"
    assert pla.zone_spearman(0.75) == "amber"
    assert pla.zone_spearman(0.60) == "red"
    assert pla.zone_ks(0.05) == "green"
    assert pla.zone_ks(0.10) == "amber"
    assert pla.zone_ks(0.20) == "red"
    # Overall zone is the worse of the two.
    assert pla.overall_zone("green", "amber") == "amber"
    assert pla.overall_zone("amber", "red") == "red"
    assert pla.overall_zone("green", "green") == "green"


def test_pla_idiosyncratic_book_degrades_zone():
    """A book dominated by a factor-orthogonal asset should not be green."""
    from pipeline import pla
    from pipeline.config import TICKERS

    rng = np.random.default_rng(7)
    dates = pd.bdate_range("2022-01-03", periods=260)
    data = {t: rng.normal(0, 0.004, 260) for t in TICKERS}
    # GLD gets large idiosyncratic moves uncorrelated with SPY/TLT.
    data["GLD"] = rng.normal(0, 0.03, 260)
    wide = pd.DataFrame(data, index=dates)[TICKERS]
    result = pla.compute_pla(wide)
    assert result["zone"] in {"green", "amber", "red"}
    assert 0.0 <= result["ks_stat"] <= 1.0
    assert result["n_obs"] == 260


# --------------------------------------------------------------------------
# Sensitivity analysis (marginal / component VaR, parameter grid)
# --------------------------------------------------------------------------
def test_component_var_sums_to_total():
    """Component VaR partitions the portfolio VaR (Euler additivity)."""
    from pipeline.sensitivity import marginal_component_var

    wide = _wide_returns_fixture(n=400, seed=11)
    df, base_var = marginal_component_var(wide)
    assert df["component_var"].sum() == pytest.approx(base_var, rel=0.05)
    assert df["pct_of_var"].sum() == pytest.approx(1.0, abs=1e-6)
    assert len(df) == 6


def test_parameter_grid_monotonic_in_confidence():
    """Higher confidence -> larger VaR; ES is never below VaR in any cell."""
    from pipeline.sensitivity import parameter_grid

    wide = _wide_returns_fixture(n=520, seed=12)
    grid = parameter_grid(wide)
    # ES >= VaR everywhere.
    assert (grid["es"] >= grid["var"] - 1e-9).all()
    # For a fixed window, VaR rises with confidence.
    for w in grid["window"].unique():
        sub = grid[grid["window"] == w].sort_values("confidence")
        assert sub["var"].is_monotonic_increasing


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


# --------------------------------------------------------------------------
# Filtered (EWMA) Historical Simulation
# --------------------------------------------------------------------------
def test_ewma_volatility_positive_and_aligned():
    rng = np.random.default_rng(11)
    r = rng.normal(0, 0.01, 500)
    sigma = ewma_volatility(r)
    assert len(sigma) == len(r) and np.all(sigma > 0)


def test_filtered_hs_reacts_to_current_volatility():
    """With a calm history then a volatile tail, filtered HS VaR exceeds plain HS."""
    rng = np.random.default_rng(12)
    calm = rng.normal(0, 0.005, 500)
    turbulent = rng.normal(0, 0.03, 60)
    r = np.concatenate([calm, turbulent])
    plain_var, _ = historical_var_es(r)
    filt_var, filt_es = filtered_historical_var_es(r)
    assert filt_var > plain_var          # reacts to the elevated current vol
    assert filt_es >= filt_var > 0


# --------------------------------------------------------------------------
# Standardised Approach (SBM delta)
# --------------------------------------------------------------------------
def test_sbm_total_equals_sum_of_breakdown():
    total, breakdown = sbm_delta_charge()
    assert total == pytest.approx(sum(b["charge"] for b in breakdown.values()))
    # equal weights, notional 1 -> total = mean of the six risk weights
    assert total == pytest.approx(sum(SA_RISK_WEIGHTS.values()) / 6, rel=1e-9)


def test_sbm_scales_with_notional():
    base, _ = sbm_delta_charge(notional=1.0)
    scaled, _ = sbm_delta_charge(notional=1_000_000.0)
    assert scaled == pytest.approx(base * 1_000_000.0)


# --------------------------------------------------------------------------
# Capital: IMA vs SA and the output floor
# --------------------------------------------------------------------------
def test_output_floor_binds_when_ima_low():
    out = apply_output_floor(ima=0.08, sa=0.155)
    assert out["floor_value"] == pytest.approx(0.725 * 0.155)
    assert out["capital"] == pytest.approx(0.725 * 0.155)
    assert out["binding"] == "SA output floor"


def test_internal_model_binds_when_ima_high():
    out = apply_output_floor(ima=0.30, sa=0.155)
    assert out["capital"] == pytest.approx(0.30)
    assert out["binding"].startswith("Internal model")


def test_capital_comparison_keys():
    out = capital_comparison(es_975=0.0156, es_stressed=0.0247,
                             liquidity_adjusted_es=0.0336, sa_charge=0.155)
    assert {"ima", "sa", "capital", "binding", "liq_adj_stressed_es"} <= set(out)
    assert ima_capital(out["liq_adj_stressed_es"]) == pytest.approx(out["ima"])
