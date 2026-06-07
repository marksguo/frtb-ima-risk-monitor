# Plain-English Explainer

*A guide to what this project is, what every number means, and how to read the daily updates, written for people who haven't seen "FRTB" or "Expected Shortfall" before.*

---

## Summary

This is a small program that wakes up every weekday after the US market closes, looks at how a mock portfolio of 6 different investments moved that day, and answers one question: **"If tomorrow goes badly, how bad could it get?"** It's a miniature version of the same daily check that big banks are legally required to run, hand-built end-to-end so the whole stack is visible.

---

## How banks think about risk

Imagine you have $1,000 invested. Tomorrow your $1,000 will be worth something different: maybe $1,010, maybe $980, maybe (rarely) $850. 
Banks have **billions** at stake, not $1,000, so post-2008 regulators force them to answer one question every single day: *"On a really bad day, how much could we lose?"* If the answer is bigger than they can comfortably absorb, they have to set aside extra cash as a safety cushion. That cushion is called **regulatory capital**, money the bank legally can't touch, just in case.

---

## VaR and ES

These are the two ways the industry measures "bad day risk."

### VaR (Value at Risk) 

**VaR at 97.5%** answers: "There's a 97.5% chance my loss tomorrow will be smaller than this number."

Sort every daily move from the last year worst-to-best. The 97.5% VaR is the dividing line between *typical days* and the *worst 2.5% of days.*

The problem with VaR: it tells you where the line is, but **nothing about how bad it gets past the line.** A loss just past the line and a catastrophic loss can have the same VaR. This is why VaR was largely retired after a really bad crash, like the 2008 Crisis.

### ES (Expected Shortfall)=

**ES at 97.5%** answers: "If we DO have one of those worst-2.5% days, on average how big is the loss?"

ES is the **average of all the bad outcomes past the VaR line.** It captures how bad the disasters actually get, not just how often they happen.

After 2008, regulators decided VaR alone wasn't safe enough and made the industry switch to ES. **That switch is essentially what FRTB is.**

---

## What is FRTB?

**FRTB = Fundamental Review of the Trading Book.** Name is arbitrary, it's just a set of banking rules from 2019 that tell big banks how to compute their daily "how much could we lose" number. Three things changed under FRTB that this project demonstrates:

1. **Use ES instead of VaR** (the "look past the line" upgrade).
2. **Account for the fact that some investments take longer to sell** (the liquidity-horizon thing (explained more in depth later)).
3. **Use a crisis-period calibration, not just calm-period** (any capital can look good on a sunny day, what matters is how it holds up on a bad day)

The **"IMA"** in this project's name = **Internal Models Approach**, FRTB-speak for "the bank built its own model." (The alternative is the "Standardised Approach," a paint-by-numbers formula the regulator hands you.) Tradeoff is that IMA gives more accurate numbers but the bank has to *prove* the model works (i simulated this with weekly backtests, by comparing my model with last week's results to see if it predicted correctly)

---

## What's in the portfolio?

A **"book"** is just your portfolio. This project's book holds equal amounts of six ETFs, each chosen to represent a different *type* of market risk:

| Ticker | What it tracks (plain English) |
|---|---|
| **SPY** | The US stock market (S&P 500) |
| **TLT** | US government bonds (interest-rate risk) |
| **HYG** | Risky corporate bonds (credit risk) |
| **EEM** | Emerging-market stocks (China, India, Brazil, etc.) |
| **GLD** | Gold |
| **UUP** | The US dollar's strength vs. other currencies |

These six together were hand-picked by me to cover most ways a portfolio can move. When stocks crash, gold often rises; when interest rates spike, TLT moves; etc. Small but *diverse*, so the risk numbers behave like a realistic portfolio's would. The way these interact with each other is important, and being able to interpret them is valuable.

---

## Why three different ES numbers?

The daily card shows three. They build on each other, like layers of stress-testing:

1. **97.5% ES**, the *base* number, calculated on the last 252 trading days (1 year). "If tomorrow is a worst-2.5% day, you lose about this much on average."

2. **Stressed ES**, same calculation, but using a **historically scary window** (the worst stretch in recent history, like the 2020 COVID crash). The idea: regulatory capital should hold up in *real* crises, not just calm markets. So we recompute assuming "the world looked like March 2020."

3. **Liquidity-adjusted ES**, stressed ES, inflated to account for the fact that **some things you can't sell in a single day**. Think about the following:
   - SPY: tradeable in an hour. Liquidity horizon = **10 days** (regulator-imposed minimum).
   - HYG (junk bonds): a real fire-sale would take weeks. Liquidity horizon = **40 days**.
   - TLT (long-term Treasuries): institutional unwinds drag on. Liquidity horizon = **60 days**.

   The longer it takes to exit, the more the market can move against you while you're stuck holding the position. The math scales risk by the square root of those days.

