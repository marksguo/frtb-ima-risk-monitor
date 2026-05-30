# Plain-English Explainer

*A guide to what this project is, what every number means, and how to read the daily updates — written for people who haven't seen "FRTB" or "Expected Shortfall" before.*

---

## 30-second version

This is a small program that wakes up every weekday after the US market closes, looks at how a mock portfolio of 6 different investments moved that day, and answers one question: **"If tomorrow goes badly, how bad could it get?"** It's a miniature version of the same daily check that big banks are legally required to run, hand-built end-to-end so the whole stack is visible.

---

## How banks think about risk

Imagine you have $1,000 invested. Tomorrow your $1,000 will be worth something different — maybe $1,010, maybe $980, maybe (rarely) $850. You don't know which.

Banks have **billions** at stake, not $1,000, so post-2008 regulators force them to answer one question every single day: *"On a really bad day, how much could we lose?"* If the answer is bigger than they can comfortably absorb, they have to set aside extra cash as a safety cushion. That cushion is called **regulatory capital** — money the bank legally can't touch, just in case.

This project is a miniature, hand-built version of that same daily check, on a small synthetic portfolio.

---

## The two key risk numbers: VaR and ES

These are the two ways the industry measures "bad day risk."

### VaR (Value at Risk) — *the speed limit*

**VaR at 97.5%** answers: "There's a 97.5% chance my loss tomorrow will be smaller than this number."

Sort every daily move from the last year worst-to-best. The 97.5% VaR is the dividing line between *typical days* and the *worst 2.5% of days.*

The problem with VaR: it tells you where the line is, but **nothing about how bad it gets past the line.** A loss just past the line and a catastrophic loss can have the same VaR.

### ES (Expected Shortfall) — *the average crash*

**ES at 97.5%** answers: "If we DO have one of those worst-2.5% days, on average how big is the loss?"

ES is the **average of all the bad outcomes past the VaR line.** It captures how bad the disasters actually get, not just how often they happen.

After 2008, regulators decided VaR alone wasn't safe enough and made the industry switch to ES. **That switch is essentially what FRTB is.**

---

## What is FRTB?

**FRTB = Fundamental Review of the Trading Book.** Don't be intimidated by the name — it's just a set of banking rules from 2019 that tell big banks how to compute their daily "how much could we lose" number. Three things changed under FRTB that this project demonstrates:

1. **Use ES instead of VaR** (the "look past the line" upgrade).
2. **Account for the fact that some investments take longer to sell** (the liquidity-horizon thing — more below).
3. **Use a crisis-period calibration, not just calm-period** — capital must hold up in a real downturn, not just on a sunny day.

The **"IMA"** in this project's name = **Internal Models Approach**, FRTB-speak for "the bank built its own model." (The alternative is the "Standardised Approach," a paint-by-numbers formula the regulator hands you.) IMA gives more accurate numbers but the bank has to *prove* the model works — that's what the weekly backtest does.

---

## What's in the portfolio?

A **"book"** in trading just means a portfolio. This project's book holds equal amounts of six ETFs, each chosen to represent a different *flavor* of market risk:

| Ticker | What it tracks (plain English) |
|---|---|
| **SPY** | The US stock market (S&P 500) |
| **TLT** | US government bonds (interest-rate risk) |
| **HYG** | Risky corporate bonds (credit risk) |
| **EEM** | Emerging-market stocks (China, India, Brazil, etc.) |
| **GLD** | Gold |
| **UUP** | The US dollar's strength vs. other currencies |

These six together cover most ways a portfolio can move — when stocks crash, gold often rises; when interest rates spike, TLT moves; etc. Small but *diverse*, so the risk numbers behave like a realistic portfolio's would.

---

## Why three different ES numbers?

The daily card shows three. They build on each other, like layers of stress-testing:

1. **97.5% ES** — the *base* number, calculated on the last 252 trading days. "If tomorrow is a worst-2.5% day, you lose about this much on average."

