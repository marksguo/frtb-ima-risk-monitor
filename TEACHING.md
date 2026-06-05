# Teaching Guide: The FRTB IMA Risk Monitor, From Scratch

This guide explains the whole project to someone smart who has never seen any of
these words before. No finance background is assumed. Every acronym is spelled
out the first time it appears, and there is a full glossary at the end.

The golden rule for understanding this project: **every piece exists because the
piece before it left a question unanswered.** If you can retell that chain of
questions, you understand the project.

---

## Table of contents

1. [The one question everything answers](#1-the-one-question-everything-answers)
2. [The vocabulary you need first](#2-the-vocabulary-you-need-first)
3. [The portfolio and the data](#3-the-portfolio-and-the-data)
4. [VaR: where is the edge of the cliff?](#4-var-where-is-the-edge-of-the-cliff)
5. [ES: how far is the fall?](#5-es-how-far-is-the-fall)
6. [The three layers of stress](#6-the-three-layers-of-stress)
7. [Regime: is the market calm or wild?](#7-regime-is-the-market-calm-or-wild)
8. [Proving the model works, part 1: backtesting](#8-proving-the-model-works-part-1-backtesting)
9. [Proving the model works, part 2: the PLA test](#9-proving-the-model-works-part-2-the-pla-test-in-depth)
10. [Turning risk into capital](#10-turning-risk-into-capital)
11. [The Scenario Lab](#11-the-scenario-lab-in-depth)
12. [Sensitivity analysis](#12-sensitivity-analysis)
13. [How it runs itself](#13-how-it-runs-itself)
14. [Glossary of every acronym](#14-glossary-of-every-acronym)
15. [The 30-second version](#15-the-30-second-version)

---

## 1. The one question everything answers

A bank holds huge amounts of investments. Tomorrow those investments will be
worth a different amount than today. Sometimes a little more, sometimes a little
less, and on rare bad days, a *lot* less.

After the 2008 financial crisis, regulators (the government bodies that police
banks) made a rule: every bank must answer, every single day, **"On a bad day,
how much could we lose?"** If that number is large, the bank has to lock away a
pile of cash it is not allowed to touch, as a safety cushion. That cushion is
called **regulatory capital**.

There is a second, subtler requirement. A bank is allowed to use its *own* model
to compute that loss number (which usually lets it hold less cash), but only if
it can **prove the model actually works**. If it cannot prove it, the bank is
forced to use a cruder, more expensive government formula instead.

This project is a complete, hand-built miniature of that entire daily process.

Two acronyms unlock the project's name:

- **FRTB = Fundamental Review of the Trading Book.** This is just the name of the
  2019 rulebook that says *how* banks must compute their daily loss number. The
  name is not important; think of it as "the modern rules for measuring trading
  risk."
- **IMA = Internal Models Approach.** This is the harder path where the bank
  builds its own model and must prove it works. The easier path is the
  **SA = Standardised Approach**, a fill-in-the-blanks formula the regulator
  hands you. The project does both and compares them.

Everything below is one link in the chain of questions that flow from "how much
could we lose, and can we trust our own answer?"

---

## 2. The vocabulary you need first

A few plain-English terms used throughout:

- **Return:** the percentage change in price from one day to the next. If
  something goes from $100 to $99, its return is -1%.
- **Portfolio / book:** your collection of investments. "Book" is just trader
  slang for portfolio.
- **P&L = Profit and Loss:** how much money you made or lost. A daily P&L of
  -2% means the book lost 2% of its value that day.
- **ETF = Exchange-Traded Fund:** a single tradable thing that bundles many
  investments. Buying one share of an ETF called SPY gives you a slice of all
  500 big US companies at once. ETFs are a convenient way to hold a whole
  category of the market in one ticker.
- **Tail:** the rare, extreme outcomes. The "left tail" is the rare big losses.
  Almost all of risk management is about the left tail.

---

## 3. The portfolio and the data

You cannot measure risk on nothing, so the project invents a small pretend
portfolio (a "synthetic book") of six ETFs, each chosen to represent a *different
kind* of market risk:

| Ticker | What it is | Type of risk it represents |
|--------|-----------|----------------------------|
| SPY | The US stock market (S&P 500) | Stocks |
| TLT | US government bonds | Interest rates |
| HYG | Risky corporate bonds | Credit (companies defaulting) |
| EEM | Emerging-market stocks | Developing economies |
| GLD | Gold | Commodities |
| UUP | The US dollar's strength | Currencies (FX) |

They are held in equal amounts (one sixth each).

**Why six diverse things instead of one?** Because risk lives in how they move
*together*. When stocks crash, gold often rises; when interest rates spike, bonds
move; the dollar often rises when everything else falls. A single stock would be
a boring toy. Six diverse, real assets behave like a genuine portfolio, which is
the whole point.

**The data:** a program downloads the real daily prices of these six ETFs going
back to 2007 and stores them in a database. That stored history is the single
source of truth every other part of the project reads from. Without a stored
history, nothing else (especially the "did our past predictions come true?"
checks) would be possible.

---

## 4. VaR: where is the edge of the cliff?

**VaR = Value at Risk.** It is the most basic "bad day" number.

Here is the recipe the project uses (called **Historical Simulation**, meaning
"just look at what actually happened"):

1. Take the last 252 trading days of the portfolio's returns. (252 is roughly one
   year of weekdays.)
2. Sort them from worst to best.
3. Find the line that separates the worst 2.5% of days from the rest.

That line is the **97.5% VaR**. In plain words: *"On 97.5% of days, my loss will
be smaller than this. Only 1 day in 40 should be worse."*

**The problem VaR leaves unsolved:** VaR tells you *where the cliff edge is* but
nothing about *how far down the fall goes*. A merely-bad day and a total
catastrophe can sit at the exact same VaR line, because VaR only marks the edge.
This blind spot is a big reason banks were caught off guard in 2008. That gap is
exactly what the next piece fixes.

---

## 5. ES: how far is the fall?

**ES = Expected Shortfall.** It answers the question VaR could not: *"If we do
have one of those worst-2.5% days, how bad is it on average?"*

ES is the **average of all the losses beyond the VaR line.** Where VaR marks the
edge of the cliff, ES tells you the average depth of the fall past that edge.

Because of this, ES "sees" catastrophes that VaR hides, and it has a nice
mathematical property: diversifying your portfolio can never make ES look worse
(that is not always guaranteed for VaR). For both reasons, the FRTB rules
**replaced VaR with ES** as the official measure. The whole modern rulebook is,
in one sentence, "use ES, and look past the cliff edge."

The project still computes VaR too, because VaR is what you backtest against and
it is a useful reference. But ES is the headline number.

> **Teach it as:** VaR asks *where is the edge of the cliff*. ES asks *how far
> down is the fall*. FRTB made everyone switch to the second question.

---

## 6. The three layers of stress

The daily report shows ES three times, each more conservative than the last.
They build on one another like coats of armor.

1. **Plain 97.5% ES.** Computed on the last year of returns. "If tomorrow is a
   bad day, here is the average loss."

2. **Stressed ES.** The same calculation, but recalibrated as if the world looked
   like its *worst historical stretch* (for example, the 2008 crash or March
   2020). **Why:** any safety cushion looks fine in calm weather. Regulators care
   whether it survives a real storm, so the number is blended with history's
   worst window. This is the project's `es_stressed`.

3. **Liquidity-adjusted ES.** Stressed ES, inflated to account for the fact that
   **some things take a long time to sell.** You can dump SPY in an hour, but
   unloading a big pile of risky bonds could take weeks, and the market can keep
   moving against you the whole time you are stuck. Each asset gets a
   "liquidity horizon" (days to exit), and its risk is scaled up by the square
   root of that horizon.

**The single most important idea in FRTB lives here:** the *gap* between the
stressed number and the liquidity-adjusted number is the regulatory cost of
holding hard-to-sell things. That gap is the headline story of the whole
rulebook.

A related acronym you will see in the code: **NMRF = Non-Modellable Risk Factor.**
This flags any risk for which there is not enough clean price data to model it
reliably. Real FRTB adds a penalty for these. In this project all six ETFs are
liquid and easy to model, so it is a flag rather than a charge, and the README
says so honestly.

---

## 7. Regime: is the market calm or wild?

A risk number alone is hard to interpret. "ES is 1.56%" means nothing unless you
know whether the market is currently calm or chaotic. So the project labels every
day with a **regime**:

- **Normal:** recent volatility (how jumpy prices have been over the last 20 days)
  is well below its long-run average. Calm. (Green.)
- **Elevated:** between 80% and 130% of the long-run average. Choppy. (Orange.)
- **Stressed:** above 130%. Unusually wild. (Red.)

"Volatility" just means how much prices have been bouncing around. The regime
turns a bare number into a *situation* you can read at a glance.

---

## 8. Proving the model works, part 1: backtesting

Remember the second requirement: a bank can use its own model only if it can
**prove it works.** This is where most amateur projects stop, and where this one
keeps going. There are two completely different ways to "prove it," and you need
both.

**Backtesting** is the first. A backtest asks: *"If we had been using this model
last year, did its predictions match what actually happened?"*

Every Friday the project runs the **Acerbi-Szekely test** (named after the two
people who invented it). Plain version: it looks back over the past year, finds
the days where losses were supposed to be extreme, and checks whether the *size*
of those losses matched the ES the model had predicted. If the model's predicted
losses were about right, it **passes**. If the model badly underestimated how big
the losses would be, it **fails** (a "breach").

A failure is serious. In real banking, repeated failures let the regulator revoke
the bank's permission to use its own model.

The project is honest about a real weakness here: the simple Historical Simulation
model fails a large share of its weekly backtests, because it reacts slowly when
markets suddenly get jumpy. That honesty is itself a feature. It is *why* the
project also includes fancier models (like one that weights recent days more
heavily) to show how the pass rate could improve. Two more named checks,
**Kupiec** and **Christoffersen**, grade the VaR from other angles (are there too
many bad days? do they cluster together?).

> **Teach it as:** A backtest asks *were my predicted loss sizes right?*

---

## 9. Proving the model works, part 2: the PLA test (in depth)

Backtesting checks whether your numbers were *big enough*. But there is a sneakier
way a model can be wrong: it could be **watching the wrong things entirely** and
still happen to produce reasonable-sized numbers. The test that catches this is
the **PLA test**, and it is worth understanding deeply.

**PLA = P&L Attribution.** ("Attribution" means "explaining where something came
from.") It is a required part of getting IMA approval under the FRTB rules.

### The core idea

There are two ways to measure how much money the book made or lost on a given day:

1. **HPL = Hypothetical P&L.** The *full, true* answer. Take all six holdings and
   add up exactly what each one did. Nothing left out, nothing approximated. This
   is reality.

2. **RTPL = Risk-Theoretical P&L.** The *model's shorthand* answer. A real risk
   model does not track every tiny detail of every position. It represents the
   book using a smaller set of core "risk factors." In this project, the model
   pretends it only really understands two big drivers: the **stock market**
   (represented by SPY) and **interest rates** (represented by TLT). RTPL is the
   P&L you get if you explain the whole book using *only* those two factors.

### Why comparing them matters

If the shorthand (RTPL) closely matches the full truth (HPL), then the model's
two factors really do capture what drives the book, and you can trust its risk
numbers. If the two drift apart, the model is **missing something important**.

In this book, gold (GLD) and the dollar (UUP) do not move with stocks or rates,
so a two-factor model that ignores them will produce an RTPL that disagrees with
reality on the days those assets move. That disagreement is the signal the test
is built to catch.

### How the test grades the match

It compares the two daily P&L series two different ways, then takes the worse of
the two grades:

1. **Spearman correlation.** This measures whether HPL and RTPL *move in the same
   order*: on days the true P&L was relatively bad, was the model's P&L also
   relatively bad? A score near 1.0 means they rank days almost identically; a
   low score means they disagree about which days were good or bad. ("Spearman"
   is just the name of this particular correlation, which compares rankings
   rather than raw sizes.)

2. **KS distance.** **KS = Kolmogorov-Smirnov**, named after two mathematicians.
   It measures how different the *overall shapes* of the two P&L distributions
   are. A small number means the spread of model P&L looks like the spread of
   real P&L; a large number means one is much more spread out than the other.

Each score lands in a colored zone:

- **Green:** the model tracks reality well. The desk keeps its IMA approval.
- **Amber:** good but not great. The desk can keep using its model but must hold
  *extra* capital as a penalty.
- **Red:** the model and reality have parted ways. The desk **loses IMA approval**
  and is forced onto the cruder Standardised Approach.

### A real result from this project

On the live data, the two-factor model scores a Spearman of about **0.78** (just
below the 0.80 green cutoff) and a KS of about **0.05** (comfortably green).
Because the desk's zone is the *worse* of the two, the overall verdict is
**amber**: the model is good but the gold and dollar moves it ignores are enough
to cost it the top grade. This is a genuinely realistic outcome, and it is a
great teaching example because it shows the test actually discriminating rather
than rubber-stamping.

### One honesty note to always include

In a real bank, RTPL comes straight out of the pricing computers. This project
builds RTPL with a simplified statistical fit instead. So it faithfully
reproduces the *mechanism* the test polices (ignore important risks and your
grade drops) rather than a bank's exact internal number. Saying this out loud is
the kind of honesty that makes the project credible.

> **Teach it as:** Backtest asks *were my predictions the right size?* PLA asks
> *is my model even looking at the right risks?* You need both, which is exactly
> why regulators require both.

---

## 10. Turning risk into capital

All of this exists to set one number: how much cash the bank must lock away.

- The **SA = Standardised Approach** number comes from the regulator's
  fill-in-the-blanks formula. In this project it is a simplified version of the
  official **SBM = Sensitivities-Based Method**, where each position gets a fixed
  "risk weight" and the charges are added up.
- The **IMA** number comes from the bank's own model (built on the
  liquidity-adjusted stressed ES from earlier). It is usually *lower*, which is
  the reward for building a good model.

But regulators do not fully trust internal models, so they add the **Basel III
output floor** (Basel is the city in Switzerland where these international banking
rules are written). The floor says: *your final capital is the larger of your own
number and 72.5% of the government formula's number.* In other words, a good model
can save you money, but only down to a floor. The project shows **which approach
binds** (sets the final number) on any given day.

---

## 11. The Scenario Lab (in depth)

Everything up to here tells you where risk sits *today*, based on what has already
happened. But risk managers also need to ask **"what if?"** about things that have
*not* happened yet. That forward-looking, invent-your-own-disaster tool is the
**Scenario Lab**, and it is the project's interactive centerpiece.

### What problem it solves

The "stressed ES" from section 6 only ever looks *backward* at real history. It
cannot answer "what if emerging markets fall 12% tomorrow and volatility doubles?"
because that exact combination may never have happened. Professional risk systems
(like the ones banks pay millions for) all let you build hypothetical shocks and
watch the numbers react. Before the Scenario Lab, this project could not.

### How it works, conceptually

The lab gives you two knobs:

1. **A volatility dial.** Crank up how jumpy the market is. Setting it to 2x means
   "imagine the market suddenly became twice as choppy as it is right now." Under
   the hood, it stretches the spread of recent returns by that factor, which fattens
   the tail and pushes VaR and ES up.

2. **A directional shock to one asset class.** Pick a category (emerging-market
   stocks, gold, credit, and so on) and drop it by some amount on the spot, for
   example "emerging markets fall 12% overnight." Under the hood, this adds one
   brand-new worst-case day to the history, which pulls the risk numbers toward
   that shock.

The instant you move a slider, **every headline number recomputes live**: VaR,
ES, the stressed and liquidity-adjusted versions, the regime light, and the
capital. You see the whole chain react at once.

### A concrete walkthrough

Start from a calm day where ES is about 1.56% and the regime light is orange
("elevated"). Now set the volatility dial to 1.8x and shock emerging markets down
12%. On the live data, the result is:

- ES jumps from **1.56% to about 2.87%**.
- VaR roughly doubles.
- The liquidity-adjusted ES nearly doubles.
- The regime light flips from **orange to red ("stressed")**.

So in one motion you watch a hypothetical shock cascade through every risk
measure, and you literally see the market tip into a "stressed" regime. That turns
a static report into something you can poke at and learn from, which is exactly
how understanding is built.

### Why it had to be fast

A naive version recomputed the entire history on every slider move and took about
20 seconds, which would feel broken. The project recomputes only the most recent
day (the only one that matters for "today's risk") and reads the historical
stress level from storage instead of recalculating it. That drops each update to
about 10 milliseconds, so the sliders feel instant. This is a nice engineering
lesson on its own: knowing *what you do not need to recompute* is half of making
something interactive.

> **Teach it as:** The Scenario Lab is a "what-if machine" for disasters that are
> not in the history books. You invent the shock; it shows you the damage.

---

## 12. Sensitivity analysis

The Scenario Lab is about *big, sudden* shocks. **Sensitivity analysis** is the
calmer, everyday cousin: *"how does my risk number respond to small changes, and
which positions are responsible for it?"* It answers two questions.

### Question 1: who owns the risk?

The project computes, for each of the six assets:

- **Marginal VaR:** if I added a little more of this one position, how much would
  the *whole portfolio's* VaR move? This is the position's sensitivity.
- **Component VaR:** how much of *today's total* VaR is this position responsible
  for? These add up exactly to the portfolio VaR (a clean mathematical fact), so
  they partition the risk across the book like slices of a pie.

A real result from the live data: gold (GLD) and emerging markets (EEM) together
account for over **80%** of the portfolio's risk, while the dollar (UUP) has a
**negative** contribution. A negative number is the fun part: it means the dollar
is a **hedge.** Adding more of it actually *lowers* total portfolio risk, because
it tends to rise when the others fall. Spotting hedges this way is exactly what a
real desk does before deciding what to buy or sell.

### Question 2: how much does the answer depend on our choices?

The project also shows a small grid of VaR and ES computed at different confidence
levels (95%, 97.5%, 99%) and different look-back windows (six months, one year,
two years). This reveals how much the headline number depends on *modelling
choices* rather than the market itself. If the number swings wildly when you
change the window, that is a warning that the result is fragile.

> **Teach it as:** Sensitivity analysis asks *which positions own the risk* (and
> which ones secretly reduce it), and *how much does our answer depend on the
> knobs we chose?*

---

## 13. How it runs itself

The final layer is what makes this a *system*, not a one-off script. Every
weekday, automatically, the project: downloads fresh prices, recomputes every
number above, re-runs the validations, updates a running log and a change
scorecard, refreshes the live web dashboard, and even writes a plain-English
summary using an AI model. It does all of this with no human involved, and it is
covered by automated tests that run on every code change.

The reason this matters: the gap between "I wrote some math in a notebook" and "I
built a system that runs itself, checks itself, and explains itself" is the gap
between a homework assignment and something that looks like real industry
software.

---

## 14. Glossary of every acronym

- **FRTB = Fundamental Review of the Trading Book.** The 2019 rulebook for how
  banks measure trading risk.
- **IMA = Internal Models Approach.** Using your own model (must be proven), which
  usually lowers required capital.
- **SA = Standardised Approach.** The regulator's fill-in-the-blanks formula; the
  fallback when your model is not trusted.
- **VaR = Value at Risk.** The loss level you only expect to exceed on the rare
  worst days (here, the worst 2.5%).
- **ES = Expected Shortfall.** The *average* loss on those worst days; the measure
  FRTB uses instead of VaR.
- **P&L = Profit and Loss.** How much money was made or lost.
- **HPL = Hypothetical P&L.** The full, true daily P&L of the book.
- **RTPL = Risk-Theoretical P&L.** The P&L the risk model can explain from its
  chosen factors.
- **PLA = P&L Attribution.** The test comparing HPL and RTPL to check the model
  watches the right risks.
- **KS = Kolmogorov-Smirnov.** A statistic measuring how different two
  distributions are; used inside the PLA test.
- **Spearman correlation.** A correlation based on rankings; the other half of the
  PLA test.
- **NMRF = Non-Modellable Risk Factor.** A risk with too little data to model
  reliably; FRTB penalizes these.
- **SBM = Sensitivities-Based Method.** The formula behind the Standardised
  Approach capital charge.
- **EWMA = Exponentially Weighted Moving Average.** A way of weighting recent days
  more heavily; used in one of the comparison models.
- **ETF = Exchange-Traded Fund.** A single tradable thing that bundles many
  investments.
- **FX = Foreign Exchange.** Currency risk (the dollar vs other currencies).
- **Basel III.** The international banking-rules package (named after Basel,
  Switzerland) that includes the output floor.
- **Marginal VaR / Component VaR.** How much one position moves total VaR, and its
  additive share of total VaR.

---

## 15. The 30-second version

> Banks must answer "how much could we lose tomorrow?" and "can we be trusted to
> model that ourselves?" I built the full pipeline that does it: it pulls real
> market data, computes Expected Shortfall the way the FRTB rules require,
> stress- and liquidity-adjusts it, proves the model with backtests and a P&L
> attribution test, turns the result into regulatory capital, and serves it all
> on a live dashboard where you can invent your own market shocks and see the
> risk react. Every piece exists because the one before it left a question
> unanswered.

That paragraph is your elevator pitch. Every section above is the longer answer
when someone asks "tell me more about that part."
