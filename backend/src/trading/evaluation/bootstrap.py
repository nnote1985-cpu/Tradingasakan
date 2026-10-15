"""Paired moving-block bootstrap — BUILDPLAN v4.2 §17.5."""

from __future__ import annotations

import numpy as np


def moving_block_bootstrap_means(d: np.ndarray, *, block: int, reps: int, seed: int) -> np.ndarray:
    """Bootstrap distribution of mean(d) by concatenating random contiguous blocks
    of length ``block`` and truncating to len(d)."""
    d = np.asarray(d, dtype=float)
    n = len(d)
    if n == 0:
        raise ValueError("empty series")
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(reps, n_blocks))
    offsets = np.arange(block)
    idx = (starts[:, :, None] + offsets[None, None, :]).reshape(reps, -1)[:, :n]
    return d[idx].mean(axis=1)


def lower_bound(d: np.ndarray, *, alpha: float, block: int, reps: int, seed: int) -> float:
    """One-sided lower (1-alpha) percentile bound of the mean paired difference."""
    means = moving_block_bootstrap_means(d, block=block, reps=reps, seed=seed)
    return float(np.quantile(means, alpha))
