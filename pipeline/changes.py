"""Period-over-period change scorecard for the daily risk metrics.

Shared by ``daily_update.py`` (which writes ``CHANGES.md``) and the Dash
dashboard panel so both surfaces report identical day / week / month deltas
off one implementation. All metrics are loss magnitudes stored as positive
fractions (e.g. 0.0156 == 1.56%); a positive change therefore means *more*
risk.
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd

# Metric column -> display label. Order defines scorecard row order.
METRICS = [
    ("es_975", "97.5% ES"),
    ("var_975", "97.5% VaR"),
    ("es_stressed", "Stressed ES"),
    ("liquidity_adjusted_es", "Liq-adj ES"),
]

# Period label -> calendar-day lookback. 1d resolves to the previous trading
# row; 1w / 1m fall back to the latest row on or before the target date.
PERIODS = [("1d", 1), ("1w", 7), ("1m", 30)]


def _value_on_or_before(df: pd.DataFrame, target_date, col: str):
    """Latest value of ``col`` on the most recent date <= target_date, or None."""
    prior = df[df["date"] <= target_date]
    if prior.empty:
        return None
    val = prior.iloc[-1][col]
    return None if pd.isna(val) else float(val)


def _regime_streak(df: pd.DataFrame) -> int:
    """Number of consecutive most-recent rows sharing the latest regime label."""
    regimes = df["volatility_regime"].fillna("unknown").to_numpy()
    latest = regimes[-1]
    streak = 0
    for r in reversed(regimes):
        if r != latest:
            break
        streak += 1
    return streak


def compute_changes(df: pd.DataFrame) -> dict:
    """Compute latest value plus 1d/1w/1m change for each headline metric.

    Inputs:
        df - daily_risk_metrics rows with a datetime 'date' column, the metric
             columns, and 'volatility_regime', sorted ascending by date.
    Output:
        dict with 'as_of' (date), 'regime', 'regime_streak', and 'rows' - a
        list of {key, label, latest, changes} where changes maps each period
        label to {'abs', 'pct'} (percentage-point and percent change) or None
        when no comparison row exists.
    """
    df = df.sort_values("date").reset_index(drop=True)
    if df.empty:
        return {"as_of": None, "regime": None, "regime_streak": 0, "rows": []}

    as_of = df["date"].iloc[-1]
    rows = []
    for col, label in METRICS:
        latest = _value_on_or_before(df, as_of, col)
        changes = {}
        for plabel, days in PERIODS:
            prev = _value_on_or_before(df, as_of - timedelta(days=days), col)
            if latest is None or prev is None or prev == 0:
                changes[plabel] = None
            else:
                changes[plabel] = {
                    "abs": (latest - prev) * 100.0,   # percentage points
                    "pct": (latest - prev) / prev * 100.0,
                }
        rows.append({"key": col, "label": label, "latest": latest, "changes": changes})

    return {
        "as_of": as_of,
        "regime": str(df["volatility_regime"].iloc[-1]),
        "regime_streak": _regime_streak(df),
        "rows": rows,
    }


def arrow_pct(change: dict | None) -> str:
    """Render a change dict as an arrowed percent string (▲ +2.6% / ▼ -1.1%)."""
    if change is None:
        return "n/a"
    pct = change["pct"]
    if abs(pct) < 0.05:
        return "→ flat"
    return f"▲ +{pct:.1f}%" if pct > 0 else f"▼ {pct:.1f}%"


def to_markdown(result: dict) -> str:
    """Render compute_changes() output as the CHANGES.md scorecard."""
    as_of = str(result["as_of"])[:10] if result["as_of"] is not None else "n/a"
    lines = [
        "# FRTB Risk Scorecard",
        "",
        "Auto-generated each trading day by the FRTB IMA Risk Monitor pipeline.",
        "Period-over-period change in the headline risk metrics. All values are",
        "loss magnitudes (higher = more potential loss), so a ▲ means risk rose.",
        "",
        f"**As of {as_of}**",
        "",
        "| Metric | Latest | 1d | 1w | 1m |",
        "|--------|--------|----|----|----|",
    ]
    for row in result["rows"]:
        latest = "n/a" if row["latest"] is None else f"{row['latest']:.2%}"
        c = row["changes"]
        lines.append(
            f"| {row['label']} | {latest} | {arrow_pct(c['1d'])} "
            f"| {arrow_pct(c['1w'])} | {arrow_pct(c['1m'])} |"
        )
    lines.append("")
    if result["regime"]:
        streak = result["regime_streak"]
        unit = "trading day" if streak == 1 else "trading days"
        lines.append(f"Regime: **{result['regime']}** (unchanged for {streak} {unit})")
        lines.append("")
    return "\n".join(lines)
