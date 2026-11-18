# Order book collection (BTCUSDT spot + perp)

Started 2026-11-18 at Note's request so order book signals can be backtested after several months.
No order book history existed before this; free tick-level history is hard to get, so we record it ourselves.

## What is recorded

- `scripts/orderbook_collect.py`, run on the Oracle VM by `tradingasakan-orderbook.service`.
- Every 10 s (clock-aligned): top 20 levels of BTCUSDT from Binance Spot `/api/v3/depth` and USD-M `/fapi/v1/depth`.
  Public REST, no API keys. Request weight about 5 per minute per market, far below the limits.
- One JSON line per snapshot: `t` slot time (ms UTC), `lt` receive time, `u` lastUpdateId, `mid`, `spr_bps`,
  `imb5`/`imb20` (bid−ask qty over top 5/20 levels, normalised), `b`/`a` = `[price, qty]` levels.
- Files: `/var/lib/tradingasakan-orderbook/{spot,perp}/BTCUSDT/YYYY-MM-DD.jsonl`; earlier days are gzipped
  (`.jsonl.gz`) at UTC midnight and on start. A failed fetch writes nothing, so gaps show as missing slots.
- Disk: about 2 MB/day/market compressed, so about 1.5 GB/year for both. Writing pauses below 2 GB free.
- Not recorded: trades (aggTrades history is downloadable from data.binance.vision later), full-depth diff stream.

## Isolation from the workers

Its own systemd unit, system `/usr/bin/python3` with the standard library only (no `uv` env, no env file, no keys),
`Nice=10`, `MemoryMax=200M`, `CPUQuota=20%`. Stopping or crashing it does not touch the Testnet workers.

## Install / update on the VM

```bash
sudo -u trading git -C /opt/tradingasakan pull --ff-only
sudo -u trading /usr/bin/python3 /opt/tradingasakan/scripts/orderbook_collect.py --out /tmp/ob-test --once   # expect {"spot": "ok", "perp": "ok"}
sudo cp /opt/tradingasakan/deploy/systemd/tradingasakan-orderbook.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now tradingasakan-orderbook
```

Check: `systemctl status tradingasakan-orderbook --no-pager`, `journalctl -u tradingasakan-orderbook -n 20 --no-pager`,
`du -sh /var/lib/tradingasakan-orderbook; df -h /`.

## Research use

Wait until there are several months (aim ≥ 3) before any backtest, and split the data in time order
(dev / holdout) before looking at it, as with the BTC C1/C2 rule. Order book signals usually work on
seconds-to-hours horizons, unlike R10a's daily decisions.
