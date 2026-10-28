# Sentinel Trader

A **controlled** self-improving trading system for US stocks (and defined-risk options).
An AI researcher proposes strategy changes. A fixed engine tests them. A strict gate rejects anything that looks like luck. Only you can send a strategy to real money.

Runs on your laptop or a small cloud VM. Exports winning strategies to **TradingView Pine Script v5**.

> Not financial advice. Most automated strategies lose money once they go live. Paper-trade for weeks before you risk capital, and only risk what you can afford to lose.

---

## How it improves on the typical "AI trading bot"

| Typical design | Sentinel |
|---|---|
| LLM writes Python strategy code | LLM edits a **typed JSON spec**; a fixed engine runs it. No arbitrary code, and no look-ahead is possible (signal at close, fill at next open) |
| "Keep what backtests better" | 9-check gate: OOS Sharpe, OOS drawdown, OOS/IS decay, walk-forward consistency, parameter robustness (±20%), survives 2× costs, **Deflated Sharpe** that tightens with every trial, plus min trade counts |
| Validation data reused every loop | **Locked holdout** (last 20% of history) never seen by research; used once at live promotion, and every use is logged |
| Risk rules inside the strategy | Hard limits in `config/risk_limits.yaml`, enforced on every order, unreachable by the AI. Exits always allowed |
| Auto-deploy to live | Auto-promotion to **paper** only. Live needs: paper first → holdout pass → typed approval → `live_trading_enabled: true` |
| Stops managed by the bot | **Bracket orders**: stop and target rest at the broker, so a sleeping laptop can't strand a position |
| — | Kill switch, daily-loss halt, drawdown halt, broker-error circuit breaker, stale-data guard, alerts |

```
 market data ──► research engine (diagnose by regime) ──► AI agent proposes JSON spec
                                                            │
       experiment memory (every trial counted) ◄── validation gate (holdout untouched)
                                                            │ pass + beats champion
                                                   paper champion ──► daily run-once
                                                            │          │ risk engine (hard limits)
                                you: holdout test + typed approval      ▼
                                                   live champion ──► broker (bracket orders)
```

## Findings (October 2026)

**Result: no strategy earned the right to trade.** Every candidate was rejected, including the one
strategy that went through the one-time holdout test. The system did what it was built to do: it
refused to promote a backtest that would most likely have lost to simply holding the same assets.

**Setup.** Daily bars from 2005 to 2026 via yfinance. Splits are about 60% development (2005–2018),
20% validation (about 2018–2022) and 20% untouched holdout (about 2022–2026). Costs are 2 bps spread
plus 3 bps slippage per fill. The benchmark is equal-weight buy & hold of the same universe over the
same window. "OOS" means the validation window.

| Stage | Universe | Strategy | OOS Sharpe | Buy & hold Sharpe | Max DD (strategy vs B&H) | Verdict |
|---|---|---|---|---|---|---|
| 1 | 8 mega-caps, 2015+ | Trend pullback (baseline) | 0.23 | — | 12.2% | Fail |
| 1 | 8 mega-caps, 2015+ | Pullback, no EMA-touch rule | **1.18** | — | 13.7% | Failed only Deflated Sharpe |
| 2 | 8 mega-caps, 2005+ | Same "winner", longer history | **0.69** | 0.73 | 16.4% vs 31.7% | Collapsed: the 1.18 came from one lucky window |
| 2 | 8 mega-caps, 2005+ | Trend pullback (baseline) | 0.60 | 0.73 | 12.9% vs 31.7% | Fail |
| 3 | 15 ETFs | Trend pullback | 0.07 | 0.52 | 7.1% vs 29.0% | Fail; lost money at 2× costs |
| 4 | 15 ETFs | Trend + momentum (Faber; Moskowitz et al.) | 0.54 (CAGR 4.2%) | 0.52 (CAGR 7.5%) | 9.0% vs 29.0% | Same risk-adjusted return, much less return |
| 5 | 15 ETFs | 19 Claude-proposed candidates | 0.14–0.88 | 0.52 | — | All failed; best = trend + ADX < 25 |
| 6 | 15 ETFs | **Holdout test** of `8f78fe1a9217` (trend + ADX < 25) | 0.73 (CAGR 5.3%) | 1.11 (CAGR 13.6%) | 9.8% vs 13.5% | **Not confirmed** |

There were 45 counted experiments in total: 16 on the stock universe and 29 on the ETF universe.
Re-tests on new data and duplicates were not counted.

**What we learned**