**The "gap" between the stressed and liquidity-adjusted numbers is the regulatory cost of holding illiquid stuff.** That's the headline story of FRTB.

---

## What does "regime" mean?

The system classifies every day into one of three buckets based on how *jumpy* the market has been recently (the last 20 days' realized volatility vs. its long-run average):

- **Normal**, current volatility is below 80% of the long-run average. Calm waters. *(Green badge.)*
- **Elevated**, between 80% and 130%. Choppy. *(Orange badge.)*
- **Stressed**, above 130%. The market is being unusually wild. *(Red badge.)*

The badge on the daily card is color-coded so you don't have to read text to know the situation.

---

## From a risk number to an actual capital figure

All the ES work above eventually has one job: set the size of the safety cushion (the regulatory capital, the money the bank legally can't touch) that the bank has to set aside. Here's how the project turns the daily risk number into that figure, and why there end up being two of them.

First, one name that's about to keep coming up: **Basel III.** That's just the title of the current international rulebook for how banks manage risk and how much capital they must hold. It's written by the Basel Committee, a group of the world's banking regulators that meets in Basel, Switzerland (hence the name), and it's "III" because it's the third major version, the one written after the 2008 crash exposed the holes in the last one. FRTB, the rules this whole project is built around, is one chapter *inside* Basel III. So whenever I say Basel III, picture the big rulebook; FRTB is its trading-risk chapter.

**My model's number (the IMA charge).** "IMA" stands for **Internal Models Approach**, which just means "the bank built its own risk model" (that's this entire project). To turn that into a capital number, I take the liquidity-adjusted stressed ES, the most conservative of the three ES layers from earlier, and multiply it by a safety factor the regulator sets (the minimum allowed is 1.5x). Why multiply at all? Because regulators assume every model is a little optimistic and a little incomplete, so they pad it on purpose. That padded result is the capital my own model says the bank needs.

**The regulator's number (the Standardised Approach, or SA).** FRTB makes every bank *also* compute capital a second, blunter way, using a fixed paint-by-numbers formula. Instead of modelling anything, you take each position, look up a pre-set "risk weight" the regulator assigns to that type of asset (stocks get one number, gold gets another, and so on), multiply, and add them all up. No judgment, no model, just plug and chug. My `standardised_approach.py` file does a simplified version of this.

One bit of jargon from that file is worth unpacking: it covers **delta risk only.** "Delta" is finance-speak for "how much a position's value changes when the underlying price moves a little." My six holdings are all **linear** positions, meaning if the asset goes up 1%, my position goes up 1%, a nice straight-line relationship. The full Standardised Approach also charges capital for two stranger risks (called *vega* and *curvature*) that only appear when you own **options**, which are contracts whose value moves in a bent, non-straight-line way. I don't own any options, so those charges are exactly zero for me and I can safely skip them. I spell that out in the code so it reads as a deliberate scope choice, not something I forgot.

**Why bother computing both?** Because regulators don't fully trust any bank's internal model, a bank has every incentive to build a model that conveniently says "see, we barely need any capital." So Basel III added the **output floor** (named that because it puts a floor under the *output* of your model). The rule: your clever internal-model number is never allowed to drop below **72.5% of the simple Standardised Approach number.** That 72.5% is just a line the regulators drew, "this is the most credit we'll give you for having a fancy model." If your model lowballs past that line, the floor quietly overrides it and you hold more anyway. The dashboard shows both numbers side by side and tells you which one is actually *binding* (the one that wins and sets your real capital). Watching the floor kick in is sort of the whole point of showing both.

(Honesty note, same as everywhere else in this project: a real IMA capital number blends in several extra pieces I left out. There's an **NMRF add-on** (extra capital for "Non-Modellable Risk Factors," meaning risks where there isn't enough clean market data to model them properly, so the regulator makes you hold more just to be safe), a **Default Risk Charge** (capital set aside for the chance that a bond issuer simply goes bankrupt and never pays you back), and some math for how risk diversifies across assets that take different amounts of time to sell. Mine shows the core mechanism, not a bank's exact number, and the code says so out loud.)

---

## The weekly backtest

A **backtest** is just a self-check where we see: *"if we had been using this model last week, did its predictions match what actually happened?"*

Every Friday the system runs the **Acerbi-Szekely test**, a specific recipe for grading ES predictions. You don't need to remember the name, just know that it looks back at the past week's daily losses and asks: *"did the model predict the size of losses accurately?"* If yes → **PASS**. If the model badly underestimated risk → **FAIL** (a "breach").

A FAIL is a big deal in real banking since it means the bank's internal model is broken and the regulator can force them to switch to the simpler (more punitive) Standardised Approach.

---

## The P&L Attribution (PLA) test

The backtest checks whether my risk numbers were *big enough*. The PLA test checks something different: whether my risk model is even looking at the *right things* in the first place.

Here is the idea. There are two ways to measure how the portfolio actually made or lost money on a given day:

- **The full, true answer (HPL):** take every one of the six holdings and add up exactly what each did. Nothing left out.
- **The model's shorthand answer (RTPL):** my risk model does not track all six holdings in full detail. It represents the book with a smaller set of core "risk factors" (here, the stock market and interest rates). RTPL is the P&L you get from *just* those factors.

If the shorthand closely matches the full truth, my model is capturing what really drives the book, and the risk numbers can be trusted. If the two drift apart, the model is missing something. For this book, gold and the dollar do not move with stocks or rates, so they are the usual culprits.

The test grades that match and hands the desk a traffic light:

- **Green:** the model tracks reality well. It stays approved.
- **Amber:** good but not great. The bank can keep using it but has to hold extra capital as a penalty.
- **Red:** the model and reality have parted ways. The desk loses approval and gets bumped to the simpler, more punitive Standardised Approach.

Same stakes as the backtest, just policing a different failure. A model can predict loss sizes fine (pass the backtest) while still watching the wrong risk factors (fail PLA), which is exactly why regulators require both.

*(One honesty note: a real bank's RTPL comes straight out of its pricing engines. Mine is a simplified stand-in built by fitting the book to those two core factors, so it shows the mechanism the test polices rather than a bank's exact internal number.)*

---

## The Scenario Lab (the interactive part)

Everything above tells you where risk sits *today*. The Scenario Lab lets you ask "what if" and watch the numbers move.

It has two knobs:

1. **A volatility dial.** Crank market choppiness up, for example "what if the market got twice as jumpy as it is right now."
2. **A shock to one type of asset.** Pick a risk class (emerging-market stocks, gold, credit, and so on) and drop it on the spot, for example "emerging markets fall 12% overnight."

The moment you move a slider, every headline number recomputes live: the ES, the VaR, the stressed and liquidity-adjusted numbers, the capital, and even the regime light. So you can watch a big enough shock flip the regime from orange to red, or push capital up off its floor. It turns a static report into something you can actually poke at and learn from.

---

## Sensitivity analysis: which positions actually own the risk?

The Scenario Lab asks "what if a big shock hits?" Sensitivity analysis asks the quieter, everyday question a real risk desk lives on: "where is my risk *coming from* right now, and how much should I even trust the number?" There are two views.

**Component VaR (who owns the risk).** Quick reminder: VaR (Value at Risk) is the "bad day" loss line from way back at the top. Here's the catch with a whole portfolio: the total risk is *not* just each holding's risk added up, because the holdings move at different times and partly cancel each other out (when stocks fall, gold often rises, so they offset). **Component VaR** is the trick for splitting today's *total* risk into per-position slices that genuinely add back up to the portfolio number. That lets me say something clean like "GLD (gold) and EEM (emerging-market stocks) together are about 80% of my risk today."

A neat thing falls out of this: UUP (the fund tracking the US dollar) usually shows up with a *negative* risk contribution. A negative contribution means it's acting as a **hedge**, which is finance-speak for "a position that tends to gain exactly when the rest of your book is losing, softening the blow." So instead of adding risk, it's quietly subtracting it. That diversification I mentioned in the portfolio section finally shows up here as a hard, measurable number rather than a hand-wave.

**The parameter grid (how robust is the number).** Every risk number I report secretly depends on two settings I picked: the **confidence level** (97.5%, the "how rare a bad day are we even talking about" dial) and the **look-back window** (252 days, which is about one year of trading, because markets are closed on weekends and holidays there are roughly 252 open days a year, not 365). Neither setting is handed down by nature, I chose them. So the grid recomputes VaR and ES across a whole range of *both* settings at once. The point is to see how much of the headline number is the *actual market* talking versus just *my settings*. If the number barely budges when I change the dials, I can trust it. If it swings wildly, that's a flag to stay humble about it.

Both of these reuse the same **Historical-Simulation** engine as the main pipeline (Historical Simulation = the "just sort the real past year of moves and read the bad tail straight off the data" method) and only crunch the recent window, so they're fast enough to update live on the dashboard right next to the Scenario Lab.

---

## Three ways to compute the same number

Everything above uses **Historical Simulation.** That's the method that makes *no* assumptions about how returns are shaped, it literally just sorts the actual last year of daily moves and reads the bad tail straight off the real data. It's the method FRTB steers banks toward, and it's what every number in this project runs on.

But it's worth seeing it next to the alternatives, so the methodology notebook computes the *same* VaR and ES three different ways and lays them next to each other:

- **Parametric (Normal).** "Parametric" means "assume the data follows a known mathematical shape, then just measure the couple of numbers (the *parameters*) that pin that shape down." Here the assumed shape is the **Normal distribution**, the classic symmetric "bell curve" you've probably seen. It's fast and clean, but real markets have **fat tails**, meaning genuine disasters happen more often than a tidy bell curve predicts. So this method systematically *understates* how bad the bad days get.
- **Historical Simulation.** The production method, described just above. No shape assumed, you simply trust the real data.
- **Monte Carlo on a fat-tailed (Student-t) distribution.** **Monte Carlo** is a technique where, instead of solving the math directly, you let the computer roll the dice and simulate thousands of random possible "tomorrows," then look at how bad the worst results come out. It's named after the famous casino in Monte Carlo, because the whole idea leans on randomness. The dice here are loaded using a **Student-t distribution**, which is basically a bell curve with deliberately fatter tails, so it spits out big crashes at a realistic rate. The odd name has nothing to do with school: the statistician who invented it, William Gosset, worked at the Guinness brewery and wasn't allowed to publish under his real name, so he used the pen name "Student."

The lesson is the comparison itself: the Normal method almost always produces the smallest, most flattering risk number, while Historical and Student-t land larger and much closer to each other. That's exactly why FRTB pushes banks toward assumption-free or fat-tailed measures when real capital is on the line. A risk number that flatters you is not a feature.

---

## The scorecard: tracking how things move

A single day's number is only half the story. What a desk actually watches is the *change*. The band of cards at the top of the dashboard (and the repo's [`CHANGES.md`](./CHANGES.md)) shows each headline metric, the ES numbers, VaR, and the liquidity-adjusted figure, next to how it moved over the last day, week, and month, plus how many days straight the market has been sitting in its current regime. A number holding still is a calm market. The same number jumping 15% in a day is the thing actually worth writing a post about.

