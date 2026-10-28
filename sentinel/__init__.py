"""Sentinel Trader — a controlled, self-improving trading research and execution system.

Design rules that make it different from a typical "AI trading bot":
  1. The AI never writes executable code. It edits a typed JSON strategy spec that is
     schema-validated and evaluated by a fixed engine (no look-ahead possible).
  2. Hard risk limits live in config/risk_limits.yaml and are enforced in code the
     research loop cannot reach.
  3. Every experiment is counted. The Deflated Sharpe Ratio penalises the number of
     trials, so "try 500 variations until one looks good" fails the gate.
  4. A holdout slice of history is never shown to the research loop. It is used once,
     at live promotion, and that use is logged.
  5. Going live always requires a human.
"""

__version__ = "0.1.0"
