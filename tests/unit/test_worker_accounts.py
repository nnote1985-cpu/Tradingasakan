"""Testnet P6: a second account's worker keeps its own local files (testnet-p6-plan.md §4, PR 2)."""

from __future__ import annotations

import pytest

from scripts import testnet_worker as tw
from trading.runtime.worker import WorkerRefused


def test_default_keeps_the_original_paths():
    var, bars = tw.account_paths("DEFAULT")
    assert var == tw.VAR and bars == tw.ROOT / "var" / "ingest" / "bars"


def test_another_account_gets_its_own_paths():
    var, bars = tw.account_paths("AI")
    assert var == tw.ROOT / "var" / "worker-AI" and bars == tw.ROOT / "var" / "ingest-AI" / "bars"
    assert var != tw.VAR


@pytest.mark.parametrize("bad", ["", "../x", "a b", "x" * 33, "AI/.."])
def test_an_unsafe_account_name_is_refused(bad):
    with pytest.raises(WorkerRefused):
        tw.account_paths(bad)