- **Short history manufactures winners.** The best result from 2015 onward (Sharpe 1.18) fell to 0.69
  once history went back to 2005. Without the longer test it would have looked tradable.
- **Searching inflates the best result.** After 29 trials on one validation window, a Sharpe near 1.0
  is about what luck alone produces. The Deflated Sharpe check rejected the 0.88 near-miss for that
  reason, and the clean holdout agreed: it beat zero (93% confidence) but lost clearly to buy & hold.
- **Being in cash is the hidden cost of trend filters.** They cut drawdowns sharply in 2008-type markets,
  but in a strong market the time spent in cash costs more than the protection is worth. Over the holdout
  years, buy & hold had a smaller worst drawdown than the strategy's historical advantage implied, and
  more than twice its return.
- **Short-horizon swing rules on daily bars lose to costs.** Pullback and RSI(2) mean-reversion variants
  failed on every universe.
- **An LLM researcher is useful but no shortcut.** Claude proposed varied, well-reasoned, cited ideas and
  recognised dead ends. Every idea is still a trial, though, and the statistics decide.

**Status:** the ETF holdout has been used up. Any further research on this setup needs new out-of-sample
data, meaning time spent paper trading, or a genuinely different data source or universe.

## Layout

```
sentinel/
  data.py            yfinance | alpaca | synthetic daily bars
  features.py        EMA, SMA, RSI, ATR, ATR%, ATR% rank, ADX, ROC, highest/lowest, volume z
  strategy.py        JSON spec language, validation, signals
  backtest.py        portfolio engine: next-open fills, costs, ATR stops/targets, risk sizing
  metrics.py         Sharpe, Sortino, drawdown, PF, Probabilistic + Deflated Sharpe
  validation.py      the gate + one-shot holdout check
  memory.py          SQLite: experiments, champions, holdout log, audit events
  research/          diagnose.py · agents.py (Claude + offline) · loop.py
  risk.py            hard limits, kill switch, halts, circuit breaker
  options.py         signal + IV regime -> defined-risk structure -> contract selection
  brokers/           paper · alpaca (paper/live) · ticket (manual / Robinhood)
  execution.py       one daily trading cycle
  pine.py            TradingView Pine Script v5 exporter
  report.py          HTML status page
config/settings.yaml      tunables      config/risk_limits.yaml   HARD limits (edit by hand)
strategies/               baseline spec  deploy/                   laptop schedulers
```

## Quick start (laptop)

Requires Python 3.11+.

```bash
git clone https://github.com/amishsinha09/sentinel-trader
cd sentinel-trader
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                     # add keys later

python -m unittest discover -s tests                     # 17 tests
python -m sentinel --source synthetic research --iterations 5   # offline dry run
rm -rf state                                             # wipe the dry-run memory
```

Then with real data (free, via yfinance):

```bash
python -m sentinel backtest            # baseline on dev + validation windows
python -m sentinel research            # 10 AI iterations (Claude if ANTHROPIC_API_KEY is set)
python -m sentinel status              # champions, trial count, risk state
python -m sentinel report              # state/report.html
```

## Paper trading → live

1. **Alpaca paper:** open a free account at alpaca.markets, put the *paper* keys in `.env`, set `execution.broker: alpaca_paper`.
2. **Schedule** `deploy/run_daily.sh` (Mac/Linux cron) or `deploy/run_daily.ps1` (Windows Task Scheduler) for **8:35 AM Central, Mon–Fri**. It reads yesterday's close, trades at the open, then writes the report. Keep `research` weekly, not daily.
3. **Paper-trade at least 4–8 weeks.** Compare live paper fills and P&L with what the backtest expected.
4. **Promote:** `python -m sentinel promote <hash> --to live` runs the one-time holdout test and asks you to type the hash.
5. **Unlock:** set `live_trading_enabled: true` in `config/risk_limits.yaml`, put *live* keys in `.env`, and set `execution.broker: alpaca_live`. Start with a small account.

Emergency: `python -m sentinel kill` blocks all new entries (exits still work). `unkill` resets it.

**Laptop caveat:** if the laptop is off or asleep at 8:35, that day's signals are skipped. Open positions stay protected by the broker-side bracket orders. Allow wake-from-sleep (notes in the scripts), or move the same Docker image to a small cloud VM later.

## TradingView

```bash
python -m sentinel pine                # current paper champion -> sentinel_<hash>.pine
python -m sentinel pine --spec strategies/baseline_trend_pullback.json
```

