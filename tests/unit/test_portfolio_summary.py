from datetime import UTC, datetime, timedelta

import pytest

from scripts import portfolio_summary as ps

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)          # 17:00 Bangkok
C21 = datetime(2026, 11, 21, 23, 59, 59, 999000, tzinfo=UTC)
C22 = C21 + timedelta(days=1)
OLD = datetime(2026, 11, 12, 23, 59, 59, tzinfo=UTC)

PAPER = [
    ("pathA-r10a/buy_and_hold", 10000, C22, 7400.0, 1), ("pathA-r10a/buy_and_hold", 10000, C21, 7359.42, 2),
    ("pathA-r10a/paper", 10000, C22, 8400.0, 1), ("pathA-r10a/paper", 10000, C21, 8388.40, 2),
    ("spot-e1-v1/paper", 10000, C21, 10000.0, 1),                                  # late: only 11-21 synced
    ("pathA-p5-r1/code", 10000, OLD, 8677.9, 1),                                   # ended book, not "paper"
    ("pathA-p5-r1/paper", 10000, OLD, 8677.9, 1),                                  # ended book
]
BOOKS = [
    ("DEFAULT", "tn-conformance-v1", 200, 200.78, datetime(2026, 10, 19, tzinfo=UTC), None),
    ("DEFAULT", "tn-r10a-code-v1", 2000, 1990.69, NOW - timedelta(minutes=1), None),
    ("BOT1", "bot-efe97e9e", 2000, 2000, NOW - timedelta(hours=3), "BOTR10a v2"),
]
FUT = [("FUT1", NOW - timedelta(minutes=1), 4975.52, 0, 0, -10.51)]


def test_paper_lines_day_total_bh_and_filters():
    lines, close = ps.paper_lines(PAPER, NOW)
    assert close == "22/11"
    by = {ln.key: ln for ln in lines}
    assert set(by) == {"paper:pathA-r10a", "paper:spot-e1-v1"}               # ended p5 book left out
    r = by["paper:pathA-r10a"]
    assert r.value == 8400.0 and r.day == pytest.approx(11.60) and r.total == pytest.approx(-1600)
    assert r.extra == "B&H -26.00%" and r.warn == ""
    e1 = by["paper:spot-e1-v1"]
    assert e1.day is None and "21/11" in e1.warn                             # stale close is flagged


def test_book_lines_use_previous_report_and_skip_dead_books():
    lines = ps.book_lines(BOOKS, NOW, {"book:DEFAULT/tn-r10a-code-v1": 1995.0})
    assert [ln.name for ln in lines] == ["DEFAULT · tn-r10a-code-v1", "BOT1 · BOTR10a v2"]
    d, b = lines
    assert d.day == pytest.approx(-4.31) and d.total == pytest.approx(-9.31) and d.warn == ""
    assert b.day is None and "worker" in b.warn


def test_futures_total_is_income_plus_unrealized():
    (f,) = ps.futures_lines(FUT, NOW, {"fut:FUT1": 4980.0})
    assert f.total == pytest.approx(-10.51) and f.start == pytest.approx(4986.03)
    assert f.day == pytest.approx(-4.48) and f.extra == "ไม่มี position" and f.warn == ""


class _Cur:
    def __init__(self):
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.rows = PAPER if "equity_snapshots" in sql else BOOKS if "exec_book_risk" in sql else FUT

    def fetchall(self):
        return self.rows


class _Conn:
    def cursor(self):
        return _Cur()


def test_build_renders_all_sections_and_state_round_trip(tmp_path):
    text, live = ps.build(_Conn(), NOW, {})
    assert "23/11/2026 17:00 เวลาไทย" in text
    assert "📝 Paper book (ปิดวัน 22/11" in text and "🧪 Spot Testnet bot" in text and "📈 Futures Demo" in text
    assert "มูลค่า 8,400.00 USDT" in text and "+11.60 (+0.14%)" in text and "-1,600.00 (-16.00%)" in text
    assert "ยังไม่มี" in text                                                  # first run: no 1-day for live ports
    assert len(text) < 4096
    state = tmp_path / "s.json"
    ps.save_state(state, NOW, live)
    assert ps.load_state(state) == {"book:DEFAULT/tn-r10a-code-v1": 1990.69, "book:BOT1/bot-efe97e9e": 2000.0,
                                    "fut:FUT1": 4975.52}
    assert ps.load_state(tmp_path / "missing.json") == {}


def test_main_refuses_without_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert ps.main(["--dry-run"]) == 2


def test_3a_is_named_and_listed_first():
    rows = PAPER + [("pathA-r10a-hedge/paper", 10000, C22, 9993.7, 1)]
    lines, _ = ps.paper_lines(rows, NOW)
    assert lines[0].name == "3a (R10a + short hedge) · pathA-r10a-hedge"
    assert lines[1].name == "R10a spot · pathA-r10a"
