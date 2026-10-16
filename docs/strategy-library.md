# Strategy library and bot profiles

Written 2026-10-16 at Note's request: a numbered library of strategies, and bot profiles that
pick strategies by number, so a bot can switch sets (for example 1, 2, 3 → 2, 4, 6) by writing a
new profile version. Design notes: `/mnt/project-files/designs/strategy-library-and-bot-profile.md`.

This is structure only. No strategy here has been backtested on our data, no profile is attached
to a paper book, and nothing changes the running books (pathA-v1.1, v1.2, p5-r1, p5-r2,
futures-r7). Running a profile forward is a separate decision.

## Library (`backend/src/trading/strategies/library/catalog.py`)

Every strategy maps closed bars to a BTC spot target weight in {0, 0.5, 1}: long or flat, 1x.

| # | Strategy | Default timeframe | Default params |
| --- | --- | --- | --- |
| 1 | MACD Crossover | 4h | fast 12, slow 26, signal 9 |
| 2 | MACD Divergence | 4h | MACD 12/26/9, lookback 14 |
| 3 | RSI Overbought/Oversold | 4h | period 14, oversold 30, overbought 70 |
| 4 | RSI Re-entry | 4h | period 14, oversold 30, overbought 70 |
| 5 | EMA + MACD Trend Following | 1d | trend EMA 200, MACD 12/26/9 |
| 6 | Momentum (rate of change) | 1d | lookback 20, threshold 0 |
| 7 | SMA trend regime (the R3 / path A trend rule) | 1d | SMA 100 |
| 8 | Donchian breakout | 4h | entry 20, exit 10 |
| 9 | RSI2 (Connors) ¹ | 1d | RSI 2 ≤ 10 above SMA 200, exit above SMA 5 |
| 10 | Bollinger Bands breakout ¹ | 4h | BB 20 × 2 on (high+low)/2, Ichimoku 9/26/52/26 filter |
| 11 | Dual Thrust ¹ | 4h | range 21 bars, anchor 6 bars, up 0.71, down 0.67 |
| 12 | KDJ ¹ | 1d | 9 / 3 / 3 |
| 13 | Turtle Rules, System 1 ¹ | 1d | entry 20, exit 10, ATR 20, add at +0.5 ATR |

¹ Long side of [jesse-ai/example-strategies](https://github.com/jesse-ai/example-strategies)
(MIT, `docs/third_party/jesse-example-strategies-LICENSE.txt`), rewritten for closed bars and
{0, 0.5, 1} weights. The originals' stops, ATR position sizing and shorts are left out; each
rule's docstring in `catalog.py` says where it differs (for example Dual Thrust's anchor and
Turtle's two steps 0.5 → 1 instead of up to 4 units).

Rules:

- Timeframes 1h, 4h and 1d, built from 1h bars; a 4h or 1d bar needs every hour inside it.
- Signals use valid bars only; the weight holds through an invalid bar; 0 until indicators exist.
- A signal at a bar uses that bar and earlier bars only (tested by truncating the data).
- Numbers are never reused. Changing a rule means a new number or a new `version`.
- Every parameter has bounds; a profile cannot set a value outside them.

## Bot profiles (`configs/profiles/*.yaml`, `trading.strategies.library.profile`)

```yaml
profile_id: btc-trend-mix
version: 1
name: BTC trend mix
status: draft                # draft | active | retired
combine: average             # or main_with_filters
strategies:
  - number: 5
    timeframe: 1d
  - number: 7
    params: {sma: 100}
risk_profile: spot.v1        # the only allowed value for BTC spot
ai_mode: "off"               # the only allowed value for now
```

- **Switching strategies:** copy the file, change the `strategies` list, bump `version`. Once a
  profile runs a paper book, a new version is a new book, as with v1.1 → v1.2.
- **Combining:** `average` takes the mean of the weights rounded down to 0, 0.5 or 1;
  `main_with_filters` uses the first strategy's weight, or 0 when any other strategy says 0.
- **Decision clock:** daily close (00:00 UTC), the same as the path A books. A 4h strategy
  counts with its last 4h bar closed at or before that time.
- The profile hash covers everything that changes behaviour (not name, notes or status) with
  default params filled in.
- Unknown fields, unknown numbers, duplicates, bad timeframes and out-of-bound params are refused.

## Web

Tab "กลยุทธ์" (`#strategies`) lists the profiles and the library, read-only, from
`GET /api/v1/bot-profiles` and `GET /api/v1/strategies`. Editing in the browser is not built:
a profile change is a reviewed file change with a new version.

## Not built yet

- A paper book that runs a profile (after the Phase 5 day-28 review, Note's call).
- Backtests of the library (plan R8 in the design notes; only pre-declared runs).
- AI suggesting a profile (`ai_mode` other than "off").
