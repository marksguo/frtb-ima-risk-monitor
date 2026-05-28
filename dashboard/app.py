"""FRTB IMA Risk Monitor - Plotly Dash dashboard.

A single-page, dark "quant terminal" dashboard with four panels:

  1. ES vs VaR over the last 252 trading days (dual line).
  2. Volatility regime: ES line over a regime-coloured background
     (green = normal, amber = elevated, red = stressed).
  3. Today's per-asset ES contribution (bar).
  4. Last 8 weekly Acerbi-Szekely backtest results (PASS green / FAIL red).

Data is read live from PostgreSQL on every refresh. The page refreshes itself
every 24 hours via a dcc.Interval. Run with: python dashboard/app.py -> :8050.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, dash_table, dcc, html

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import run_query

# Dark palette.
BG = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
GRID = "#21262d"
REGIME_COLORS = {"normal": "#2ecc71", "elevated": "#f1c40f", "stressed": "#e74c3c"}
WINDOW = 252


def _empty_fig(message: str) -> go.Figure:
    """Return a dark-themed placeholder figure with a centred message.

    Inputs:  message - text to display.
    Output:  a plotly Figure.
    """
    fig = go.Figure()
    fig.add_annotation(text=message, showarrow=False,
                       font=dict(color=TEXT, size=14), x=0.5, y=0.5, xref="paper",
                       yref="paper")
    fig.update_layout(template="plotly_dark", paper_bgcolor=PANEL,
                      plot_bgcolor=PANEL, margin=dict(l=40, r=20, t=50, b=40))
    return fig


def _load_metrics() -> pd.DataFrame:
    """Load the most recent WINDOW days of daily_risk_metrics.

    Inputs:  none.
    Output:  DataFrame sorted ascending by date (may be empty).
    """
    df = run_query(
        "SELECT date, var_975, es_975, es_stressed, liquidity_adjusted_es, "
        "volatility_regime FROM daily_risk_metrics ORDER BY date DESC LIMIT :n",
        params={"n": WINDOW},
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def figure_es_var() -> go.Figure:
    """Panel 1: ES vs VaR dual line over the last 252 days.

    Inputs:  none.  Output:  a plotly Figure.
    """
    df = _load_metrics()
    if df.empty:
        return _empty_fig("No risk metrics yet. Run the pipeline.")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["es_975"], name="ES 97.5%",
                             line=dict(color="#58a6ff", width=2)))
    fig.add_trace(go.Scatter(x=df["date"], y=df["var_975"], name="VaR 97.5%",
                             line=dict(color="#f78166", width=2, dash="dot")))
    fig.update_layout(title="ES vs VaR (last 252 days)", template="plotly_dark",
                      paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      legend=dict(orientation="h", y=1.12, x=0),
                      margin=dict(l=50, r=20, t=60, b=40),
                      yaxis=dict(title="loss magnitude", gridcolor=GRID),
                      xaxis=dict(gridcolor=GRID))
    return fig


def figure_regime() -> go.Figure:
    """Panel 2: ES over a regime-coloured background.

    Inputs:  none.  Output:  a plotly Figure.
    """
    df = _load_metrics()
    if df.empty:
        return _empty_fig("No risk metrics yet. Run the pipeline.")
    fig = go.Figure()
    # Shade contiguous regime runs.
    regime = df["volatility_regime"].fillna("normal").to_numpy()
    start = 0
    for i in range(1, len(df) + 1):
        if i == len(df) or regime[i] != regime[start]:
            fig.add_vrect(
                x0=df["date"].iloc[start],
                x1=df["date"].iloc[min(i, len(df) - 1)],
                fillcolor=REGIME_COLORS.get(regime[start], "#2ecc71"),
                opacity=0.15, layer="below", line_width=0,
            )
            start = i
    fig.add_trace(go.Scatter(x=df["date"], y=df["es_975"], name="ES 97.5%",
                             line=dict(color=TEXT, width=2)))
    fig.update_layout(title="Volatility Regime (ES overlay)", template="plotly_dark",
                      paper_bgcolor=PANEL, plot_bgcolor=PANEL, showlegend=False,
                      margin=dict(l=50, r=20, t=60, b=40),
                      yaxis=dict(title="ES", gridcolor=GRID),
                      xaxis=dict(gridcolor=GRID))
    return fig


def figure_contributions() -> go.Figure:
    """Panel 3: today's per-asset ES contribution bar chart.

    Inputs:  none.  Output:  a plotly Figure.
    """
    latest = run_query("SELECT MAX(date) AS d FROM asset_risk")
    if latest.empty or latest["d"].iloc[0] is None:
        return _empty_fig("No asset risk data yet. Run the pipeline.")
    as_of = latest["d"].iloc[0]
    df = run_query(
        "SELECT ticker, es_contribution, is_nmrf FROM asset_risk WHERE date = :d "
        "ORDER BY es_contribution DESC", params={"d": as_of},
    )
    colors = ["#e74c3c" if nm else "#58a6ff" for nm in df["is_nmrf"]]
    fig = go.Figure(go.Bar(x=df["ticker"], y=df["es_contribution"].astype(float),
                           marker_color=colors))
    fig.update_layout(title=f"Asset ES Contribution ({as_of})",
                      template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      margin=dict(l=50, r=20, t=60, b=40),
                      yaxis=dict(title="ES contribution", gridcolor=GRID),
                      xaxis=dict(gridcolor=GRID))
    return fig


def backtest_table():
    """Panel 4: last 8 weekly backtest results with PASS/FAIL colouring.

    Inputs:  none.
    Output:  a configured dash_table.DataTable.
    """
    df = run_query(
        "SELECT week_ending, exceptions_count, acerbi_szekely_statistic, "
        "pass_fail, regime FROM backtest_results ORDER BY week_ending DESC LIMIT 8"
    )
    if not df.empty:
        df["week_ending"] = pd.to_datetime(df["week_ending"]).dt.date.astype(str)
    return dash_table.DataTable(
        data=df.to_dict("records"),
        columns=[
            {"name": "Week Ending", "id": "week_ending"},
            {"name": "Exceptions", "id": "exceptions_count"},
            {"name": "Z2", "id": "acerbi_szekely_statistic"},
            {"name": "Result", "id": "pass_fail"},
            {"name": "Regime", "id": "regime"},
        ],
        style_header={"backgroundColor": GRID, "color": TEXT, "fontWeight": "bold"},
        style_cell={"backgroundColor": PANEL, "color": TEXT, "border": f"1px solid {GRID}",
                    "textAlign": "center", "padding": "6px", "fontFamily": "monospace"},
        style_data_conditional=[
            {"if": {"filter_query": '{pass_fail} = "PASS"', "column_id": "pass_fail"},
             "color": "#2ecc71", "fontWeight": "bold"},
            {"if": {"filter_query": '{pass_fail} = "FAIL"', "column_id": "pass_fail"},
             "color": "#e74c3c", "fontWeight": "bold"},
        ],
    )


app = Dash(__name__)
app.title = "FRTB IMA Risk Monitor"

_panel_style = {"backgroundColor": PANEL, "borderRadius": "8px", "padding": "8px"}


def serve_layout() -> html.Div:
    """Build the page layout fresh on each load.

    Inputs:  none.  Output:  the root html.Div.
    """
    return html.Div(style={"backgroundColor": BG, "minHeight": "100vh",
                           "padding": "20px", "fontFamily": "monospace"}, children=[
        html.H1("FRTB IMA Risk Monitor",
                style={"color": TEXT, "marginBottom": "4px"}),
        html.Div("Historical-simulation VaR / Expected Shortfall, stress calibration, "
                 "liquidity-horizon scaling, and weekly Acerbi-Szekely backtesting.",
                 style={"color": "#8b949e", "marginBottom": "16px"}),
        # 24-hour auto refresh.
        dcc.Interval(id="refresh", interval=24 * 60 * 60 * 1000, n_intervals=0),
        html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                        "gap": "16px"}, children=[
            html.Div(dcc.Graph(id="es-var"), style=_panel_style),
            html.Div(dcc.Graph(id="regime"), style=_panel_style),
            html.Div(dcc.Graph(id="contrib"), style=_panel_style),
            html.Div([html.H3("Weekly Backtest (last 8)",
                              style={"color": TEXT, "marginTop": "0"}),
                      html.Div(id="backtest")], style=_panel_style),
        ]),
    ])


app.layout = serve_layout


@app.callback(
    Output("es-var", "figure"),
    Output("regime", "figure"),
    Output("contrib", "figure"),
    Output("backtest", "children"),
    Input("refresh", "n_intervals"),
)
def refresh_panels(_n):
    """Refresh all four panels from the database.

    Inputs:  _n - the Interval tick count (unused).
    Output:  (es_var figure, regime figure, contributions figure, backtest table).
    """
    return figure_es_var(), figure_regime(), figure_contributions(), backtest_table()


if __name__ == "__main__":
    # Host/port are configurable via env so the same app serves locally
    # (127.0.0.1) and inside a container (DASH_HOST=0.0.0.0).
    import os
    app.run(
        host=os.getenv("DASH_HOST", "127.0.0.1"),
        port=int(os.getenv("DASH_PORT", "8050")),
        debug=False,
    )
