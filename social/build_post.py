"""Generate a ready-to-post LinkedIn package (image + caption) for the day.

Semi-automated by design: this writes a draft to social/drafts/ for Marks to
review and post manually. It never posts anything itself.

A draft is produced only when the day is worth posting about:
  * any event fired (see pipeline/event_detector.py), or
  * it is the weekly digest day (Friday).
Quiet days produce nothing, keeping the LinkedIn feed high-signal.

Each draft is two files in social/drafts/:
  * YYYY-MM-DD.png  - a branded "risk card" rendered with matplotlib, and
  * YYYY-MM-DD.md   - the caption plus a short review checklist.

The caption is written by Claude when ANTHROPIC_API_KEY is set; otherwise a
templated fallback is assembled from the metrics so the package still builds.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: render to file, never open a window (CI-safe)
import matplotlib.pyplot as plt
import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from database.db_utils import get_engine, run_query
from narrative.generate_summary import (
    api_key_configured, build_daily_context, get_client, _message_text,
)

DRAFTS_DIR = Path(os.getenv("FRTB_DRAFTS_DIR")
                  or (Path(__file__).resolve().parent / "drafts"))
REPO_URL = "github.com/marksguo/frtb-ima-risk-monitor"
EXPLAINER_URL = (
    "https://github.com/marksguo/frtb-ima-risk-monitor/blob/main/EXPLAINER.md"
)
FRIDAY = 4

# Brand palette (matches the portfolio "quant terminal" aesthetic).
BG = "#0a0814"
FG = "#e6e1f5"
MUTED = "#8a82a8"
ACCENT = "#58a6ff"
REGIME_COLOR = {"normal": "#6ec5a8", "elevated": "#e8b563", "stressed": "#ff7e6b"}


def es_history(engine, n: int = 60) -> pd.DataFrame:
    """Return the trailing ``n`` days of (date, es_975) oldest-first for the sparkline."""
    df = run_query(
        "SELECT date, es_975 FROM daily_risk_metrics ORDER BY date DESC LIMIT :n",
        params={"n": n}, engine=engine,
    ).iloc[::-1].reset_index(drop=True)
    df["es_975"] = df["es_975"].astype(float)
    df["date"] = pd.to_datetime(df["date"])
    return df


def events_for(engine, date: str) -> list[dict]:
    """Return the events recorded for a given ISO date (newest severity first)."""
    df = run_query(
        "SELECT event_type, severity, headline, detail FROM events WHERE date = :d",
        params={"d": date}, engine=engine,
    )
    return df.to_dict("records")


def decide(events: list[dict], as_of: pd.Timestamp) -> tuple[bool, str]:
    """Decide whether to post and in which mode.

    Inputs:
        events: today's detected events.
        as_of:  the metrics date.
    Output:
        (should_post, mode) where mode is 'weekly' on Fridays, else 'event'
        when something fired, else ('', '') on a quiet day.
    """
    if as_of.dayofweek == FRIDAY:
        return True, "weekly"
    if events:
        return True, "event"
    return False, ""


def render_card(ctx: dict, hist: pd.DataFrame, out_png: Path) -> None:
    """Render the branded daily risk card PNG.

    Inputs:
        ctx:     daily context dict (date, es_975, regime, top_mover, ...).
        hist:    trailing ES history for the sparkline (oldest-first).
        out_png: destination path for the PNG.
    Output:  None. Side effect: the PNG is written.
    """
    regime = str(ctx["volatility_regime"]).lower()
    rcolor = REGIME_COLOR.get(regime, ACCENT)

    fig = plt.figure(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor(BG)

    # Header.
    fig.text(0.06, 0.88, "FRTB IMA RISK MONITOR", color=ACCENT, fontsize=18,
             fontweight="bold", family="monospace")
    fig.text(0.94, 0.88, ctx["date"], color=MUTED, fontsize=14, ha="right",
             family="monospace")

    # Headline metric.
    fig.text(0.06, 0.60, "97.5% Expected Shortfall (1-day)", color=MUTED, fontsize=15)
    fig.text(0.06, 0.42, f"{ctx['es_975']:.2%}", color=FG, fontsize=72, fontweight="bold")

    # Regime badge.
    fig.text(0.06, 0.30, f"  {regime.upper()} REGIME  ", color=BG, fontsize=14,
             fontweight="bold", family="monospace",
             bbox=dict(boxstyle="round,pad=0.4", facecolor=rcolor, edgecolor="none"))

    # Secondary stats.
    fig.text(0.40, 0.30,
             f"Stressed ES {ctx['es_stressed']:.2%}    "
             f"Liquidity-adj ES {ctx['liquidity_adjusted_es']:.2%}    "
             f"Top mover {ctx['top_mover']} {ctx['top_mover_return']:+.2f}%",
             color=MUTED, fontsize=12)

    # Sparkline of trailing ES (top-right inset).
    if len(hist) >= 2:
        ax = fig.add_axes([0.55, 0.46, 0.39, 0.34])
        ax.plot(hist["date"], hist["es_975"], color=ACCENT, linewidth=2)
        ax.fill_between(hist["date"], hist["es_975"], hist["es_975"].min(),
                        color=ACCENT, alpha=0.12)
        ax.scatter([hist["date"].iloc[-1]], [hist["es_975"].iloc[-1]],
                   color=rcolor, s=40, zorder=5)
        ax.set_facecolor(BG)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=MUTED, labelsize=8)
        ax.set_xticks([])
        ax.set_title(f"97.5% ES, trailing {len(hist)} days", color=MUTED,
                     fontsize=10, loc="left")

    # Footer.
    fig.text(0.06, 0.09,
             f"{REPO_URL}   |   6-asset multi-class book   |   Historical Simulation, "
             f"FRTB liquidity-horizon scaled", color=MUTED, fontsize=11,
             family="monospace")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=BG, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)


# Voice spec for caption generation. Mirrors how Marks writes on LinkedIn: a
# curious finance/stats junior showing his work, not a senior analyst. Strict
# rules carry through to the templated fallback below.
VOICE = (
    "You are Marks Guo, a Statistics + Finance junior at the University of "
    "Rochester. You built this small FRTB IMA risk-monitoring project as a "
    "learning exercise and ongoing recruiting signal, and you write regular "
    "LinkedIn updates about it in your own voice. Sound like a curious student "
    "showing his work to a classmate, NOT a senior risk analyst.\n"
    "\n"
    "STRICT voice rules:\n"
    "- NEVER use em-dashes (no '--' or '—'). Use commas, periods, "
    "parentheses, or words like 'so' / 'which' instead.\n"
    "- No polished-analyst openers. Avoid lines like 'Markets kept risk "
    "managers honest', 'The real story sits in...', or 'X is not decoration, "
    "it is Y in disguise'. That voice is wrong here.\n"
    "- Plain English first. The first time a technical term appears (FRTB, "
    "Expected Shortfall, 'stressed regime', 'liquidity horizon', etc.), "
    "demystify it inline with a quick casual parenthetical.\n"
    "- Frame numbers casually. E.g., 'about 1.5%, which is roughly the cushion "
    "a bank would have to set aside in case tomorrow goes badly' rather than "
    "'97.5% ES of 1.56% under stress-calibrated conditions'.\n"
    "- Friendly conversational opener like 'Quick weekly update on the FRTB "
    "Risk Monitor:', 'Caught something interesting on the risk monitor today:', "
    "or 'Here's what showed up this week:'.\n"
    "- Light. Curious. First-person. Show that you understand and can explain.\n"
    "- End with 3 to 5 relevant hashtags on their own final line. Each hashtag "
    "MUST be a single token with no spaces inside it (write "
    "'#QuantitativeFinance', NOT '#Quantitative Finance')."
)


def _format_metrics(ctx: dict, backtest: dict | None) -> str:
    """One-paragraph data block handed to Claude. All numbers are facts to use."""
    lines = [
        f"Date: {ctx['date']}",
        f"97.5% Expected Shortfall: {ctx['es_975']:.4f} ({ctx['es_975']:.2%})",
        f"Stressed ES: {ctx['es_stressed']:.4f} ({ctx['es_stressed']:.2%})",
        f"Liquidity-adjusted ES: {ctx['liquidity_adjusted_es']:.4f} "
        f"({ctx['liquidity_adjusted_es']:.2%})",
        f"Volatility regime: {ctx['volatility_regime']}",
        f"Top mover today: {ctx['top_mover']} ({ctx['top_mover_return']:+.3f}%)",
    ]
    if backtest:
        lines.append(
            f"Weekly model backtest: {backtest['pass_fail']} "
            f"(Acerbi-Szekely statistic {float(backtest['acerbi_szekely_statistic']):.4f}, "
            f"{int(backtest['exceptions_count'])} VaR exceptions)"
        )
    return "\n".join(lines)


def _fallback_caption(ctx: dict, events: list[dict], mode: str,
                      backtest: dict | None) -> str:
    """Templated caption when no Claude key is configured. Same voice."""
    if mode == "weekly":
        bt = ""
        if backtest:
            bt = (f" The weekly model self-check (backtest) "
                  f"{str(backtest['pass_fail']).lower()}ed.")
        return (
            f"Quick weekly update on the FRTB Risk Monitor:\n\n"
            f"The portfolio's 97.5% Expected Shortfall (basically the average "
            f"loss on a really bad day) is about {ctx['es_975']:.2%}. Stress "
            f"the model and that jumps to {ctx['es_stressed']:.2%}; factor in "
            f"how long it would take to actually exit the riskier positions "
            f"(the 'liquidity horizon' thing) and you get "
            f"{ctx['liquidity_adjusted_es']:.2%}. The market is in a "
            f"'{ctx['volatility_regime']}' regime, and today's biggest mover "
            f"was {ctx['top_mover']} at {ctx['top_mover_return']:+.2f}%.{bt}\n\n"
            f"Plain-English explainer if any of the terms are new: {EXPLAINER_URL}\n\n"
            f"#FRTB #MarketRisk #ExpectedShortfall #QuantFinance"
        )
    # Event mode.
    headline = (events[0]["headline"] if events else "Notable risk move")
    return (
        f"Quick update from the FRTB Risk Monitor: {headline.lower()}.\n\n"
        f"Today's 97.5% Expected Shortfall (the average loss on a really bad "
        f"day) sits at {ctx['es_975']:.2%}, stressed ES at "
        f"{ctx['es_stressed']:.2%}, and the liquidity-adjusted figure at "
        f"{ctx['liquidity_adjusted_es']:.2%}. The market regime is "
        f"'{ctx['volatility_regime']}', and today's top mover was "
        f"{ctx['top_mover']} at {ctx['top_mover_return']:+.2f}%.\n\n"
        f"More context: {EXPLAINER_URL}\n\n"
        f"#FRTB #MarketRisk #ExpectedShortfall #QuantFinance"
    )


def write_caption(ctx: dict, events: list[dict], mode: str,
                  backtest: dict | None = None) -> str:
    """Generate the LinkedIn caption in Marks's voice.

    Uses Claude when ANTHROPIC_API_KEY is set; falls back to a templated voice
    match otherwise so the package always builds.

    Inputs:
        ctx:      daily context dict (date, es_975, regime, top_mover, ...).
        events:   today's detected events (may be empty on the weekly digest day).
        mode:     'event' or 'weekly'.
        backtest: latest backtest row (dict) or None.
    Output:  the caption text.
    """
    if not api_key_configured():
        return _fallback_caption(ctx, events, mode, backtest)

    client = get_client()
    if mode == "weekly":
        ask = (
            "Write a 4 to 6 short-paragraph weekly LinkedIn update in this "
            "voice. Cover, briefly: a friendly opener, today's 97.5% Expected "
            "Shortfall and what it means in plain English, how the stressed "
            "and liquidity-adjusted numbers compare, what regime the market is "
            "in and what that implies, the biggest mover and one short bit of "
            "interpretation, the backtest result if it is informative, and one "
            "curious forward-look. Include this explainer link on its own line "
            f"near the end: {EXPLAINER_URL}. Total length around 180 to 240 "
            "words."
        )
    else:
        headlines = "; ".join(e["headline"] for e in events) or "a notable risk move"
        ask = (
            "Write a punchy 3 to 5 sentence LinkedIn update in this voice "
            f"about today's notable risk signal(s): {headlines}. Explain what "
            "happened, give the relevant numbers casually, and add one short "
            "line of plain-English interpretation. Optionally include the "
            "explainer link if it would help a non-quant reader: "
            f"{EXPLAINER_URL}. Total length around 80 to 140 words."
        )

    prompt = (
        f"{VOICE}\n\n"
        f"Task: {ask}\n\n"
        f"Today's facts (use these exact numbers):\n{_format_metrics(ctx, backtest)}"
    )
    response = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return _message_text(response)


def write_draft(ctx: dict, caption: str, events: list[dict], mode: str,
                png_name: str) -> Path:
    """Write the draft markdown file with the caption and a review checklist.

    Inputs:
        ctx:      daily context dict.
        caption:  the generated caption text.
        events:   today's events.
        mode:     'event' or 'weekly'.
        png_name: filename of the accompanying image (same folder).
    Output:  the path to the written .md file.
    """
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    md = DRAFTS_DIR / f"{ctx['date']}.md"
    trigger = (", ".join(e["event_type"] for e in events)
               if events else "weekly digest")
    lines = [
        f"# Draft post - {ctx['date']} ({mode})",
        "",
        f"**Trigger:** {trigger}  ",
        f"**Image:** `{png_name}`  ",
        f"**Regime:** {ctx['volatility_regime']} | **97.5% ES:** {ctx['es_975']:.2%}",
        "",
        "## Caption (review, then post with the image)",
        "",
        caption,
        "",
        "---",
        "- [ ] Numbers look right",
        "- [ ] Caption reads well",
        "- [ ] Image attached",
        "- [ ] Posted to LinkedIn",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    return md


def update_index() -> None:
    """Regenerate social/drafts/README.md listing drafts newest-first."""
    drafts = sorted(DRAFTS_DIR.glob("20*.md"), reverse=True)
    lines = ["# Post drafts", "",
             "Auto-generated ready-to-post packages. Review the `.md`, attach the "
             "matching `.png`, and post to LinkedIn.", ""]
    for d in drafts:
        lines.append(f"- **{d.stem}** - [caption]({d.name}) + [image]({d.stem}.png)")
    lines.append("")
    (DRAFTS_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Build today's post package if the day is post-worthy; else do nothing.

    Inputs:  none.
    Output:  None. Side effects: draft files written under social/drafts/.
    """
    engine = get_engine()
    try:
        ctx = build_daily_context(engine)
        as_of = ctx["as_of"]
        date = ctx["date"]
        events = events_for(engine, date)
        post, mode = decide(events, as_of)
        if not post:
            print(f"[build_post] {date}: quiet day, no draft.")
            return
        hist = es_history(engine, n=60)
        bt_df = run_query(
            "SELECT pass_fail, acerbi_szekely_statistic, exceptions_count "
            "FROM backtest_results ORDER BY week_ending DESC LIMIT 1",
            engine=engine,
        )
        backtest = bt_df.iloc[0].to_dict() if not bt_df.empty else None
    finally:
        engine.dispose()

    png = DRAFTS_DIR / f"{date}.png"
    render_card(ctx, hist, png)
    caption = write_caption(ctx, events, mode, backtest=backtest)
    md = write_draft(ctx, caption, events, mode, png.name)
    update_index()
    print(f"[build_post] {date}: wrote draft ({mode}) -> {md.name} + {png.name}")


if __name__ == "__main__":
    main()
