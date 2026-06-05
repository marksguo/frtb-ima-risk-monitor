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
    """Render the branded daily risk card PNG at exactly 1200x1200 (square).

    Square is LinkedIn-safe: the feed (mobile and desktop) and Articles never
    crop a 1:1 image, so all stats stay visible. ``bbox_inches`` is left at its
    default so the saved file matches the figure's native pixel dimensions
    (12in x 12in @ 100 dpi = 1200 x 1200).

    Inputs:
        ctx:     daily context dict (date, es_975, regime, top_mover, ...).
        hist:    trailing ES history for the sparkline (oldest-first).
        out_png: destination path for the PNG.
    Output:  None. Side effect: the PNG is written at exactly 1200x1200.
    """
    regime = str(ctx["volatility_regime"]).lower()
    rcolor = REGIME_COLOR.get(regime, ACCENT)

    fig = plt.figure(figsize=(12, 12), dpi=100)
    fig.patch.set_facecolor(BG)

    # Header band: brand left, date right.
    fig.text(0.06, 0.94, "FRTB IMA RISK MONITOR", color=ACCENT, fontsize=22,
             fontweight="bold", family="monospace")
    fig.text(0.94, 0.94, ctx["date"], color=MUTED, fontsize=16, ha="right",
             family="monospace")

    # Headline metric: label, then the huge percentage.
    fig.text(0.06, 0.81, "97.5% Expected Shortfall (1-day)", color=MUTED,
             fontsize=20)
    fig.text(0.06, 0.62, f"{ctx['es_975']:.2%}", color=FG, fontsize=110,
             fontweight="bold")

    # Regime badge.
    fig.text(0.06, 0.51, f"  {regime.upper()} REGIME  ", color=BG, fontsize=18,
             fontweight="bold", family="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor=rcolor,
                       edgecolor="none"))

    # Secondary stats: a single horizontal row below the badge.
    fig.text(0.06, 0.44,
             f"Stressed ES  {ctx['es_stressed']:.2%}     "
             f"Liquidity-adj ES  {ctx['liquidity_adjusted_es']:.2%}     "
             f"Top mover  {ctx['top_mover']} {ctx['top_mover_return']:+.2f}%",
             color=MUTED, fontsize=14)

    # Sparkline of trailing ES, full width across the lower half.
    if len(hist) >= 2:
        ax = fig.add_axes([0.06, 0.13, 0.88, 0.23])
        ax.plot(hist["date"], hist["es_975"], color=ACCENT, linewidth=2.5)
        ax.fill_between(hist["date"], hist["es_975"], hist["es_975"].min(),
                        color=ACCENT, alpha=0.12)
        ax.scatter([hist["date"].iloc[-1]], [hist["es_975"].iloc[-1]],
                   color=rcolor, s=60, zorder=5)
        ax.set_facecolor(BG)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=MUTED, labelsize=10)
        ax.set_xticks([])
        ax.set_title(f"97.5% ES, trailing {len(hist)} days", color=MUTED,
                     fontsize=12, loc="left")

    # Footer: two stacked lines so nothing clips at 1200px wide.
    fig.text(0.06, 0.075, REPO_URL,
             color=MUTED, fontsize=11, family="monospace")
    fig.text(0.06, 0.04,
             "6-asset multi-class book   |   Historical Simulation, "
             "FRTB liquidity-horizon scaled",
             color=MUTED, fontsize=11, family="monospace")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=BG)
    plt.close(fig)


