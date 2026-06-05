"""FRTB IMA Risk Monitor - Plotly Dash dashboard.

A single-page, dark "quant terminal" dashboard organised into three zones:

  * Today's Risk      - the change scorecard, ES vs VaR, the regime overlay, and
                        per-asset ES contributions.
  * Explore the Model - the interactive Scenario Lab (live stress testing) and
                        the sensitivity panel (marginal/component VaR + a grid).
  * Model Validation  - the FRTB P&L Attribution test and the weekly backtest.

Styling lives in assets/styles.css (Dash auto-loads it). Data is read live from
the database on every refresh; the page refreshes itself every 24 hours via a
dcc.Interval. Run with: python dashboard/app.py -> :8050.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from dash import Dash, Input, Output, dash_table, dcc, html

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import run_query
from pipeline.config import ASSETS, TICKERS

RETURNS_CSV = Path(__file__).resolve().parents[1] / "data" / "returns_history.csv"
REPO_URL = "https://github.com/marksguo/frtb-ima-risk-monitor"

# Dark palette (GitHub-dark inspired).
BG = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
MUTED = "#8b949e"
BORDER = "#30363d"
ACCENT = "#58a6ff"
GRID = "#21262d"
REGIME_COLORS = {"normal": "#2ecc71", "elevated": "#f1c40f", "stressed": "#e74c3c"}
ZONE_COLORS = {"green": "#2ecc71", "amber": "#f1c40f", "red": "#e74c3c"}
WINDOW = 252
FONT = "Inter, system-ui, sans-serif"

# Make every Plotly chart inherit the dashboard's font for a cohesive look.
pio.templates.default = "plotly_dark"
pio.templates["plotly_dark"].layout.font.family = FONT

# FRTB risk classes, in book order, for the Scenario Lab shock dropdown.
RISK_CLASSES = list(dict.fromkeys(a["asset_class"] for a in ASSETS.values()))

# Plain-English labels so the dropdown reads to a non-specialist. Each FRTB risk
# class maps to the single ETF that represents it in this book.
CLASS_LABELS = {
    "Large cap equity": "US stocks (SPY)",
    "Interest rates": "Long-term US bonds (TLT)",
    "Credit (high yield)": "Risky corporate bonds (HYG)",
    "Emerging market equity": "Emerging-market stocks (EEM)",
    "Commodities": "Gold (GLD)",
    "FX": "US dollar (UUP)",
}

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
                      legend=dict(orientation="h", y=1.14, x=1, xanchor="right"),
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
        cards.append(html.Div(className="card metric-card",
                              style={"flex": "1", "minWidth": "150px"}, children=[
            html.Div(row["label"], style={"color": MUTED, "fontSize": "12px",
                                          "textTransform": "uppercase",
                                          "letterSpacing": ".04em"}),
            html.Div(latest, className="mono", style={"color": TEXT, "fontSize": "28px",
                                    "fontWeight": "600", "margin": "4px 0 8px"}),
            _delta("1d", c["1d"]), _delta("1w", c["1w"]), _delta("1m", c["1m"]),
        ]))

    streak = result["regime_streak"]
    regime = result["regime"]
    cards.append(html.Div(className="card metric-card",
                          style={"flex": "1", "minWidth": "150px"}, children=[
        html.Div("Regime", style={"color": MUTED, "fontSize": "12px",
                                  "textTransform": "uppercase", "letterSpacing": ".04em"}),
        html.Div(regime.capitalize(), style={"color": REGIME_COLORS.get(regime, TEXT),
                                "fontSize": "24px", "fontWeight": "700",
                                "margin": "4px 0 8px"}),
        html.Div(f"held {streak} trading days",
                 style={"color": MUTED, "fontSize": "13px"}),
    ]))

    return html.Div([
        html.Div(f"Day / week / month change as of {as_of}   (▲ = risk rose, ▼ = risk fell)",
                 style={"color": MUTED, "marginBottom": "10px", "fontSize": "13px"}),
        html.Div(cards, style={"display": "flex", "gap": "14px", "flexWrap": "wrap"}),
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
    label_style = {"color": MUTED, "fontSize": "12.5px", "fontWeight": "500",
                   "marginBottom": "10px"}
    return html.Div([
        html.Div("Build a scenario", className="card-title"),
        html.Div([
            html.Div(id="sc-vol-label", style=label_style),
            dcc.Slider(id="sc-vol", min=1.0, max=3.0, step=0.1, value=1.0,
                       marks={1: "1x", 1.5: "1.5x", 2: "2x", 2.5: "2.5x", 3: "3x"}),
        ], style={"marginBottom": "22px"}),
        html.Div([
            html.Div("2. Pick one thing to shock", style=label_style),
            dcc.Dropdown(id="sc-class", className="dash-dropdown",
                         options=[{"label": CLASS_LABELS.get(c, c), "value": c}
                                  for c in RISK_CLASSES],
                         value="Emerging market equity", clearable=False),
        ], style={"marginBottom": "22px"}),
        html.Div([
            html.Div(id="sc-shock-label", style=label_style),
            dcc.Slider(id="sc-shock", min=-0.25, max=0.10, step=0.01, value=-0.10,
                       marks={-0.25: "-25%", -0.1: "-10%", 0: "0", 0.1: "+10%"}),
        ]),
        html.Div("Move any control to recompute risk instantly.",
                 style={"color": MUTED, "fontSize": "12px", "marginTop": "18px",
                        "fontStyle": "italic"}),
    ])


def _scenario_help() -> "html.Div":
    """A plain-language 'how to use this' box for the Scenario Lab."""
    step = {"color": MUTED, "fontSize": "13px", "margin": "3px 0"}
    head = {"color": MUTED, "fontSize": "11.5px", "fontWeight": "600",
            "textTransform": "uppercase", "letterSpacing": ".05em",
            "margin": "10px 0 4px"}
    return html.Div([
        html.Div("A what-if tool. You invent a market shock and every risk number "
                 "updates instantly. None of this is a forecast. It just answers "
                 "\"if this happened, how bad would today's risk look?\"",
                 style={"color": TEXT, "fontSize": "13px"}),
        html.Div("How to use it", style=head),
        html.Div("1. Drag the volatility slider to make the market jumpier than "
                 "today (2x means twice as choppy).", style=step),
        html.Div("2. Pick one risk class to shock, for example emerging-market equity.",
                 style=step),
        html.Div("3. Set how far it moves on the spot. Negative is a drop, so -12% "
                 "is a sudden 12% fall.", style=step),
        html.Div("Reading the result", style=head),
        html.Div("The table shows each number as it is today, then under your "
                 "scenario with an arrow for how much it moved (red up means more "
                 "risk). When the shock is large enough the Market regime flips "
                 "toward \"stressed\" and the Capital set by row switches, which is "
                 "the model tipping into crisis mode.",
                 style={"color": MUTED, "fontSize": "13px"}),
    ], style={"backgroundColor": BG, "border": f"1px solid {BORDER}",
              "borderRadius": "8px", "padding": "13px 15px", "marginBottom": "18px"})


def _pct(x: float) -> str:
    return f"{x:.2%}"


def _change_chip(base_v: float, scen_v: float) -> "html.Span":
    """A small coloured 'how much it changed vs today' chip for a numeric row."""
    rel = (scen_v - base_v) / base_v * 100 if base_v else 0.0
    if abs(rel) < 0.5:
        return html.Span("no change", style={"color": MUTED, "fontSize": "12px",
                                             "marginLeft": "10px"})
    up = rel > 0
    color = ZONE_COLORS["red"] if up else ZONE_COLORS["green"]
    arrow = "▲" if up else "▼"
    return html.Span(f"{arrow} {abs(rel):.0f}%", style={
        "color": color, "fontSize": "12.5px", "fontWeight": "600",
        "marginLeft": "10px"})


def render_scenario(vol: float, klass: str, shock: float) -> "html.Div":
    """Render a Today-vs-Scenario table where each scenario value carries an
    inline change arrow showing how far it moved from today.

    Inputs:  vol - volatility multiplier; klass - risk class to shock;
             shock - instantaneous return shock applied to that class.
    Output:  an html.Div with the metrics table (or a placeholder).
    """
    from pipeline.scenario import scenario_result

    wide, sp = _returns_and_stress()
    if wide is None:
        return html.Div("Scenario Lab needs the return history "
                        "(ships on the next daily update).",
                        style={"color": MUTED})

    shocks = {klass: shock} if shock else None
    res = scenario_result(wide, vol_multiplier=vol, class_shocks=shocks,
                          stress_period_es=sp)
    b, s, d = res["base"], res["stressed"], res["deltas"]
    no_shock = (vol == 1.0 and not shocks)

    label_td = {"color": MUTED, "padding": "6px 10px"}
    today_td = {"color": TEXT, "padding": "6px 10px",
                "fontFamily": "'JetBrains Mono', monospace"}
    scen_td = {"padding": "6px 10px", "fontFamily": "'JetBrains Mono', monospace"}

    def _num_row(label, base_v, scen_v):
        return html.Tr([
            html.Td(label, style=label_td),
            html.Td(_pct(base_v), style=today_td),
            html.Td([html.Span(_pct(scen_v), style={"color": TEXT}),
                     _change_chip(base_v, scen_v)], style=scen_td),
        ])

    def _cat_row(label, today_label, scen_label, changed, color):
        chip = (html.Span("changed", style={"color": ZONE_COLORS["red"],
                "fontSize": "12px", "marginLeft": "10px", "fontWeight": "600"})
                if changed else
                html.Span("no change", style={"color": MUTED, "fontSize": "12px",
                "marginLeft": "10px"}))
        return html.Tr([
            html.Td(label, style=label_td),
            html.Td(today_label, style=today_td),
            html.Td([html.Span(scen_label, style={"color": color}), chip],
                    style=scen_td),
        ])

    header = html.Tr([html.Th(h) for h in
                      ["Metric", "Today", "Under your scenario"]])
    rows = [
        _num_row("Expected Shortfall (97.5%)", b["es_975"], s["es_975"]),
        _num_row("Value at Risk (97.5%)", b["var_975"], s["var_975"]),
        _num_row("Stressed ES", b["es_stressed"], s["es_stressed"]),
        _num_row("Liquidity-adjusted ES", b["liquidity_adjusted_es"],
                 s["liquidity_adjusted_es"]),
        _num_row("Capital required", b["capital"]["capital"], s["capital"]["capital"]),
        _cat_row("Market regime", b["volatility_regime"].capitalize(),
                 s["volatility_regime"].capitalize(),
                 d["regime_changed"], REGIME_COLORS.get(s["volatility_regime"], TEXT)),
        _cat_row("Capital set by", b["capital"]["binding"], s["capital"]["binding"],
                 d["binding_changed"],
                 ZONE_COLORS["red"] if d["binding_changed"] else TEXT),
    ]

    caption = ("Move a control above to run a scenario. The right column will show "
               "the new number and how far it moved from today."
               if no_shock else
               "Right column: your scenario. The arrow shows how much each number "
               "moved from today (red ▲ = more risk).")
    return html.Div([
        html.Table([header] + rows, className="data-table"),
        html.Div(caption, style={"color": MUTED, "fontSize": "12px",
                                 "marginTop": "10px"}),
    ])


def sensitivity_panel() -> "html.Div":
    """Sensitivity panel: marginal/component VaR + a confidence/window grid.

    Inputs:  none.  Output:  an html.Div (two tables side by side).
    """
    wide, _ = _returns_and_stress()
    if wide is None:
        return html.Div("Sensitivity analysis needs the return history "
                        "(ships on the next weekly update).",
                        style={"color": "#8b949e"})
    from pipeline.sensitivity import marginal_component_var, parameter_grid

    df, base_var = marginal_component_var(wide)
    grid = parameter_grid(wide)

    def _cell(v, **extra):
        style = {"padding": "4px 10px", "fontFamily": "monospace", "color": TEXT}
        style.update(extra)
        return html.Td(v, style=style)

    def _hdr(text):
        return html.Th(text, style={"color": "#8b949e", "textAlign": "left",
                                    "padding": "4px 10px"})

    # Marginal / component VaR table.
    mc_rows = [html.Tr([_hdr(h) for h in
                        ["Asset", "Weight", "Marginal VaR", "Component", "Share"]])]
    for _, r in df.iterrows():
        hedge = r["component_var"] < 0
        share_color = "#2ecc71" if hedge else TEXT
        mc_rows.append(html.Tr([
            _cell(r["ticker"], color="#8b949e"),
            _cell(f"{r['weight']:.0%}"),
            _cell(f"{r['marginal_var']:+.4f}"),
            _cell(f"{r['component_var']:+.4f}"),
            _cell(f"{r['pct_of_var']:+.1%}", color=share_color,
                  fontWeight="bold" if hedge else "normal"),
        ]))

    # Parameter grid (VaR) table: rows = window, cols = confidence.
    confs = sorted(grid["confidence"].unique())
    pivot = grid.pivot(index="window", columns="confidence", values="var")
    grid_rows = [html.Tr([_hdr("Window")] + [_hdr(f"{c:.1%}") for c in confs])]
    for w in sorted(pivot.index):
        grid_rows.append(html.Tr(
            [_cell(f"{w}d", color="#8b949e")] +
            [_cell(f"{pivot.loc[w, c]:.2%}") for c in confs]))

    return html.Div([
        html.Div([
            html.Div(f"Two questions about today's 97.5% VaR of {base_var:.2%}.",
                     style={"color": TEXT, "fontSize": "13px", "marginBottom": "4px"}),
            html.Div("Left: which assets are responsible for it. Each asset's "
                     "share adds up to 100% of the total. A negative share (like the "
                     "dollar) means that asset is a hedge: holding it actually lowers "
                     "the portfolio's risk.",
                     style={"color": MUTED, "fontSize": "12.5px", "margin": "2px 0"}),
            html.Div("Right: how that VaR changes if you measure it at a stricter "
                     "confidence level or over a different length of history, so you "
                     "can see how much the number depends on those choices.",
                     style={"color": MUTED, "fontSize": "12.5px", "margin": "2px 0"}),
        ], style={"marginBottom": "12px"}),
        html.Div(style={"display": "grid", "gridTemplateColumns": "1.4fr 1fr",
                        "gap": "20px"}, children=[
            html.Div([html.Div("Marginal & component VaR by asset",
                               style={"color": TEXT, "marginBottom": "6px"}),
                      html.Table(mc_rows, className="data-table")]),
            html.Div([html.Div("VaR sensitivity to confidence x window",
                               style={"color": TEXT, "marginBottom": "6px"}),
                      html.Table(grid_rows, className="data-table")]),
        ]),
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

_GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


def _card(children, title: str | None = None, style: dict | None = None,
          interactive: bool = False) -> html.Div:
    """A bordered panel with an optional title. `interactive` adds a hover lift."""
    inner = []
    if title:
        inner.append(html.Div(title, className="card-title"))
    inner.append(children)
    cls = "card card-interactive" if interactive else "card"
    return html.Div(inner, className=cls, style=style or {})


def _section(title: str, subtitle: str, children) -> html.Div:
    """A labelled top-level zone: heading, one-line description, divider, body."""
    return html.Div([
        html.Div(title, className="section-title"),
        html.Div(subtitle, className="section-sub"),
        html.Div(className="section-rule"),
        children,
    ], style={"marginBottom": "30px"})


def _legend() -> html.Div:
    """A small colour key so the green/amber/red coding is self-explanatory."""
    def chip(color, label):
        return html.Span([
            html.Span(style={"display": "inline-block", "width": "9px",
                             "height": "9px", "borderRadius": "50%",
                             "backgroundColor": color, "marginRight": "6px"}),
            html.Span(label, style={"color": MUTED, "fontSize": "12px"}),
        ], style={"marginRight": "16px"})
    return html.Div([
        html.Span("Colour key:", style={"color": MUTED, "fontSize": "12px",
                                        "marginRight": "10px"}),
        chip(REGIME_COLORS["normal"], "calm / passing"),
        chip(REGIME_COLORS["elevated"], "elevated / caution"),
        chip(REGIME_COLORS["stressed"], "stressed / failing"),
    ], style={"marginTop": "10px"})


def _header() -> html.Div:
    """Top bar: title and tagline on the left, live status and links on the right."""
    latest = run_query("SELECT MAX(date) AS d FROM daily_risk_metrics")
    as_of = (str(latest["d"].iloc[0])[:10]
             if not latest.empty and latest["d"].iloc[0] is not None else "n/a")
    return html.Div([
        html.Div([
            html.H1("FRTB IMA Risk Monitor",
                    style={"color": TEXT, "margin": "0", "fontSize": "26px",
                           "fontWeight": "700", "letterSpacing": "-.01em"}),
            html.Div("A daily Fundamental Review of the Trading Book risk desk: "
                     "Expected Shortfall, capital, model validation, and live stress testing.",
                     style={"color": MUTED, "fontSize": "13.5px", "marginTop": "4px",
                            "maxWidth": "640px"}),
        ]),
        html.Div([
            html.Div([html.Span(className="live-dot"),
                      html.Span(f"  Updated {as_of}",
                                style={"color": MUTED, "fontSize": "12.5px",
                                       "marginLeft": "8px"})],
                     style={"display": "flex", "alignItems": "center",
                            "justifyContent": "flex-end", "marginBottom": "8px"}),
            html.Div([
                html.A("GitHub", href=REPO_URL, className="nav-link", target="_blank"),
                html.Span(" · ", style={"color": BORDER}),
                html.A("Teaching guide", href=f"{REPO_URL}/blob/main/TEACHING.md",
                       className="nav-link", target="_blank"),
                html.Span(" · ", style={"color": BORDER}),
                html.A("Explainer", href=f"{REPO_URL}/blob/main/EXPLAINER.md",
                       className="nav-link", target="_blank"),
            ], style={"textAlign": "right"}),
        ]),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "flex-start", "flexWrap": "wrap", "gap": "16px",
              "paddingBottom": "18px", "borderBottom": f"1px solid {BORDER}",
              "marginBottom": "26px"})


def serve_layout() -> html.Div:
    """Build the page layout fresh on each load.

    Inputs:  none.  Output:  the root html.Div.
    """
    return html.Div(style={"backgroundColor": BG, "minHeight": "100vh",
                           "padding": "28px 32px", "fontFamily": FONT,
                           "maxWidth": "1280px", "margin": "0 auto"}, children=[
        dcc.Interval(id="refresh", interval=24 * 60 * 60 * 1000, n_intervals=0),
        _header(),
        _legend(),
        html.Div(style={"height": "22px"}),

        # ----- Zone 1: today's risk at a glance -----------------------------
        _section(
            "Today's Risk", "Where the book sits right now and how it has moved.",
            html.Div([
                html.Div(id="scorecard", style={"marginBottom": "18px"}),
                html.Div(className="grid-2", children=[
                    _card(dcc.Graph(id="es-var", config=_GRAPH_CONFIG)),
                    _card(dcc.Graph(id="regime", config=_GRAPH_CONFIG)),
                ]),
                html.Div(style={"height": "16px"}),
                _card(dcc.Graph(id="contrib", config=_GRAPH_CONFIG)),
            ]),
        ),

        # ----- Zone 2: interactive exploration ------------------------------
        _section(
            "Explore the Model",
            "Two hands-on tools. The Scenario Lab asks \"what if a shock hits?\". "
            "The sensitivity panel asks \"which positions actually drive the risk?\".",
            html.Div([
                _card(html.Div([
                    _scenario_help(),
                    html.Div(className="grid-scenario", children=[
                        scenario_controls(),
                        html.Div(id="sc-output"),
                    ]),
                ]), title="Scenario Lab: invent a shock, watch the risk react",
                    interactive=True),
                html.Div(style={"height": "16px"}),
                _card(html.Div(id="sensitivity"),
                      title="Sensitivity Analysis: which positions own the risk, and how robust is the number"),
            ]),
        ),

        # ----- Zone 3: model validation -------------------------------------
        _section(
            "Model Validation",
            "The checks an FRTB internal model must pass to keep using its own "
            "numbers instead of the regulator's stricter formula.",
            html.Div(className="grid-2", children=[
                _card(html.Div(id="pla"),
                      title="P&L Attribution (PLA): is the model watching the right risks?"),
                _card(html.Div(id="backtest"),
                      title="Weekly Backtest: were the predicted losses the right size?"),
            ]),
        ),

        html.Div("Built end-to-end as a portfolio project. Figures are illustrative "
                 "on a synthetic book; see the README's Limitations section.",
                 style={"color": MUTED, "fontSize": "12px", "textAlign": "center",
                        "padding": "10px 0 4px", "borderTop": f"1px solid {BORDER}"}),
    ])


app.layout = serve_layout


@app.callback(
    Output("scorecard", "children"),
    Output("es-var", "figure"),
    Output("regime", "figure"),
    Output("contrib", "figure"),
    Output("backtest", "children"),
    Output("pla", "children"),
    Output("sensitivity", "children"),
    Input("refresh", "n_intervals"),
)
def refresh_panels(_n):
    """Refresh the scorecard band, the grid panels, PLA, and sensitivity.

    Inputs:  _n - the Interval tick count (unused).
    Output:  (scorecard, es_var figure, regime figure, contributions figure,
             backtest table, pla panel, sensitivity panel).
    """
    return (scorecard(), figure_es_var(), figure_regime(),
            figure_contributions(), backtest_table(), pla_panel(),
            sensitivity_panel())


@app.callback(
    Output("sc-shock-label", "children"),
    Input("sc-class", "value"),
    Input("sc-shock", "value"),
)
def _shock_label(klass, shock):
    """Echo the chosen directional shock in plain words above its slider."""
    name = CLASS_LABELS.get(klass, klass)
    return f"3. How far {name} moves on the spot: {shock:+.0%}"


@app.callback(
    Output("sc-vol-label", "children"),
    Input("sc-vol", "value"),
)
def _vol_label(vol):
    """Echo the chosen volatility multiplier in words above its slider."""
    if vol == 1.0:
        return "1. Make the market jumpier: 1x (same as today)"
    return f"1. Make the market jumpier: {vol:g}x as choppy as today"


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