2. **Stressed ES** — same calculation, but using a **historically scary window** (the worst stretch in recent history, like the 2020 COVID crash). The idea: regulatory capital should hold up in *real* crises, not just calm markets. So we recompute assuming "the world looked like March 2020."

3. **Liquidity-adjusted ES** — stressed ES, inflated to account for the fact that **some things you can't sell in a single day**. Examples:
   - SPY: tradeable in an hour. Liquidity horizon = **10 days** (regulator-imposed minimum).
   - HYG (junk bonds): a real fire-sale would take weeks. Liquidity horizon = **40 days**.
   - TLT (long-term Treasuries): institutional unwinds drag on. Liquidity horizon = **60 days**.

   The longer it takes to exit, the more the market can move against you while you're stuck holding the position. The math scales risk by the square root of those days.

**The "gap" between the stressed and liquidity-adjusted numbers is the regulatory cost of holding illiquid stuff.** That's the headline story of FRTB.

---

## What does "regime" mean?

The system classifies every day into one of three buckets based on how *jumpy* the market has been recently (the last 20 days' realized volatility vs. its long-run average):

- **Normal** — current volatility is below 80% of the long-run average. Calm waters. *(Mint badge.)*
- **Elevated** — between 80% and 130%. Choppy. *(Amber badge.)*
- **Stressed** — above 130%. The market is being unusually wild. *(Coral badge.)*

The badge on the daily card is color-coded so you don't have to read text to know the situation.

---

## The weekly backtest

A **backtest** is a self-check: *"if we had been using this model last week, did its predictions match what actually happened?"*

Every Friday the system runs the **Acerbi-Szekely test** — don't worry about the name, it's a specific recipe for grading ES predictions. It looks back at the past week's daily losses and asks: *"did the model predict the size of losses accurately?"* If yes → **PASS**. If the model badly underestimated risk → **FAIL** (a "breach").

A FAIL is a big deal in real banking — it means the bank's internal model is broken and the regulator can force them to switch to the simpler (more punitive) Standardised Approach.

---

## How to read the daily image card

The PNG you see in posts shows:

- **"FRTB IMA RISK MONITOR"** (top-left, blue) — project brand.
- **The date** (top-right).
- **The huge percent number** — the day's 97.5% ES. The whole image is built so you can absorb this in one second.
- **The colored "REGIME" pill** — coral = stressed, amber = elevated, mint = normal.
- **The line chart on the right** — the 97.5% ES over the last 60 trading days, with today's dot highlighted. Lets you see the trajectory at a glance.
- **The row of secondary stats** — stressed ES, liquidity-adjusted ES, and which asset moved the most that day. The image stands alone if someone only sees the picture.
- **Footer** — the repo URL + the methodology in 5 words.

---

## How to read the daily caption

Most posts will follow this rough flow:

1. **A title** marking the post as part of an ongoing series.
2. **The headline number** in context (today's 97.5% ES).
3. **The three ES layers** (base → stressed → liquidity-adjusted) — this is the FRTB story.
4. **The regime + the day's top mover** — and what real-world story might explain it (e.g., "flight to safety" when gold leads).
5. **A short educational aside** — one quotable line that teaches the concept.
6. **A forward look** — what to watch next.
7. **Hashtags.**

The point of the post is never *just* the numbers — it's the *interpretation*. Numbers tell you what; the post tries to tell you *why* it matters.

---

## Where everything lives

- **Live dashboard:** https://frtb-ima-risk-monitor.onrender.com/
- **Repo (code + methodology notebook):** https://github.com/marksguo/frtb-ima-risk-monitor
- **Daily log (last 30 trading days):** [`DAILY_LOG.md`](./DAILY_LOG.md)
- **All draft posts the bot has generated:** [`social/drafts/`](./social/drafts/)
- **Automated runs:** the [Actions tab](https://github.com/marksguo/frtb-ima-risk-monitor/actions) shows every daily run.

---

## Questions?

If anything here is still fuzzy, open an issue on the repo or reach out — I'd rather over-explain than have someone bounce off the jargon.
