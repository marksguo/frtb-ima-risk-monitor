"""Step 5 of the pipeline: generate LinkedIn-ready narratives via the Claude API.

Produces two outputs:
  * a daily one-liner summarising the day's risk metrics, and
  * (on Fridays) a ~150-word weekly narrative tying together the backtest
    result, ES drivers, and the volatility regime.

Both are written to the narrative_log table.

MODEL CHOICE
    The project brief named ``claude-sonnet-4-20250514``. That model ID is
    deprecated and retires 2026-06-15, which would break this daily pipeline a
    few weeks after it was written. We therefore default to ``claude-sonnet-4-6``
    (current Sonnet, same price tier) and expose it as the MODEL constant below
    so it is a one-line change.

PROMPT CACHING
    Intentionally omitted. These prompts are short and unique per day, with no
    large shared prefix to cache, and they fall under Sonnet's 2048-token
    minimum cacheable prefix, so cache_control would silently no-op.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic
import pandas as pd
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import ENV_PATH, get_engine, run_query, upsert_dataframe

# Claude may emit non-ASCII (emoji, smart punctuation) in narratives. Force
# UTF-8 stdout/stderr so printing never crashes on Windows' default cp1252.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

load_dotenv(ENV_PATH)

# Configurable Claude model. See module docstring for why this differs from the
# brief's specified (now-deprecated) model ID.
MODEL = "claude-sonnet-4-6"

FRIDAY = 4


def api_key_configured() -> bool:
    """Return True if a real (non-placeholder) Anthropic API key is set.

    Inputs:  none (reads ANTHROPIC_API_KEY from the environment).
    Output:  True if a usable key is present, else False.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    return bool(key) and not key.startswith("replace_with_")


