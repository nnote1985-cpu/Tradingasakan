# R8: order-book depth and open interest as a filter on path A (pre-declared)

Written 2026-10-16, before any depth or metrics data was downloaded or looked at. Note asked
whether the bots should look at the order book, and chose "ลองเลย" on existing data first, without
spending C1/C2. Exploratory: it can say whether this is worth a confirmation run, not prove an edge.

## Question

Does cutting path A's BTC weight when bid depth is thin relative to ask depth (and, in one variant,
when open interest is also rising fast) improve path A's risk-adjusted result?

Hypothesis, declared before seeing data: a day where resting buy orders within 2% of the price are
unusually small compared with sell orders has less support under the price, and rising open
interest at the same time means more leveraged positions that can be forced out. Both point to a
higher chance of a sharp fall, so holding less BTC on those days should lower drawdown more than it
costs in return.

## Data (Binance USD-M BTCUSDT, data.binance.vision, checksums verified)

| Set | Fields used | Range downloaded |
| --- | --- | --- |
| `bookDepth` daily files | `timestamp`, `percentage` (−2 and +2), `notional` | 2023-01-01 → 2025-12-31 |
| `metrics` daily files | `create_time`, `sum_open_interest_value` | 2023-01-01 → 2025-12-31 |
| 1h klines + funding (as all path A work) | daily close, SMA100, 7-day funding | 2022-01-01 → 2025-12-31 |

C1 (2020-04 → 2022-01) and C2 (2026-01 → 2026-10) are not downloaded or used.

## Features (known at the close of UTC day d; only rows timestamped inside day d)

- `imb_d` = mean over day d's snapshots of (B − A) / (B + A), where B is `notional` at
  percentage −2 and A at +2 (cumulative resting orders within 2% below / above the price).
- `oi_d` = the last `sum_open_interest_value` of day d; `oi7_d` = oi_d / oi_{d−7} − 1.
- Thresholds are trailing and include day d: `lo_imb_d` = 20th percentile of imb over days
  d−89 … d; `hi_oi_d` = 80th percentile of oi7 over days d−89 … d. A threshold needs at least 60
  valid days in its window, otherwise the filter is off (neutral) that day.
- A day with no depth (or no OI) file, or fewer than 100 snapshots, is missing: that filter is
  neutral that day. Missing days are counted and reported.

## Rules (two trials, nothing else)

Base weight `w_d` = pathA-v1 / v1.2 rule, unchanged: (close > SMA100) × (0.5 if 7-day mean funding
> 0.09%/day else 1), from `trading.alerts.risk_check.signals`.

- **R8a depth:** if imb_d < lo_imb_d then w_d × 0.5.
- **R8b depth + OI:** if imb_d < lo_imb_d **and** oi7_d > hi_oi_d then w_d × 0.5.

Weights can be 0, 0.25, 0.5 or 1. Decided at day d's close, filled at day d+1's open
(`funding_overlay_v3.simulate_weights`), BTC spot model with perp prices as proxy, 10,000 USDT,
fee 0.10% + 2 bps per trade, as in the pathA holdout test.

## Window

Evaluation 2023-04-01 → 2026-01-01 (the first 90 days of 2023 warm up the thresholds). Also
reported per calendar year (2023 from April, 2024, 2025). This window overlaps the data path A was
built and tested on, so this is a screen, not a clean out-of-sample test.

## Pass criteria (per variant, on the full window)

1. **Better than path A:** Sharpe(R8x) > Sharpe(path A) **and** max DD(R8x) ≤ max DD(path A)
   **and** net(R8x) > 0.
2. Reported against Note's objective as well (net > 0 and Sharpe > B&H or max DD ≤ ½ B&H), but the
   decision to go further rests on criterion 1, since the filter can only improve on path A.

Also reported: number of filtered days, trades, fees, the result with costs ×2, and missing days.
A variant that passes is a candidate for a confirmation run on C2 (Note's separate approval) and,
after the Phase 5 day-28 review, a forward paper book. A variant that fails is recorded and not
retuned (no other percentiles, depth bands or windows are tried on this data).
