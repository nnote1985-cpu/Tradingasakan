# R21: cross-asset check on ETH, period ETH-1 (pre-declared)

Written 2026-11-17, before any R21 backtest was run. Note asked whether ETH's history can be used in
separate periods, and approved ("โอเค และเพิ่มเทส future ไปด้วย") a cross-asset check that includes
the futures variants. Every rule was built on BTC; ETH has never been simulated by any of them.

## Periods (fixed now)

- **ETH-1: 2018 → 2021** — used now, once.
- **ETH-2: 2022 → 2025** — reserved for the next rule version, if rules change after ETH-1.
- **ETH 2026** — reserved for the final confirmation together with BTC C2 (Note's OK needed).
No ETH file after 2021-12-31 is downloaded here.

ETHUSDT USDⓈ-M perp data (and funding) start 2019-11-27, so:
- **ETH-1F (futures and mixed): 2020-04-15 → 2022-01-01** (perp from 2019-12-01, 100-day warm-up),
  the same dates as BTC C1. R17 needs SMA200: 2020-08-01 → 2022-01-01.
- **ETH-1S (spot only, the full period): 2018-04-11 → 2022-01-01** on spot daily klines from
  2018-01; no funding data exists before late 2019, so R10a runs without its funding half-size there.

## Runs (BTC parameters exactly, no retuning, one run each)

On ETH-1F, using the R15/R17/R19/R20 code with the symbol switched to ETHUSDT:
1. Spot only: R10a (band ±2% on SMA100, half when funding hot).
2. Futures only: R15 F1 gated long/short 1x.
3a. Mixed, hedge: R20 s = 1.0 (R10a's share in plain spot, the rest in delta-neutral carry).
3b. Mixed, net short: R17 short sleeve beside R10a (SL/TP on the exchange, 1% risk, caps).
Base: R19 K0 always-on carry. Benchmark: ETH spot buy & hold.
On ETH-1S: R10a spot (no funding rule) and B&H.

## Reported

Same table as the BTC three-way comparison: annual return, max DD, Sharpe, non-negative quarters and
years. Read: a rule "holds on ETH" if its ranking against B&H and against spot R10a matches BTC's. No
pass/fail threshold is added; ETH-1 is a consistency check, not a tuning set.
