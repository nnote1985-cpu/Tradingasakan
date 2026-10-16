"""The pathA-v1.2 daily runner's journal and Thai report (no network)."""

import importlib.util
from pathlib import Path

import pandas as pd

from tests.unit.test_pathA_v12 import falling, stamps
from trading.shadow.rebalance_book import PathABook

_spec = importlib.util.spec_from_file_location("paper_trade_v12", Path("scripts/paper_trade_v12.py"))
assert _spec and _spec.loader
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def test_journal_and_report_show_alerts_halt_and_trades(tmp_path):
    daily, hourly = falling()
    book = PathABook()
    r, dn = stamps(daily.index[0])
    book.open(str(daily.index[0].date()), 1.0, r, dn, ["open"])
    added, problems = book.advance(daily, hourly)
    runner.write_journal(book, tmp_path / "journal.md")
    text = (tmp_path / "journal.md").read_text()
    assert "เปิดพอร์ต" in text and "หยุด (HALT)" in text and "แจ้งเตือน" in text and "SELL" in text
    report = runner.render_th(book.performance(), added, problems)
    assert "HALT DRAWDOWN" in report and "รอ Note สั่งเริ่มใหม่" in report
    trade = next(e for e in added if e["kind"] == "trade")
    assert pd.Timestamp(trade["at"]) > pd.Timestamp(trade["decided_at"])        # paper_run's timing check can see it


def test_decision_inputs_are_pathA_v11s():
    assert runner.decision_inputs.__module__ == "paper_trade_v11"