def render_card_wide(ctx: dict, hist: pd.DataFrame, out_png: Path) -> None:
    """Render the article-cover variant at exactly 1920x1080 (16:9 landscape).

    Same content as ``render_card`` rearranged for LinkedIn's Article cover
    dimensions, so when this image is used as an article header it displays
    cleanly on both feed and article views without cropping.

    Inputs:
        ctx:     daily context dict (date, es_975, regime, top_mover, ...).
        hist:    trailing ES history for the sparkline (oldest-first).
        out_png: destination path for the PNG.
    Output:  None. Side effect: the PNG is written at exactly 1920x1080.
    """
    regime = str(ctx["volatility_regime"]).lower()
    rcolor = REGIME_COLOR.get(regime, ACCENT)

    fig = plt.figure(figsize=(19.2, 10.8), dpi=100)
    fig.patch.set_facecolor(BG)

    # Header band: brand left, date right.
    fig.text(0.04, 0.91, "FRTB IMA RISK MONITOR", color=ACCENT, fontsize=26,
             fontweight="bold", family="monospace")
    fig.text(0.96, 0.91, ctx["date"], color=MUTED, fontsize=18, ha="right",
             family="monospace")

    # Left column: headline label + huge percent.
    fig.text(0.04, 0.74, "97.5% Expected Shortfall (1-day)", color=MUTED,
             fontsize=22)
    fig.text(0.04, 0.48, f"{ctx['es_975']:.2%}", color=FG, fontsize=140,
             fontweight="bold")

    # Regime badge below the headline number.
    fig.text(0.04, 0.32, f"  {regime.upper()} REGIME  ", color=BG, fontsize=20,
             fontweight="bold", family="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor=rcolor,
                       edgecolor="none"))

    # Secondary stats: one row, fits comfortably across the left half at 1920px.
    fig.text(0.04, 0.22,
             f"Stressed ES  {ctx['es_stressed']:.2%}     "
             f"Liquidity-adj ES  {ctx['liquidity_adjusted_es']:.2%}     "
             f"Top mover  {ctx['top_mover']} {ctx['top_mover_return']:+.2f}%",
             color=MUTED, fontsize=16)

    # Sparkline on the right half.
    if len(hist) >= 2:
        ax = fig.add_axes([0.55, 0.27, 0.40, 0.50])
        ax.plot(hist["date"], hist["es_975"], color=ACCENT, linewidth=2.5)
        ax.fill_between(hist["date"], hist["es_975"], hist["es_975"].min(),
                        color=ACCENT, alpha=0.12)
        ax.scatter([hist["date"].iloc[-1]], [hist["es_975"].iloc[-1]],
                   color=rcolor, s=70, zorder=5)
        ax.set_facecolor(BG)
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(colors=MUTED, labelsize=11)
        ax.set_xticks([])
        ax.set_title(f"97.5% ES, trailing {len(hist)} days", color=MUTED,
                     fontsize=14, loc="left")

    # Footer: one line fits comfortably at 1920px.
    fig.text(0.04, 0.08,
             f"{REPO_URL}   |   6-asset multi-class book   |   "
             "Historical Simulation, FRTB liquidity-horizon scaled",
             color=MUTED, fontsize=12, family="monospace")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, facecolor=BG)
    plt.close(fig)


# Voice spec for caption generation. Mirrors how Marks writes on LinkedIn:
# memo-style title, no jargon hand-holding, no hashtags, ends with the explainer
# link. The strict rules carry through to the templated fallback below.
VOICE = (
    "You are Marks Guo, a Statistics + Finance junior at the University of "
    "Rochester. You built this small FRTB IMA risk-monitoring project as a "
    "learning exercise and ongoing recruiting signal, and you write LinkedIn "
    "updates about it in your own voice. The voice is first-person, direct, "
    "confident: a junior analyst sharing a working note, NOT a tutorial.\n"
    "\n"
    "STRICT rules:\n"
    "- NEVER use em-dashes or en-dashes, and no double-hyphen '--'. Use commas, "
    "periods, parentheses, or words like 'so' / 'which' instead.\n"
    "- DO NOT define or explain common technical terms inline. Terms like "
    "Expected Shortfall, FRTB, stressed regime, liquidity horizon, VaR, "
    "Acerbi-Szekely, backtest are used directly without a parenthetical "
    "definition. Assume the reader either knows them or will click the "
    "explainer link at the end. One short clarifying parenthetical is OK if "
    "it adds insight rather than a textbook definition, e.g. '(which adds "
    "extra holding-period penalties for assets that are harder to sell "
    "quickly)' is fine. 'Expected Shortfall (basically the average loss on "
    "the worst days)' is NOT fine.\n"
    "- No dramatic analyst openers. No 'Markets kept risk managers honest', "
    "no 'The real story sits in...', no 'X is not decoration, it is Y in "
    "disguise'.\n"
    "- No conversational openers like 'Quick weekly update on...' or 'Caught "
    "something interesting today...'. Start with the title line on its own, "
    "blank line, then dive straight into the numbers.\n"
    "- DO NOT include hashtags anywhere in the post.\n"
    "- End the post with this exact line on its own (and nothing after it): "
    f"'Definitions and Interpretations: {EXPLAINER_URL}'.\n"
    "- Use ordinary text formatting, no asterisks or markdown bolding around "
    "individual numbers."
)


