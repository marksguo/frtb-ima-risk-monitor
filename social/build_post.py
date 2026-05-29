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


def _fallback_caption(ctx: dict, events: list[dict], mode: str) -> str:
    """Assemble a templated caption when no Claude API key is available."""
    lead = (events[0]["headline"] if events
            else f"Weekly risk recap | {ctx['date']}")
    body = (f"The synthetic 6-asset FRTB book sits at a 97.5% Expected Shortfall of "
            f"{ctx['es_975']:.2%} under a '{ctx['volatility_regime']}' volatility regime; "
            f"stress-calibrated ES is {ctx['es_stressed']:.2%} and the liquidity-adjusted "
            f"figure {ctx['liquidity_adjusted_es']:.2%}. Top mover: {ctx['top_mover']} "
            f"({ctx['top_mover_return']:+.2f}%).")
    return f"{lead}\n\n{body}\n\n#RiskManagement #FRTB #Quant #MarketRisk"


def write_caption(ctx: dict, events: list[dict], mode: str) -> str:
    """Generate the LinkedIn caption (Claude when configured, else a template).

    Inputs:
        ctx:    daily context dict.
        events: today's events (may be empty on the weekly digest day).
        mode:   'event' or 'weekly'.
    Output:  the caption text.
    """
    if not api_key_configured():
        return _fallback_caption(ctx, events, mode)

    client = get_client()
    if mode == "weekly":
        instruction = (
            "Write a ~150-word weekly LinkedIn post recapping this synthetic FRTB IMA "
            "risk book's week. Cover the current Expected Shortfall and what the "
            "volatility regime implies, and end with one forward-looking insight."
        )
    else:
        headlines = "; ".join(e["headline"] for e in events) or "a notable risk move"
        instruction = (
            "Write a punchy 2-3 sentence LinkedIn post about today's notable risk "
            f"event(s): {headlines}. Explain what happened and why it matters for FRTB "
            "capital. Be specific with the numbers."
        )
    prompt = (
        "You are a quantitative risk analyst posting to LinkedIn. "
        f"{instruction}\n"
        f"Metrics: date {ctx['date']}, 97.5% ES {ctx['es_975']:.4f}, stressed ES "
        f"{ctx['es_stressed']:.4f}, liquidity-adjusted ES {ctx['liquidity_adjusted_es']:.4f}, "
        f"regime {ctx['volatility_regime']}, top mover {ctx['top_mover']} "
        f"{ctx['top_mover_return']}%.\n"
        "Tone: sharp, professional, educational, accessible to finance students. "
        "Do not use em-dashes. End with 3-4 relevant hashtags."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=500,
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
    finally:
        engine.dispose()

    png = DRAFTS_DIR / f"{date}.png"
    render_card(ctx, hist, png)
    caption = write_caption(ctx, events, mode)
    md = write_draft(ctx, caption, events, mode, png.name)
    update_index()
    print(f"[build_post] {date}: wrote draft ({mode}) -> {md.name} + {png.name}")


if __name__ == "__main__":
    main()
