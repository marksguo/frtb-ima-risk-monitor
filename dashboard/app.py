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
from pipeline.config import ASSETS, TICKERS

RETURNS_CSV = Path(__file__).resolve().parents[1] / "data" / "returns_history.csv.gz"

# Dark palette.
BG = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
GRID = "#21262d"
REGIME_COLORS = {"normal": "#2ecc71", "elevated": "#f1c40f", "stressed": "#e74c3c"}
ZONE_COLORS = {"green": "#2ecc71", "amber": "#f1c40f", "red": "#e74c3c"}
WINDOW = 252

# FRTB risk classes, in book order, for the Scenario Lab shock dropdown.
RISK_CLASSES = list(dict.fromkeys(a["asset_class"] for a in ASSETS.values()))

# The interactive Scenario Lab recomputes risk from the raw return history. That
# matrix and the stored stress-period ES are static per deploy, so cache them on
# first use (the process restarts on each Render redeploy, refreshing the cache).
_DATA_CACHE: dict = {}


def _returns_and_stress():
    """Return (wide_returns, stress_period_es), loading and caching on first call.

    Output: (DataFrame or None, float or None). Returns (None, None) if the
    snapshot has no price_history yet (older deploy), so panels can degrade.
    """
    if "loaded" not in _DATA_CACHE:
        _DATA_CACHE["loaded"] = True
        try:
            _DATA_CACHE["wide"] = _load_returns()
            sp = run_query("SELECT MAX(es_975) AS m FROM daily_risk_metrics")
            _DATA_CACHE["sp"] = (float(sp["m"].iloc[0])
                                 if not sp.empty and sp["m"].iloc[0] is not None
                                 else None)
        except Exception:
            _DATA_CACHE["wide"] = None
            _DATA_CACHE["sp"] = None
    return _DATA_CACHE.get("wide"), _DATA_CACHE.get("sp")


def _load_returns() -> pd.DataFrame:
    """Load the wide return matrix, preferring the committed CSV over the DB."""
    if RETURNS_CSV.exists():
        df = pd.read_csv(RETURNS_CSV)
        df["date"] = pd.to_datetime(df["date"])
        wide = df.pivot(index="date", columns="ticker", values="daily_return")
        wide = wide[[t for t in TICKERS if t in wide.columns]]
        return wide.dropna(how="any").astype(float)
    # Fallback: read straight from price_history (e.g. local Postgres run).
    from pipeline.calculate_risk import load_returns_from_db
    return load_returns_from_db()


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