---

## How to read the daily image card

The PNG you see in posts shows:

- **"FRTB IMA RISK MONITOR"** (top-left, blue), project name.
- **The date** (top-right).
- **The huge percent number**, the day's 97.5% ES. The whole image is built so you can absorb this in one second.
- **The colored "REGIME" pill**, red = stressed, orange = elevated, green = normal.
- **The line chart on the right**, the 97.5% ES over the last 60 trading days, with today's dot highlighted. Lets you see the trajectory at a glance.
- **The row of secondary stats**, stressed ES, liquidity-adjusted ES, and which asset moved the most that day. The image stands alone if someone only sees the picture.
- **Footer**, the repo URL + the methodology.

---

## How to read the daily caption

Most posts will follow this rough flow:

1. **A formal memo-style title** marking the post as part of an ongoing series (for example *Weekly FRTB IMA Risk Desk Recap | Week Ending June 5, 2026*).
2. **The headline number** in context (today's 97.5% ES).
3. **The three ES layers** (base → stressed → liquidity-adjusted) (This is the main FRTB story).
4. **The regime + the day's top mover**, and what real-world story might explain it (interpreting "why" something changes)
5. **A short educational aside**, something that teaches the concept.
6. **A forward look**, what to watch next going forward, and any practical takeaways.
7. **A link back to this explainer**, so anyone who hit a term they didn't know can get the plain-English version instead of bouncing off the jargon.
8. **Hashtags**, the handful of topic tags at the very end (things like #RiskManagement or #FRTB). On LinkedIn a hashtag is just a searchable label: tagging the post with them is how people who follow or search those topics actually stumble onto it.

The point of the post is not *just* the numbers but also the *interpretation*. Numbers tell you what. but the role of the post tries to tell you *why* it matters.

---

## Where everything lives

- **Live dashboard:** https://frtb-ima-risk-monitor.onrender.com/
- **Repo (code + methodology notebook):** https://github.com/marksguo/frtb-ima-risk-monitor
- **A from-scratch teaching deep-dive (every acronym defined):** [`TEACHING.md`](./TEACHING.md)
- **The change scorecard (1d / 1w / 1m moves):** [`CHANGES.md`](./CHANGES.md)
- **Daily log (last 30 trading days):** [`DAILY_LOG.md`](./DAILY_LOG.md)
- **All draft posts the bot has generated:** [`social/drafts/`](./social/drafts/)
- **Automated runs:** the [Actions tab](https://github.com/marksguo/frtb-ima-risk-monitor/actions) shows every daily run.

---

## Questions?

If anything here is still fuzzy, open an issue on the repo or reach out. I'd rather over-explain than have someone bounce off the jargon, especially if this isn't clear to everyone.