def _format_metrics(ctx: dict, backtest: dict | None) -> str:
    """One-paragraph data block handed to Claude. All numbers are facts to use."""
    date_pretty = ctx["as_of"].strftime("%B %d, %Y")
    lines = [
        f"Date (ISO): {ctx['date']}",
        f"Date (display, use this in titles): {date_pretty}",
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
    """Templated caption when no Claude key is configured. Same voice/format."""
    date_pretty = ctx["as_of"].strftime("%B %d, %Y")
    if mode == "weekly":
        bt = ""
        if backtest:
            bt = (f" On the backtest side this week: "
                  f"{str(backtest['pass_fail']).upper()}, with "
                  f"{int(backtest['exceptions_count'])} VaR exceptions.")
        return (
            f"Weekly FRTB IMA Risk Desk Recap | Week Ending {date_pretty}\n\n"
            f"The headline number today is a 97.5% Expected Shortfall of "
            f"{ctx['es_975']:.2%}. Stressed ES is {ctx['es_stressed']:.2%}, "
            f"and the liquidity-adjusted figure comes out to "
            f"{ctx['liquidity_adjusted_es']:.2%}, so the gap from base to "
            f"liquidity-adjusted is meaningful.\n\n"
            f"The model is currently flagging a {ctx['volatility_regime']} "
            f"volatility regime, which tends to push capital requirements "
            f"higher across the board.\n\n"
            f"Biggest mover today was {ctx['top_mover']} at "
            f"{ctx['top_mover_return']:+.2f}%, worth watching into next week."
            f"{bt}\n\n"
            f"Definitions and Interpretations: {EXPLAINER_URL}"
        )
    # Event mode.
    headline = events[0]["headline"] if events else "Notable risk move today"
    return (
        f"FRTB IMA Risk Desk Update | {date_pretty}\n\n"
        f"{headline}. The 97.5% Expected Shortfall is now "
        f"{ctx['es_975']:.2%}, with stressed ES at {ctx['es_stressed']:.2%} "
        f"and liquidity-adjusted ES at {ctx['liquidity_adjusted_es']:.2%}. "
        f"Market regime is {ctx['volatility_regime']}, and the day's biggest "
        f"mover was {ctx['top_mover']} at {ctx['top_mover_return']:+.2f}%.\n\n"
        f"Definitions and Interpretations: {EXPLAINER_URL}"
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
            "voice. The first line MUST be the title, exactly in this format "
            "(use the display date from the metrics block):\n"
            "'Weekly FRTB IMA Risk Desk Recap | Week Ending Month DD, YYYY'\n"
            "Leave one blank line, then dive straight into the numbers (no "
            "'Quick weekly update' or other opener). Cover, briefly: today's "
            "97.5% Expected Shortfall headline number, how the stressed and "
            "liquidity-adjusted numbers compare and what that gap implies, the "
            "regime and what it implies, the biggest mover with one short line "
            "of interpretation, the backtest result if it is informative, and "
            "one curious forward-look. End with the required "
            "'Definitions and Interpretations:' line. Total length around 180 "
            "to 240 words."
        )
    else:
        headlines = "; ".join(e["headline"] for e in events) or "a notable risk move"
        ask = (
            "Write a punchy LinkedIn update in this voice about today's "
            f"notable risk signal(s): {headlines}. The first line MUST be the "
            "title, exactly in this format (use the display date from the "
            "metrics block):\n"
            "'FRTB IMA Risk Desk Update | Month DD, YYYY'\n"
            "Leave one blank line, then 3 to 5 short sentences covering what "
            "happened, the relevant numbers, and one short line of "
            "interpretation. End with the required "
            "'Definitions and Interpretations:' line. Total length around 80 "
            "to 140 words."
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
                png_name: str, cover_name: str) -> Path:
    """Write the draft markdown file with the caption and a review checklist.

    Inputs:
        ctx:        daily context dict.
        caption:    the generated caption text.
        events:     today's events.
        mode:       'event' or 'weekly'.
        png_name:   filename of the in-feed square image (same folder).
        cover_name: filename of the article-cover (16:9) image (same folder).
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
        f"**Image (feed post, 1200x1200):** `{png_name}`  ",
        f"**Image (article cover, 1920x1080):** `{cover_name}`  ",
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
             "Auto-generated ready-to-post packages. Review the `.md`, then post "
             "with the matching feed image, or use the cover image when posting "
             "as a LinkedIn Article.", ""]
    for d in drafts:
        cover = DRAFTS_DIR / f"{d.stem}_cover.png"
        cover_link = (f" + [article cover]({cover.name})" if cover.exists() else "")
        lines.append(
            f"- **{d.stem}** - [caption]({d.name}) + "
            f"[feed image]({d.stem}.png){cover_link}"
        )
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
    cover = DRAFTS_DIR / f"{date}_cover.png"
    render_card(ctx, hist, png)
    render_card_wide(ctx, hist, cover)
    caption = write_caption(ctx, events, mode, backtest=backtest)
    md = write_draft(ctx, caption, events, mode, png.name, cover.name)
    update_index()
    print(f"[build_post] {date}: wrote draft ({mode}) -> "
          f"{md.name} + {png.name} + {cover.name}")


if __name__ == "__main__":
    main()