def scorecard() -> "html.Div":
    """Top band of metric cards: latest value + 1d / 1w / 1m change.

    Inputs:  none.
    Output:  an html.Div row of per-metric cards (or a placeholder note).
    """
    from pipeline.changes import compute_changes

    df = _load_metrics()
    if df.empty:
        return html.Div("No risk metrics yet. Run the pipeline.",
                        style={"color": "#8b949e"})
    result = compute_changes(df)
    as_of = str(result["as_of"])[:10]

    def _delta(label: str, change) -> "html.Div":
        """One '1w +0.4%' line, coloured by direction (red up = more risk)."""
        if change is None:
            txt, color = f"{label} n/a", "#8b949e"
        else:
            pct = change["pct"]
            if abs(pct) < 0.05:
                txt, color = f"{label} flat", "#8b949e"
            elif pct > 0:
                txt, color = f"{label} ▲ +{pct:.1f}%", "#e74c3c"
            else:
                txt, color = f"{label} ▼ {pct:.1f}%", "#2ecc71"
        return html.Div(txt, style={"color": color, "fontSize": "13px"})

    cards = []
    for row in result["rows"]:
        latest = "n/a" if row["latest"] is None else f"{row['latest']:.2%}"
        c = row["changes"]
        cards.append(html.Div(style={**_panel_style, "flex": "1", "minWidth": "150px"},
                              children=[
            html.Div(row["label"], style={"color": "#8b949e", "fontSize": "12px"}),
            html.Div(latest, style={"color": TEXT, "fontSize": "26px",
                                    "fontWeight": "bold", "margin": "2px 0 6px"}),
            _delta("1d", c["1d"]), _delta("1w", c["1w"]), _delta("1m", c["1m"]),
        ]))

    streak = result["regime_streak"]
    regime = result["regime"]
    cards.append(html.Div(style={**_panel_style, "flex": "1", "minWidth": "150px"},
                          children=[
        html.Div("Regime", style={"color": "#8b949e", "fontSize": "12px"}),
        html.Div(regime, style={"color": REGIME_COLORS.get(regime, TEXT),
                                "fontSize": "22px", "fontWeight": "bold",
                                "margin": "2px 0 6px"}),
        html.Div(f"held {streak} trading days",
                 style={"color": "#8b949e", "fontSize": "13px"}),
    ]))

    return html.Div([
        html.Div(f"Period change as of {as_of}  (▲ = risk rose)",
                 style={"color": "#8b949e", "marginBottom": "8px", "fontSize": "13px"}),
        html.Div(cards, style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
    ])


def pla_panel() -> "html.Div":
    """FRTB P&L Attribution panel: traffic-light zone + Spearman trend.

    Inputs:  none.  Output:  an html.Div (zone badge, metrics, trend chart).
    """
    try:
        df = run_query(
            "SELECT as_of_date, spearman, ks_stat, spearman_zone, ks_zone, zone "
            "FROM pla_results ORDER BY as_of_date"
        )
    except Exception:
        # Older snapshot without the pla_results table (pre-deploy); degrade.
        df = pd.DataFrame()
    if df.empty:
        return html.Div("PLA results populate on the next pipeline run.",
                        style={"color": "#8b949e"})
    df["as_of_date"] = pd.to_datetime(df["as_of_date"])
    latest = df.iloc[-1]
    zone = str(latest["zone"])
    zone_meaning = {
        "green": "IMA-eligible",
        "amber": "IMA-eligible, capital surcharge",
        "red": "fails PLA -> Standardised Approach",
    }

    badge = html.Span(zone.upper(), style={
        "backgroundColor": ZONE_COLORS.get(zone, TEXT), "color": "#0d1117",
        "fontWeight": "bold", "padding": "2px 10px", "borderRadius": "4px"})

    fig = go.Figure()
    fig.add_hrect(y0=0.80, y1=1.0, fillcolor="#2ecc71", opacity=0.08, line_width=0)
    fig.add_hrect(y0=0.70, y1=0.80, fillcolor="#f1c40f", opacity=0.08, line_width=0)
    fig.add_hrect(y0=0.0, y1=0.70, fillcolor="#e74c3c", opacity=0.08, line_width=0)
    fig.add_trace(go.Scatter(x=df["as_of_date"], y=df["spearman"],
                             name="Spearman", line=dict(color="#58a6ff", width=2)))
    fig.update_layout(title="PLA Spearman correlation (RTPL vs HPL)",
                      template="plotly_dark", paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      showlegend=False, margin=dict(l=50, r=20, t=50, b=40),
                      yaxis=dict(title="Spearman", gridcolor=GRID, range=[0.5, 1.0]),
                      xaxis=dict(gridcolor=GRID))

    return html.Div([
        html.Div([html.Span("Desk zone: ", style={"color": "#8b949e"}), badge,
                  html.Span(f"  {zone_meaning.get(zone, '')}",
                            style={"color": "#8b949e", "fontSize": "13px"})],
                 style={"marginBottom": "8px"}),
        html.Div(
            f"Spearman {float(latest['spearman']):.3f} ({latest['spearman_zone']})   "
            f"|   KS {float(latest['ks_stat']):.3f} ({latest['ks_zone']})   "
            f"|   as of {latest['as_of_date'].date()}",
            style={"color": TEXT, "fontFamily": "monospace", "fontSize": "13px",
                   "marginBottom": "8px"}),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ])


def scenario_controls() -> "html.Div":
    """The Scenario Lab input widgets (vol shock, risk-class directional shock)."""
    return html.Div([
        html.Div([
            html.Div("Volatility shock (current risk window)",
                     style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Slider(id="sc-vol", min=1.0, max=3.0, step=0.1, value=1.0,
                       marks={1: "1x", 1.5: "1.5x", 2: "2x", 2.5: "2.5x", 3: "3x"}),
        ], style={"marginBottom": "14px"}),
        html.Div([
            html.Div("Instantaneous shock to a risk class",
                     style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Dropdown(id="sc-class", options=[{"label": c, "value": c}
                                                 for c in RISK_CLASSES],
                         value="Emerging market equity", clearable=False,
                         style={"backgroundColor": "#0d1117", "color": "#0d1117"}),
        ], style={"marginBottom": "14px"}),
        html.Div([
            html.Div(id="sc-shock-label",
                     style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Slider(id="sc-shock", min=-0.25, max=0.10, step=0.01, value=-0.10,
                       marks={-0.25: "-25%", -0.1: "-10%", 0: "0", 0.1: "+10%"}),
        ]),
    ])


def _pct(x: float) -> str:
    return f"{x:.2%}"


def render_scenario(vol: float, klass: str, shock: float) -> "html.Div":
    """Compute and render the base-vs-stressed table for a scenario.

    Inputs:  vol - volatility multiplier; klass - risk class to shock;
             shock - instantaneous return shock applied to that class.
    Output:  an html.Div with a before/after metrics table (or a placeholder).
    """
    from pipeline.scenario import scenario_result

    wide, sp = _returns_and_stress()
    if wide is None:
        return html.Div("Scenario Lab needs price history in the snapshot "
                        "(regenerated on the next pipeline run).",
                        style={"color": "#8b949e"})

    shocks = {klass: shock} if shock else None
    res = scenario_result(wide, vol_multiplier=vol, class_shocks=shocks,
                          stress_period_es=sp)
    b, s, d = res["base"], res["stressed"], res["deltas"]

    def _row(label, base_v, stress_v, delta=None, flip=False):
        cells = [
            html.Td(label, style={"color": "#8b949e", "padding": "4px 10px"}),
            html.Td(base_v, style={"color": TEXT, "padding": "4px 10px",
                                   "fontFamily": "monospace"}),
            html.Td(stress_v, style={
                "color": ZONE_COLORS["red"] if flip else TEXT, "padding": "4px 10px",
                "fontFamily": "monospace", "fontWeight": "bold" if flip else "normal"}),
        ]
        if delta is not None:
            up = delta > 1e-9
            color = "#e74c3c" if up else ("#2ecc71" if delta < -1e-9 else "#8b949e")
            arrow = "▲" if up else ("▼" if delta < -1e-9 else "→")
            cells.append(html.Td(f"{arrow} {delta:+.2%}", style={
                "color": color, "padding": "4px 10px", "fontFamily": "monospace"}))
        else:
            cells.append(html.Td("", style={"padding": "4px 10px"}))
        return html.Tr(cells)

    header = html.Tr([html.Th(h, style={"color": "#8b949e", "textAlign": "left",
                                        "padding": "4px 10px"})
                      for h in ["Metric", "Base", "Stressed", "Change"]])
    rows = [
        _row("97.5% ES", _pct(b["es_975"]), _pct(s["es_975"]), d["es_975"]),
        _row("97.5% VaR", _pct(b["var_975"]), _pct(s["var_975"]), d["var_975"]),
        _row("Stressed ES", _pct(b["es_stressed"]), _pct(s["es_stressed"]),
             d["es_stressed"]),
        _row("Liq-adj ES", _pct(b["liquidity_adjusted_es"]),
             _pct(s["liquidity_adjusted_es"]), d["liquidity_adjusted_es"]),
        _row("IMA capital", _pct(b["capital"]["capital"]),
             _pct(s["capital"]["capital"]), d["capital"]),
        _row("Regime", b["volatility_regime"], s["volatility_regime"],
             flip=d["regime_changed"]),
        _row("Capital binding", b["capital"]["binding"], s["capital"]["binding"],
             flip=d["binding_changed"]),
    ]
    notes = []
    if d["regime_changed"]:
        notes.append(f"Regime flips {b['volatility_regime']} -> "
                     f"{s['volatility_regime']}.")
    if d["binding_changed"]:
        notes.append("The binding capital approach switches under stress.")

    return html.Div([
        html.Table([header] + rows, style={"width": "100%", "borderCollapse": "collapse"}),
        html.Div(" ".join(notes), style={"color": "#f1c40f", "fontSize": "13px",
                                         "marginTop": "8px"}) if notes else html.Div(),
    ])


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
# Exposed so a production WSGI server can serve it: gunicorn dashboard.app:server
server = app.server

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
                 "liquidity-horizon scaling, weekly Acerbi-Szekely backtesting, "
                 "P&L attribution, and an interactive scenario lab.",
                 style={"color": "#8b949e", "marginBottom": "16px"}),
        # 24-hour auto refresh.
        dcc.Interval(id="refresh", interval=24 * 60 * 60 * 1000, n_intervals=0),
        html.Div(id="scorecard", style={"marginBottom": "16px"}),
        # Interactive Scenario Lab: controls on the left, live results on the right.
        html.Div([
            html.H3("Scenario Lab — stress the book and watch risk react",
                    style={"color": TEXT, "marginTop": "0"}),
            html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                            "gap": "20px"}, children=[
                scenario_controls(),
                html.Div(id="sc-output"),
            ]),
        ], style={**_panel_style, "marginBottom": "16px"}),
        html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr",
                        "gap": "16px"}, children=[
            html.Div(dcc.Graph(id="es-var"), style=_panel_style),
            html.Div(dcc.Graph(id="regime"), style=_panel_style),
            html.Div(dcc.Graph(id="contrib"), style=_panel_style),
            html.Div([html.H3("Weekly Backtest (last 8)",
                              style={"color": TEXT, "marginTop": "0"}),
                      html.Div(id="backtest")], style=_panel_style),
        ]),
        # FRTB P&L Attribution (PLA) test, full width below the grid.
        html.Div([html.H3("P&L Attribution (PLA) Test",
                          style={"color": TEXT, "marginTop": "0"}),
                  html.Div(id="pla")],
                 style={**_panel_style, "marginTop": "16px"}),
    ])


