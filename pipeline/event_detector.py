"""Step 4b of the pipeline: detect notable daily signals worth posting about.

The book's risk numbers barely move on a quiet day, so a literal daily post is
noise. This module decides whether *today* is interesting by comparing the
latest computed metrics against recent history, and records any findings to the
``events`` table. The post-package generator (social/build_post.py) reads those
events to decide which days become LinkedIn drafts.

Signals detected (for the latest date):
  * regime_change   - volatility regime differs from the prior trading day.
  * es_spike        - 97.5% ES moved >= ES_SPIKE_PCT day-over-day.
  * es_high         - 97.5% ES is at a new high over the trailing window.
  * backtest_breach - the current week's Acerbi-Szekely ES backtest failed.

Everything is derived from the full computed series, so it works whether the
database is long-lived (local Postgres) or rebuilt from scratch each run (cloud
SQLite).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import get_engine, run_query, upsert_dataframe

# Thresholds.
ES_SPIKE_PCT = 0.15      # day-over-day relative ES move that counts as a spike
WINDOW = 60              # trailing window (trading days) for the "new high" test
MIN_HISTORY = 20         # need at least this many days before calling a high
FRIDAY = 4

_REGIME_SEVERITY = {"stressed": "alert", "elevated": "warning", "normal": "info"}


def _week_ending(as_of: pd.Timestamp) -> pd.Timestamp:
    """Return the Friday of the week containing ``as_of`` (Sat/Sun -> Friday past)."""
    return (as_of - pd.Timedelta(days=as_of.dayofweek - FRIDAY)).normalize()


def detect_events(engine=None) -> list[dict]:
    """Detect notable signals for the latest available metrics date.

    Inputs:
        engine: optional SQLAlchemy engine; one is created/closed if None.
    Output:
        A list of event dicts with keys date, event_type, severity, headline,
        detail. Empty if there is too little history or nothing notable.
    """
    own_engine = engine is None
    engine = engine or get_engine()
    try:
        hist = run_query(
            "SELECT date, es_975, volatility_regime FROM daily_risk_metrics "
            "ORDER BY date DESC LIMIT :n",
            params={"n": WINDOW}, engine=engine,
        )
        if len(hist) < 2:
            return []
        # Oldest -> newest for window stats.
        hist = hist.iloc[::-1].reset_index(drop=True)
        hist["es_975"] = hist["es_975"].astype(float)

        latest, prev = hist.iloc[-1], hist.iloc[-2]
        date = str(latest["date"])[:10]
        es_now, es_prev = float(latest["es_975"]), float(prev["es_975"])
        regime_now, regime_prev = latest["volatility_regime"], prev["volatility_regime"]

        events: list[dict] = []

        # 1. Regime change.
        if regime_now != regime_prev:
            events.append({
                "date": date,
                "event_type": "regime_change",
                "severity": _REGIME_SEVERITY.get(regime_now, "info"),
                "headline": f"Volatility regime shifted: {regime_prev} -> {regime_now}",
                "detail": (f"97.5% ES now {es_now:.2%} ({_pct_change(es_now, es_prev)} "
                           f"day-over-day). The book moved from a '{regime_prev}' to a "
                           f"'{regime_now}' volatility regime."),
            })

        # 2. ES spike (day-over-day).
        if es_prev > 0:
            change = (es_now - es_prev) / es_prev
            if abs(change) >= ES_SPIKE_PCT:
                rose = change > 0
                events.append({
                    "date": date,
                    "event_type": "es_spike",
                    "severity": "warning" if rose else "info",
                    "headline": (f"97.5% ES {'rose' if rose else 'fell'} {change:+.0%} "
                                 f"day-over-day to {es_now:.2%}"),
                    "detail": (f"Expected Shortfall moved from {es_prev:.2%} to {es_now:.2%} "
                               f"in one session under a '{regime_now}' regime."),
                })

        # 3. New trailing-window high.
        if len(hist) >= MIN_HISTORY:
            window_max = float(hist["es_975"].max())
            if es_now >= window_max and es_now > es_prev:
                events.append({
                    "date": date,
                    "event_type": "es_high",
                    "severity": "warning",
                    "headline": f"Book risk at a {len(hist)}-day high: 97.5% ES {es_now:.2%}",
                    "detail": (f"Today's Expected Shortfall ({es_now:.2%}) is the highest in "
                               f"the trailing {len(hist)} trading days."),
                })

        # 4. Current-week backtest breach.
        bt = run_query(
            "SELECT week_ending, pass_fail, acerbi_szekely_statistic, exceptions_count "
            "FROM backtest_results ORDER BY week_ending DESC LIMIT 1", engine=engine,
        )
        if not bt.empty:
            b = bt.iloc[0]
            this_week = _week_ending(pd.to_datetime(date)).date().isoformat()
            if str(b["pass_fail"]).upper() == "FAIL" and str(b["week_ending"])[:10] == this_week:
                events.append({
                    "date": date,
                    "event_type": "backtest_breach",
                    "severity": "alert",
                    "headline": "Weekly ES backtest breached (Acerbi-Szekely)",
                    "detail": (f"This week's Acerbi-Szekely statistic "
                               f"{float(b['acerbi_szekely_statistic']):.4f} failed, with "
                               f"{int(b['exceptions_count'])} VaR exceptions."),
                })
        return events
    finally:
        if own_engine:
            engine.dispose()


def _pct_change(now: float, prev: float) -> str:
    """Format a relative change as a signed percent string, guarding divide-by-zero."""
    if prev == 0:
        return "n/a"
    return f"{(now - prev) / prev:+.0%}"


def persist_events(events: list[dict], engine=None) -> int:
    """Upsert detected events into the ``events`` table.

    Inputs:
        events: list of event dicts from detect_events().
        engine: optional SQLAlchemy engine; one is created/closed if None.
    Output:
        The number of event rows written.
    """
    if not events:
        return 0
    df = pd.DataFrame(events)[["date", "event_type", "severity", "headline", "detail"]]
    return upsert_dataframe(
        df, table="events",
        conflict_cols=["date", "event_type"],
        update_cols=["severity", "headline", "detail"],
        engine=engine,
    )


def main() -> None:
    """Detect and persist notable events for the latest date; print a summary.

    Inputs:  none.
    Output:  None. Side effect: the events table is updated.
    """
    engine = get_engine()
    try:
        events = detect_events(engine=engine)
        persist_events(events, engine=engine)
    finally:
        engine.dispose()

    if not events:
        print("[event_detector] No notable events today (quiet day).")
        return
    print(f"[event_detector] {len(events)} event(s) detected:")
    for e in events:
        print(f"  [{e['severity']}] {e['event_type']}: {e['headline']}")


if __name__ == "__main__":
    main()
