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

The workflow commits the snapshot, DAILY_LOG.md, and any new drafts, then pushes;
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
DASHBOARD_TABLES = ["daily_risk_metrics", "asset_risk", "backtest_results"]
LOG_DAYS = 30


def export_snapshot() -> None:
    """Copy the dashboard tables from the working DB into the committed snapshot.

    Rebuilds the snapshot fresh so it holds only the three tables the hosted
    dashboard reads (no bulky price_history), keeping the committed file small.
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
    write_daily_log()
    print("[daily_update] done.")


if __name__ == "__main__":
    main()
