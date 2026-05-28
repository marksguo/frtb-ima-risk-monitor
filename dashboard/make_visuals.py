"""Render static PNG visuals from the live database for the README / GitHub.

Produces dark-themed images in ../assets/ that mirror the Plotly Dash panels:
a composite 2x2 "dashboard" hero image plus four standalone panels. Run after
the pipeline has populated the database:

    pip install kaleido      # one-time: static-image engine for Plotly
    python dashboard/make_visuals.py

kaleido is only needed to regenerate these images; the app and pipeline do not
require it, so it is intentionally left out of requirements.txt.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import run_query

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ASSETS.mkdir(exist_ok=True)

BG = "#0d1117"
TEXT = "#e6edf3"
GRID = "#21262d"
ES_COLOR = "#58a6ff"
VAR_COLOR = "#f78166"
REGIME_COLORS = {"normal": "#2ecc71", "elevated": "#f1c40f", "stressed": "#e74c3c"}
PASS_COLOR = "#2ecc71"
FAIL_COLOR = "#e74c3c"
WINDOW = 252


def _metrics() -> pd.DataFrame:
    """Load the last 252 days of daily_risk_metrics, ascending by date."""
    df = run_query(
        "SELECT date, var_975, es_975, es_stressed, liquidity_adjusted_es, "
        "volatility_regime FROM daily_risk_metrics ORDER BY date DESC LIMIT :n",
        params={"n": WINDOW},
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _contributions() -> pd.DataFrame:
    """Load the latest date's per-asset ES contributions."""
    as_of = run_query("SELECT MAX(date) AS d FROM asset_risk")["d"].iloc[0]
    return run_query(
        "SELECT ticker, es_contribution, is_nmrf FROM asset_risk WHERE date = :d "
        "ORDER BY es_contribution DESC", params={"d": as_of},
    )


def _backtests() -> pd.DataFrame:
    """Load the last 12 weekly backtests, ascending by week."""
    df = run_query(
        "SELECT week_ending, acerbi_szekely_statistic, pass_fail FROM "
        "backtest_results ORDER BY week_ending DESC LIMIT 12"
    )
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df.sort_values("week_ending").reset_index(drop=True)


def _dark(fig: go.Figure, title: str) -> go.Figure:
    """Apply the shared dark theme to a figure."""
    fig.update_layout(
        title=title, template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT), margin=dict(l=60, r=30, t=70, b=50),
    )
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    return fig


def _add_regime_bands(fig: go.Figure, df: pd.DataFrame,
                      xref: str = "x", yref: str = "y domain") -> None:
    """Shade contiguous volatility-regime runs behind a time series.

    Uses explicit shapes (not add_vrect) with a domain y-reference so the bands
    span the full panel height and attach to the correct subplot axes. xref/yref
    select the target subplot (e.g. 'x2' / 'y2 domain' for the top-right cell).
    """
    regime = df["volatility_regime"].fillna("normal").to_numpy()
    start = 0
    for i in range(1, len(df) + 1):
        if i == len(df) or regime[i] != regime[start]:
            fig.add_shape(
                type="rect", xref=xref, yref=yref,
                x0=df["date"].iloc[start].isoformat(),
                x1=df["date"].iloc[min(i, len(df) - 1)].isoformat(),
                y0=0, y1=1,
                fillcolor=REGIME_COLORS.get(regime[start], "#2ecc71"),
                opacity=0.18, layer="below", line_width=0,
            )
            start = i