app.layout = serve_layout


@app.callback(
    Output("scorecard", "children"),
    Output("es-var", "figure"),
    Output("regime", "figure"),
    Output("contrib", "figure"),
    Output("backtest", "children"),
    Output("pla", "children"),
    Input("refresh", "n_intervals"),
)
def refresh_panels(_n):
    """Refresh the scorecard band, the four grid panels, and the PLA panel.

    Inputs:  _n - the Interval tick count (unused).
    Output:  (scorecard, es_var figure, regime figure, contributions figure,
             backtest table, pla panel).
    """
    return (scorecard(), figure_es_var(), figure_regime(),
            figure_contributions(), backtest_table(), pla_panel())


@app.callback(
    Output("sc-shock-label", "children"),
    Input("sc-class", "value"),
    Input("sc-shock", "value"),
)
def _shock_label(klass, shock):
    """Echo the chosen directional shock in words above its slider."""
    return f"{klass}: {shock:+.0%} instantaneous move"


@app.callback(
    Output("sc-output", "children"),
    Input("sc-vol", "value"),
    Input("sc-class", "value"),
    Input("sc-shock", "value"),
)
def _run_scenario(vol, klass, shock):
    """Recompute the scenario live whenever a control changes."""
    return render_scenario(vol, klass, shock)


if __name__ == "__main__":
    # Host/port are configurable via env so the same app serves locally
    # (127.0.0.1) and inside a container (DASH_HOST=0.0.0.0).
    import os
    app.run(
        host=os.getenv("DASH_HOST", "127.0.0.1"),
        port=int(os.getenv("DASH_PORT", "8050")),
        debug=False,
    )
