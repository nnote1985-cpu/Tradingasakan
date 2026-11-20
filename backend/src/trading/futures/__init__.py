"""USDⓈ-M Futures desk on Binance Demo Trading (docs/futures-desk-plan.md).

Stage 1 is read-only: a separate worker syncs the Demo account (balances,
positions, open orders, fills, funding) into Supabase, and the web page shows
per-trade P&L, open positions, liquidation prices and the standing risk rules.
"""