def build_individual() -> None:
    """Write the four standalone panel PNGs."""
    m = _metrics()
    c = _contributions()
    b = _backtests()

    # ES vs VaR
    f = go.Figure()
    f.add_trace(go.Scatter(x=m["date"], y=m["es_975"], name="ES 97.5%",
                           line=dict(color=ES_COLOR, width=2)))
    f.add_trace(go.Scatter(x=m["date"], y=m["var_975"], name="VaR 97.5%",
                           line=dict(color=VAR_COLOR, width=2, dash="dot")))
    f.update_layout(legend=dict(orientation="h", y=1.1, x=0))
    _dark(f, "Expected Shortfall vs VaR (last 252 days)")
    f.write_image(str(ASSETS / "es_vs_var.png"), width=900, height=500, scale=2)

    # Regime
    f = go.Figure()
    _add_regime_bands(f, m)
    f.add_trace(go.Scatter(x=m["date"], y=m["es_975"], name="ES 97.5%",
                           line=dict(color=TEXT, width=2)))
    _dark(f, "Volatility Regime (green=normal, amber=elevated, red=stressed)")
    f.update_layout(showlegend=False)
    f.write_image(str(ASSETS / "volatility_regime.png"), width=900, height=500, scale=2)

    # Contributions
    f = go.Figure(go.Bar(x=c["ticker"], y=c["es_contribution"].astype(float),
                         marker_color=ES_COLOR))
    _dark(f, "Per-Asset ES Contribution (latest day)")
    f.write_image(str(ASSETS / "asset_contributions.png"), width=900, height=500, scale=2)

    # Backtest Z2
    colors = [PASS_COLOR if v == "PASS" else FAIL_COLOR for v in b["pass_fail"]]
    f = go.Figure(go.Bar(x=b["week_ending"], y=b["acerbi_szekely_statistic"].astype(float),
                         marker_color=colors))
    f.add_hline(y=-0.2, line_dash="dash", line_color="#8b949e",
                annotation_text="FAIL threshold (-0.2)", annotation_font_color=TEXT)
    _dark(f, "Acerbi-Szekely Z2 by Week (last 12)")
    f.write_image(str(ASSETS / "backtest_z2.png"), width=900, height=500, scale=2)


def build_composite() -> None:
    """Write the 2x2 composite hero image (dashboard.png)."""
    m = _metrics()
    c = _contributions()
    b = _backtests()

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("ES vs VaR (252d)", "Volatility Regime",
                        "Asset ES Contribution", "Backtest Z2 (last 12 weeks)"),
        vertical_spacing=0.13, horizontal_spacing=0.09,
    )

    fig.add_trace(go.Scatter(x=m["date"], y=m["es_975"], name="ES",
                             line=dict(color=ES_COLOR, width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=m["date"], y=m["var_975"], name="VaR",
                             line=dict(color=VAR_COLOR, width=2, dash="dot")), row=1, col=1)

    _add_regime_bands(fig, m, xref="x2", yref="y2 domain")
    fig.add_trace(go.Scatter(x=m["date"], y=m["es_975"], showlegend=False,
                             line=dict(color=TEXT, width=2)), row=1, col=2)

    fig.add_trace(go.Bar(x=c["ticker"], y=c["es_contribution"].astype(float),
                         marker_color=ES_COLOR, showlegend=False), row=2, col=1)

    colors = [PASS_COLOR if v == "PASS" else FAIL_COLOR for v in b["pass_fail"]]
    fig.add_trace(go.Bar(x=b["week_ending"], y=b["acerbi_szekely_statistic"].astype(float),
                         marker_color=colors, showlegend=False), row=2, col=2)
    fig.add_hline(y=-0.2, line_dash="dash", line_color="#8b949e", row=2, col=2)

    fig.update_layout(
        title="FRTB IMA Risk Monitor - Dashboard",
        template="plotly_dark", paper_bgcolor=BG, plot_bgcolor=BG,
        font=dict(color=TEXT), legend=dict(orientation="h", y=1.06, x=0.0),
        margin=dict(l=60, r=40, t=90, b=50),
    )
    fig.update_xaxes(gridcolor=GRID)
    fig.update_yaxes(gridcolor=GRID)
    fig.write_image(str(ASSETS / "dashboard.png"), width=1500, height=950, scale=2)


def main() -> None:
    """Generate all visuals and report the files written."""
    build_individual()
    build_composite()
    written = sorted(p.name for p in ASSETS.glob("*.png"))
    print(f"[make_visuals] Wrote {len(written)} images to assets/: {', '.join(written)}")


if __name__ == "__main__":
    main()