Paste the file into the Pine Editor → *Add to chart* → *Strategy Tester*. Entries and exits carry JSON `alert_message`s, so a TradingView alert on the strategy (with *Webhook URL*) can notify you or drive an external executor. Expect small differences from Python: Pine sets stops from the signal bar's close, while Python uses the next-open fill. If you tune inputs in TradingView, re-validate the new values in Python before trusting them.

## Robinhood

**Scheduled mode.** `.github/workflows/signals.yml` runs `signals` every weekday at 12:05 UTC and commits
`signals/latest.json`. A scheduled Claude session then reads it, gets the real cash, positions and resting
stops from Robinhood, runs `plan --account ... --peak ...`, and places exactly the resulting orders.
To pause all new buys, create an empty file `config/PAUSE` in the repo (sells and stops still work).

Daily flow: run `python -m sentinel --settings config/settings_etf.yaml run-once --broker ticket`, paste the
`TICKET` lines into Claude, and approve each order Claude places through the Robinhood connector. Then update
`state_etf/tickets/positions.json` and `account.json` so the bot knows what you hold. Robinhood can't hold a
stop-loss and a profit target on the same shares, so only the stop-loss is placed.

Robinhood has no public stock-trading API for bots. Use `execution.broker: ticket`. Tickets are treated as **real money**: they need a live-approved strategy and `live_trading_enabled: true`. Each run writes risk-checked order tickets (entry, stop, target) to `state/tickets/`. You place them yourself, or ask Claude to place them through your Robinhood connector with trade approval on.

## Options

Set `execution.options_enabled: true`. A bullish signal maps to a structure by IV regime: call debit spread, long call when conviction is high and IV is low, or bull put credit spread when IV is high. Contracts are chosen for liquidity (OI, bid/ask %), 14–60 DTE, and target deltas, then sized so max loss stays within `options.max_loss_per_trade_pct`. Only defined-risk structures are allowed. Without a broker chain feed, the bot logs the intended structure only. Options backtesting is not modelled yet, so validate option rules on paper first.

## Research profiles and new ideas

```bash
# 15 diversified ETFs (sectors, small caps, international, bonds, gold), separate state_etf/ folder
python -m sentinel --settings config/settings_etf.yaml research

# Start research from a different idea. Scored like any candidate, and counted as a trial.
python -m sentinel --settings config/settings_etf.yaml baseline --spec strategies/trend_momentum.json
```

Included ideas: `trend_pullback` (short-term swing; rejected on both universes in testing) and
`trend_momentum` (hold above the 200-day SMA with positive 6-month momentum, strongest first).
A spec's optional `rank_by` term decides which signals fill the free position slots.

## Watching a rejected strategy on paper (override)

To see a strategy trade with play money even though it failed the checks:

```bash
python -m sentinel --settings config/settings_etf.yaml promote <hash> --to paper --override --reason "observe only"
```

You type the hash to confirm, and the reason is logged. The dashboard marks it **PRACTICE MODE (override)**.
It can never reach live trading this way, and research never silently replaces or demotes it.

## One-time holdout confirmation

If research ends with a near-miss that failed only the Deflated Sharpe check, you may test **one**
strategy, chosen in advance, on the never-used holdout:

```bash
python -m sentinel --settings config/settings_etf.yaml confirm <hash>
```

Pass requires all of these: at least 90% confidence the holdout Sharpe is above zero, a Sharpe at least
equal to equal-weight buy & hold over the same years, max drawdown within limits, and enough trades.
It runs only after you type the hash, and it can run **once per setup**: the holdout is then used up.
A pass makes the strategy the paper strategy; live still needs paper trading plus `promote --to live`.
A fail removes it from paper and blocks it from promotion permanently.

## Extending

- **New indicator:** add one line to `INDICATORS` in `features.py` (it must use only past data) and a mapping in `pine.py`. The AI can use it on the next run.
- **Universe:** edit `universe` in `settings.yaml`. More symbols give more trades and a more meaningful gate.
- **Cloud later:** the same `Dockerfile` runs on AWS ECS Fargate scheduled tasks (EventBridge cron, Secrets Manager for keys, EFS for `state/`) or Azure Container Apps Jobs.

## Known limits

- Daily bars only. Signal-on-close, fill-at-next-open by design.
- Survivorship bias: today's tickers didn't all exist or thrive in 2015. Broaden the universe, and treat results as optimistic.
- Long strategies only in live execution (short specs backtest but won't trade).
- `brokers/alpaca.py` multi-leg options call: verify it against your installed alpaca-py version on paper before any live use.