def get_client() -> anthropic.Anthropic:
    """Create an Anthropic client using the key from the project .env.

    Inputs:  none (reads ANTHROPIC_API_KEY from the environment).
    Output:  a configured anthropic.Anthropic client.
    Raises:  RuntimeError if the API key is missing or still a placeholder.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key or key.startswith("replace_with_"):
        raise RuntimeError(
            f"ANTHROPIC_API_KEY is not set. Edit {ENV_PATH} and provide a real key."
        )
    return anthropic.Anthropic(api_key=key)


def _message_text(response: anthropic.types.Message) -> str:
    """Extract the concatenated text from a Claude Messages API response.

    Inputs:
        response: a Message object returned by client.messages.create().
    Output:
        The response's text content as a single stripped string.
    """
    return "".join(b.text for b in response.content if b.type == "text").strip()


def daily_one_liner(metrics: dict, client: anthropic.Anthropic | None = None) -> str:
    """Generate the daily one-line LinkedIn post from a metrics dict.

    Inputs:
        metrics: dict with keys date, es_975, es_stressed, volatility_regime,
                 liquidity_adjusted_es, top_mover, top_mover_return.
        client:  optional Anthropic client (one is created if None) so this can
                 be unit-tested with dummy data.
    Output:
        The generated post text (<= 2 sentences).
    """
    client = client or get_client()
    prompt = (
        "You are a quantitative risk analyst. Given these daily risk metrics for "
        "a synthetic FRTB IMA trading book, write a single LinkedIn post of "
        "maximum 2 sentences. Be specific with numbers. End with one insight "
        "about what this means for capital adequacy.\n"
        f"Metrics: {metrics['date']}, ES: {metrics['es_975']}, "
        f"Stressed ES: {metrics['es_stressed']}, Regime: {metrics['volatility_regime']}, "
        f"Liquidity-adjusted ES: {metrics['liquidity_adjusted_es']}.\n"
        f"Top moving asset: {metrics['top_mover']} ({metrics['top_mover_return']}%).\n"
        "Tone: professional, data-driven, accessible to non-quants."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return _message_text(response)


def weekly_narrative(weekly_summary_dict: dict,
                     client: anthropic.Anthropic | None = None) -> str:
    """Generate the weekly ~150-word LinkedIn narrative.

    Inputs:
        weekly_summary_dict: dict of this week's metrics and backtest results.
        client:              optional Anthropic client (created if None).
    Output:
        The generated narrative text (~150 words).
    """
    client = client or get_client()
    prompt = (
        "You are a quantitative risk analyst writing a weekly LinkedIn post about "
        "market risk. Given this week's FRTB IMA backtest results and risk "
        "metrics, write a 150-word post. Include: what drove ES changes this "
        "week, whether the model passed or failed the Acerbi-Szekely backtest, "
        "which asset contributed most to liquidity-adjusted capital, and one "
        "forward-looking insight about the volatility regime.\n"
        f"Data: {json.dumps(weekly_summary_dict, default=str)}\n"
        "Tone: Sharp, professional, educational. Accessible to finance students "
        "and junior analysts. Do not use em-dashes. Do not use filler phrases."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return _message_text(response)


def week_ending_for(as_of: pd.Timestamp) -> pd.Timestamp:
    """Return the Friday of the week containing ``as_of``.

    Inputs:
        as_of: any date.
    Output:
        The Friday (pd.Timestamp) of that Mon-Sun week. If as_of is Sat/Sun the
        Friday just passed is returned.
    """
    return (as_of - pd.Timedelta(days=as_of.dayofweek - FRIDAY)).normalize()


def build_daily_context(engine) -> dict:
    """Assemble the daily metrics dict from the database.

    Inputs:
        engine: a SQLAlchemy engine.
    Output:
        A dict suitable for daily_one_liner(), plus 'key_movers' (JSON string)
        and 'as_of' (pd.Timestamp).
    """
    latest = run_query(
        "SELECT * FROM daily_risk_metrics ORDER BY date DESC LIMIT 1", engine=engine
    )
    if latest.empty:
        raise RuntimeError("daily_risk_metrics is empty; run calculate_risk first.")
    row = latest.iloc[0]
    as_of = pd.to_datetime(row["date"])

    assets = run_query(
        "SELECT ticker, daily_return, es_contribution, liquidity_horizon "
        "FROM asset_risk WHERE date = :d",
        params={"d": as_of.date()}, engine=engine,
    )
    movers = assets.reindex(
        assets["daily_return"].abs().sort_values(ascending=False).index
    )
    top = movers.iloc[0]
    key_movers = json.dumps([
        {"ticker": r["ticker"], "return_pct": round(float(r["daily_return"]) * 100, 3)}
        for _, r in movers.head(3).iterrows()
    ])

    return {
        "date": str(as_of.date()),
        "es_975": float(row["es_975"]),
        "es_stressed": float(row["es_stressed"]),
        "liquidity_adjusted_es": float(row["liquidity_adjusted_es"]),
        "volatility_regime": row["volatility_regime"],
        "top_mover": top["ticker"],
        "top_mover_return": round(float(top["daily_return"]) * 100, 3),
        "key_movers": key_movers,
        "as_of": as_of,
    }


def build_weekly_context(engine, daily_ctx: dict) -> dict:
    """Assemble the weekly narrative input dict from the database.

    Inputs:
        engine:    a SQLAlchemy engine.
        daily_ctx: the dict returned by build_daily_context().
    Output:
        A dict of this week's metrics, backtest verdict, and top liquidity
        contributor, ready for weekly_narrative().
    """
    backtest = run_query(
        "SELECT * FROM backtest_results ORDER BY week_ending DESC LIMIT 1",
        engine=engine,
    )
    as_of = daily_ctx["as_of"]
    assets = run_query(
        "SELECT ticker, es_contribution, liquidity_horizon FROM asset_risk "
        "WHERE date = :d", params={"d": as_of.date()}, engine=engine,
    )
    # Liquidity-adjusted contribution proxy: ES contribution scaled by sqrt-time.
    assets["liq_adj"] = assets["es_contribution"].astype(float) * (
        (assets["liquidity_horizon"].astype(float) / 10.0) ** 0.5
    )
    top_liq = assets.sort_values("liq_adj", ascending=False).iloc[0]["ticker"] \
        if not assets.empty else None

    ctx = {
        "week_ending": str(week_ending_for(as_of).date()),
        "es_975": daily_ctx["es_975"],
        "es_stressed": daily_ctx["es_stressed"],
        "liquidity_adjusted_es": daily_ctx["liquidity_adjusted_es"],
        "volatility_regime": daily_ctx["volatility_regime"],
        "top_liquidity_contributor": top_liq,
        "key_movers": daily_ctx["key_movers"],
    }
    if not backtest.empty:
        b = backtest.iloc[0]
        ctx.update({
            "backtest_pass_fail": b["pass_fail"],
            "acerbi_szekely_statistic": float(b["acerbi_szekely_statistic"]),
            "exceptions_count": int(b["exceptions_count"]),
        })
    return ctx


def store_narrative(engine, week_ending: pd.Timestamp, daily_summary: str,
                    weekly_summary: str | None, key_movers: str) -> None:
    """Append a row to narrative_log.

    Inputs:
        engine:         a SQLAlchemy engine.
        week_ending:    the week-ending Friday for this narrative.
        daily_summary:  the daily one-liner text.
        weekly_summary: the weekly narrative text, or None on non-Fridays.
        key_movers:     JSON string of the day's top movers.
    Output:  None. Side effect: one row inserted into narrative_log.
    """
    df = pd.DataFrame([{
        "week_ending": week_ending.date(),
        "daily_summary": daily_summary,
        "weekly_summary": weekly_summary,
        "key_movers": key_movers,
    }])
    # narrative_log is append-only (it is a log), so no conflict target.
    df.to_sql("narrative_log", engine, if_exists="append", index=False)


def main() -> None:
    """Generate the daily (and, on Fridays, weekly) narrative and store it.

    Inputs:  none.
    Output:  None. Side effects: narrative_log updated; summaries printed.
             Skips cleanly (no error) if no API key is configured.
    """
    if not api_key_configured():
        print("[generate_summary] Skipped: ANTHROPIC_API_KEY not set in .env.")
        return

    client = get_client()
    engine = get_engine()
    try:
        daily_ctx = build_daily_context(engine)
        daily = daily_one_liner(daily_ctx, client=client)

        weekly = None
        as_of = daily_ctx["as_of"]
        if as_of.dayofweek == FRIDAY:
            weekly_ctx = build_weekly_context(engine, daily_ctx)
            weekly = weekly_narrative(weekly_ctx, client=client)

        store_narrative(
            engine, week_ending_for(as_of), daily, weekly, daily_ctx["key_movers"]
        )
    finally:
        engine.dispose()

    print(f"[generate_summary] Daily: {daily}")
    if weekly:
        print(f"[generate_summary] Weekly narrative generated ({len(weekly.split())} words).")


if __name__ == "__main__":
    main()
