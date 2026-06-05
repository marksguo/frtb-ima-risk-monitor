"""Master pipeline orchestrator - the entry point Windows Task Scheduler calls.

Runs the full daily workflow in order:

    1. fetch_prices      - pull latest prices, store returns
    2. calculate_risk    - VaR / ES / stress / liquidity / regime
    3. nmrf_checker      - NMRF classification flags
    4. backtest          - Acerbi-Szekely weekly backtest (Fridays, or first run)
    5. pla               - FRTB P&L Attribution test (Spearman + KS, by Friday)
    6. generate_summary  - daily one-liner; weekly narrative on Fridays

Every step is wrapped so one failure is logged and never crashes the run
silently. All output is timestamped and appended to outputs/pipeline_log.txt
(absolute path, so it works regardless of the scheduler's working directory).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

# Narratives may contain non-ASCII; force UTF-8 console output so a scheduled
# run never dies printing an emoji on Windows' default cp1252 code page.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

LOG_PATH = PROJECT_ROOT / "outputs" / "pipeline_log.txt"

from database.db_utils import (
    create_database, init_schema, get_engine, run_query, _is_sqlite,
)
from pipeline import (
    fetch_prices, calculate_risk, nmrf_checker, backtest, event_detector, pla,
)
from narrative import generate_summary

FRIDAY = 4


def _build_logger() -> logging.Logger:
    """Configure a logger that writes timestamped lines to file and stdout.

    Inputs:  none (uses the absolute LOG_PATH).
    Output:  a configured logging.Logger.
    """
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("frtb_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _run_step(logger: logging.Logger, name: str, func, critical: bool) -> bool:
    """Run a single pipeline step, logging success or failure.

    Inputs:
        logger:   the pipeline logger.
        name:     human-readable step name.
        func:     zero-argument callable that performs the step.
        critical: if True, a failure aborts the rest of the pipeline.
    Output:
        True if the step succeeded, False otherwise.
    """
    logger.info(f"START {name}")
    try:
        func()
        logger.info(f"OK    {name}")
        return True
    except Exception:
        logger.exception(f"FAIL  {name}")
        if critical:
            logger.error(f"Critical step '{name}' failed; aborting pipeline.")
        return False


def _should_backtest(engine) -> bool:
    """Decide whether to run the weekly backtest on this invocation.

    Runs if today is Friday (the intended weekly cadence) or if no backtest
    results exist yet (first-run bootstrap, so the dashboard is populated
    immediately rather than waiting for the next Friday).

    Inputs:  engine - a SQLAlchemy engine.
    Output:  True if the backtest step should run.
    """
    if datetime.now().weekday() == FRIDAY:
        return True
    existing = run_query("SELECT COUNT(*) AS n FROM backtest_results", engine=engine)
    return int(existing["n"].iloc[0]) == 0


def main() -> None:
    """Run the full pipeline end to end with logging and a final summary line.

    Inputs:  none.
    Output:  None. Side effects: all tables updated; a confirmation line printed
             and logged.
    """
    logger = _build_logger()
    logger.info("=" * 70)
    logger.info("FRTB pipeline run starting")

    # Self-bootstrap so a fresh machine works without manual setup. Idempotent.
    # create_database() is PostgreSQL-only (CREATE DATABASE); on SQLite the file
    # and tables are created by init_schema(), so skip it there.
    def _bootstrap() -> None:
        if not _is_sqlite():
            create_database()
        init_schema()

    if not _run_step(logger, "ensure database + schema", _bootstrap, critical=True):
        logger.info("Pipeline aborted at bootstrap.")
        return

    if not _run_step(logger, "fetch_prices", fetch_prices.main, critical=True):
        logger.info("Pipeline aborted: price data unavailable.")
        return

    if not _run_step(logger, "calculate_risk", calculate_risk.main, critical=True):
        logger.info("Pipeline aborted: risk metrics unavailable.")
        return

    _run_step(logger, "nmrf_checker", nmrf_checker.main, critical=False)

    engine = get_engine()
    try:
        ran_backtest = False
        if _should_backtest(engine):
            ran_backtest = _run_step(logger, "backtest", backtest.main, critical=False)
        else:
            logger.info("SKIP  backtest (not Friday and results already exist)")

        _run_step(logger, "pla", pla.main, critical=False)

        _run_step(logger, "event_detector", event_detector.main, critical=False)

        _run_step(logger, "generate_summary", generate_summary.main, critical=False)

        # Final confirmation line.
        latest = run_query(
            "SELECT date, es_975, volatility_regime FROM daily_risk_metrics "
            "ORDER BY date DESC LIMIT 1", engine=engine,
        )
        if latest.empty:
            summary = "Pipeline complete: no metrics available."
        else:
            r = latest.iloc[0]
            backtest_status = "N/A"
            if ran_backtest:
                bt = run_query(
                    "SELECT pass_fail FROM backtest_results "
                    "ORDER BY week_ending DESC LIMIT 1", engine=engine,
                )
                if not bt.empty:
                    backtest_status = bt.iloc[0]["pass_fail"]
            summary = (
                f"Pipeline complete: {pd.to_datetime(r['date']).date()} | "
                f"ES: {float(r['es_975']):.4f} | Regime: {r['volatility_regime']} | "
                f"Backtest: {backtest_status}"
            )
    finally:
        engine.dispose()

    logger.info(summary)
    logger.info("FRTB pipeline run finished")
    print(summary)


if __name__ == "__main__":
    main()
