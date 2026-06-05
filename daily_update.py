"""Cloud daily entrypoint, invoked by .github/workflows/daily.yml.

Runs the whole thing against an ephemeral SQLite working DB (no PostgreSQL in
CI), builds the post package if the day is notable, refreshes the committed
dashboard snapshot, and regenerates DAILY_LOG.md. Idempotent: the working DB is
rebuilt from full market history every run, so re-running is safe.

Steps:
  1. pipeline.run_pipeline  - fetch, risk, NMRF, backtest, events, narrative.
  2. social.build_post      - write a draft to social/drafts/ on notable days.
  3. export_snapshot        - copy the 3 dashboard tables into the committed
                              snapshot (keeps it small; dashboard reads only these).
  4. write_daily_log        - regenerate DAILY_LOG.md from the latest 30 days.
  5. write_changes          - regenerate CHANGES.md: 1d/1w/1m metric deltas.

The workflow commits the snapshot, DAILY_LOG.md, CHANGES.md, and any new drafts,
then pushes;
that push triggers the Render redeploy so the public dashboard refreshes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parent
WORKING = ROOT / "data" / "_working.sqlite"
# Output paths are overridable via env so this can be exercised against temp
# files in tests without touching the committed snapshot / log.
SNAPSHOT = Path(os.getenv("FRTB_SNAPSHOT_PATH") or (ROOT / "data" / "frtb_snapshot.sqlite"))
DAILY_LOG = Path(os.getenv("FRTB_DAILY_LOG") or (ROOT / "DAILY_LOG.md"))
CHANGES = Path(os.getenv("FRTB_CHANGES") or (ROOT / "CHANGES.md"))
# Return history for the interactive Scenario Lab. A plain CSV (not gzip) so git
# delta-compresses the daily one-row append to almost nothing; committed every
# run so the Scenario Lab's "today" baseline always matches the live metrics.
RETURNS_CSV = Path(os.getenv("FRTB_RETURNS_CSV") or (ROOT / "data" / "returns_history.csv"))
# pla_results powers the PLA panel (tiny). price_history is NOT kept here: at
# ~29k rows it would bloat the daily-committed snapshot, so the return history
# the Scenario Lab needs ships separately as a weekly gzipped CSV (export_returns).
DASHBOARD_TABLES = ["daily_risk_metrics", "asset_risk", "backtest_results",
                    "pla_results"]
LOG_DAYS = 30
# Lookback window (calendar days) loaded for the change scorecard; >30 so the
# 1-month comparison row is always present even across long market gaps.
CHANGES_DAYS = 45


def export_snapshot() -> None:
    """Copy the dashboard tables from the working DB into the committed snapshot.

    Rebuilds the snapshot fresh so it holds only the tables the hosted dashboard
    reads, keeping the committed file small. price_history is included so the
    interactive Scenario Lab can recompute risk from raw returns.
    """
    src = create_engine(f"sqlite:///{WORKING}")
    if SNAPSHOT.exists():
        SNAPSHOT.unlink()
    dst = create_engine(f"sqlite:///{SNAPSHOT}")
    try:
        for table in DASHBOARD_TABLES:
            df = pd.read_sql(f"SELECT * FROM {table}", src)
            df.to_sql(table, dst, if_exists="replace", index=False)
    finally:
        src.dispose()
        dst.dispose()
    print(f"[daily_update] snapshot refreshed: {', '.join(DASHBOARD_TABLES)}")


def export_returns() -> None:
    """Write the per-asset return history to a plain CSV for the Scenario Lab.

    The hosted dashboard recomputes risk from these raw returns. Stored as plain
    CSV and committed every run so the lab's "today" baseline stays current; git
    delta-compresses the daily append, keeping repo growth tiny.
    """
    src = create_engine(f"sqlite:///{WORKING}")
    try:
        df = pd.read_sql(
            "SELECT date, ticker, daily_return FROM price_history ORDER BY date", src
        )
    finally:
        src.dispose()
    RETURNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RETURNS_CSV, index=False)
    print(f"[daily_update] returns_history.csv written ({len(df)} rows).")


def write_daily_log() -> None:
    """Regenerate DAILY_LOG.md from the most recent LOG_DAYS of metrics + events."""
    eng = create_engine(f"sqlite:///{WORKING}")
    try:
        metrics = pd.read_sql(
            "SELECT date, es_975, volatility_regime FROM daily_risk_metrics "
            f"ORDER BY date DESC LIMIT {LOG_DAYS}", eng,
        )
        events = pd.read_sql("SELECT date, headline FROM events", eng)
    finally:
        eng.dispose()

    notable = events.groupby("date")["headline"].apply(lambda s: "; ".join(s)).to_dict()
    lines = [
        "# Daily risk log",
        "",
        "Auto-generated each trading day by the FRTB IMA Risk Monitor pipeline.",
        "97.5% 1-day Expected Shortfall on a synthetic 6-asset multi-class book.",
        "",
        "| Date | 97.5% ES | Regime | Notable |",
        "|------|----------|--------|---------|",
    ]
    for _, r in metrics.iterrows():
        d = str(r["date"])[:10]
        lines.append(
            f"| {d} | {float(r['es_975']):.2%} | {r['volatility_regime']} "
            f"| {notable.get(d, '-')} |"
        )
    lines.append("")
    DAILY_LOG.write_text("\n".join(lines), encoding="utf-8")
    print(f"[daily_update] DAILY_LOG.md regenerated ({len(metrics)} rows).")


def write_changes() -> None:
    """Regenerate CHANGES.md: 1d/1w/1m deltas on the headline risk metrics."""
    from pipeline.changes import compute_changes, to_markdown

    eng = create_engine(f"sqlite:///{WORKING}")
    try:
        metrics = pd.read_sql(
            "SELECT date, var_975, es_975, es_stressed, liquidity_adjusted_es, "
            "volatility_regime FROM daily_risk_metrics "
            f"ORDER BY date DESC LIMIT {CHANGES_DAYS}", eng,
        )
    finally:
        eng.dispose()
    metrics["date"] = pd.to_datetime(metrics["date"])
    CHANGES.write_text(to_markdown(compute_changes(metrics)), encoding="utf-8")
    print(f"[daily_update] CHANGES.md regenerated ({len(metrics)} rows scanned).")


def main() -> None:
    """Run the full daily update against an ephemeral SQLite working DB."""
    os.environ["FRTB_SQLITE_PATH"] = str(WORKING)
    if WORKING.exists():
        WORKING.unlink()  # start clean; full history is re-fetched every run

    from pipeline import run_pipeline
    run_pipeline.main()

    from social import build_post
    build_post.main()

    export_snapshot()
    export_returns()
    write_daily_log()
    write_changes()
    print("[daily_update] done.")


if __name__ == "__main__":
    main()
