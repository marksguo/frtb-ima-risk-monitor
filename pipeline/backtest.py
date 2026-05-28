"""Step 4 of the pipeline: weekly Acerbi-Szekely ES backtest.

The Acerbi-Szekely Z2 statistic tests whether predicted Expected Shortfall is
consistent with realised returns on the days that breached VaR:

    Z2 = (1 / (T * alpha)) * sum_t [ R_t / ES_t * 1(R_t < -VaR_t) ] + 1

where T is the number of days in the test window (252), alpha = 0.025, R_t is
the realised portfolio return, and ES_t / VaR_t are the *predicted* loss
magnitudes for day t. A well-calibrated model gives Z2 near 0; a materially
negative Z2 means ES is too small (risk underestimated). We fail at Z2 < -0.2.

NO LOOK-AHEAD
    The prediction for day t uses information available before day t, so we use
    the previous trading day's stored VaR/ES (a one-day shift) as the forecast
    that is then tested against day t's realised return.

The backtest is meant to run every Friday. On each run it backfills every Friday
in history that has a full 252-day window, so the dashboard shows a full weekly
history immediately and re-runs stay idempotent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from pipeline.config import ALPHA, BACKTEST_FAIL_THRESHOLD, VAR_WINDOW, WEIGHTS
from database.db_utils import get_engine, run_query, upsert_dataframe

FRIDAY = 4  # pandas Timestamp.dayofweek: Monday=0 ... Friday=4


def acerbi_szekely_z2(returns: np.ndarray, es_pred: np.ndarray,
                      var_pred: np.ndarray, alpha: float = ALPHA) -> float:
    """Compute the Acerbi-Szekely Z2 statistic over an aligned window.

    Inputs:
        returns:  realised portfolio returns R_t (signed).
        es_pred:  predicted ES for each day (positive loss magnitudes).
        var_pred: predicted VaR for each day (positive loss magnitudes).
        alpha:    tail probability (0.025).
    Output:
        The Z2 statistic as a float. Near 0 means well calibrated; materially
        negative means ES underestimates risk.
    """
    T = len(returns)
    if T == 0:
        return float("nan")
    breach = returns < -var_pred
    summand = np.where(breach, returns / es_pred, 0.0)
    return float(summand.sum() / (T * alpha) + 1.0)


def _portfolio_returns() -> pd.Series:
    """Build the realised portfolio return series from price_history.

    Inputs:  none.
    Output:  pandas Series of portfolio returns indexed by date (assets aligned,
             equal-weighted).
    """
    df = run_query("SELECT date, ticker, daily_return FROM price_history ORDER BY date")
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot(index="date", columns="ticker", values="daily_return").dropna(how="any")
    return (wide * pd.Series(WEIGHTS)).sum(axis=1)


def compute_backtests() -> pd.DataFrame:
    """Compute the weekly backtest for every eligible Friday in history.

    Joins realised portfolio returns with the stored daily VaR/ES predictions
    (shifted one day to avoid look-ahead), then for each Friday with a full
    252-day trailing window computes Z2, the week's VaR exceptions, the regime,
    and a PASS/FAIL verdict.

    Inputs:  none.
    Output:  DataFrame with columns
             [week_ending, exceptions_count, acerbi_szekely_statistic,
              pass_fail, regime], one row per eligible Friday.
    """
    metrics = run_query(
        "SELECT date, var_975, es_975, volatility_regime FROM daily_risk_metrics "
        "ORDER BY date"
    )
    if metrics.empty:
        raise RuntimeError("daily_risk_metrics is empty; run calculate_risk first.")
    metrics["date"] = pd.to_datetime(metrics["date"])
    metrics = metrics.set_index("date")

    port = _portfolio_returns()

    df = pd.DataFrame({"R": port})
    df["var_pred"] = metrics["var_975"].shift(1)
    df["es_pred"] = metrics["es_975"].shift(1)
    df["regime"] = metrics["volatility_regime"]
    df = df.dropna(subset=["R", "var_pred", "es_pred"])

    results = []
    fridays = df.index[df.index.dayofweek == FRIDAY]
    for friday in fridays:
        window = df.loc[:friday].tail(VAR_WINDOW)
        if len(window) < VAR_WINDOW:
            continue
        z2 = acerbi_szekely_z2(
            window["R"].to_numpy(),
            window["es_pred"].to_numpy(),
            window["var_pred"].to_numpy(),
        )
        # Exceptions "that week": breaches in the trailing 7 calendar days.
        week = df.loc[friday - pd.Timedelta(days=6): friday]
        exceptions = int((week["R"] < -week["var_pred"]).sum())
        verdict = "FAIL" if z2 < BACKTEST_FAIL_THRESHOLD else "PASS"
        results.append({
            "week_ending": friday,
            "exceptions_count": exceptions,
            "acerbi_szekely_statistic": round(z2, 6),
            "pass_fail": verdict,
            "regime": df.loc[friday, "regime"],
        })

    return pd.DataFrame(results)


def main() -> None:
    """Run the weekly backtest backfill and store results.

    Inputs:  none.
    Output:  None. Side effect: backtest_results is populated/updated; the most
             recent week's verdict is printed.
    """
    results = compute_backtests()
    engine = get_engine()
    try:
        n = upsert_dataframe(
            results, "backtest_results",
            conflict_cols=["week_ending"],
            update_cols=["exceptions_count", "acerbi_szekely_statistic",
                         "pass_fail", "regime"],
            engine=engine,
        )
    finally:
        engine.dispose()

    latest = results.iloc[-1]
    print(
        f"[backtest] Week ending {latest['week_ending'].date()} | "
        f"Z2 {latest['acerbi_szekely_statistic']:.4f} | "
        f"{latest['pass_fail']} | exceptions {latest['exceptions_count']} | "
        f"regime {latest['regime']}"
    )
    print(f"[backtest] Upserted {n} weekly results "
          f"({results['pass_fail'].value_counts().to_dict()}).")


if __name__ == "__main__":
    main()
