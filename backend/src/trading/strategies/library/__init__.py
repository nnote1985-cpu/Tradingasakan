"""Numbered strategy library and versioned bot profiles (docs/strategy-library.md).

Every strategy is a pure function from closed bars to a BTC spot target weight in
{0, 0.5, 1} (long or flat, 1x). A bot profile picks strategies by number, with a
timeframe and parameters inside each strategy's bounds, and combines them into one
daily target weight. Nothing here places orders or touches a running book.
"""
