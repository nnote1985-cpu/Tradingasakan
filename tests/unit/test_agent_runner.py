"""agent-v1 decision runner (docs/agent-book-plan.md §4, step 2) with a scripted model and recorded market data."""

import json
import threading
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace as NS

from trading.agent import market
from trading.agent.roles import DECIDER, OPUS, SONNET
from trading.agent.runner import Budget, Tools, Trigger, call_cost, decide, run_role

NOW = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
HOUR = 3_600_000


def clock() -> datetime:
    return NOW


def usage(i=1000, o=200, searches=0):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0,
              server_tool_use=NS(web_search_requests=searches))


def text(obj):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(obj))], usage=usage())


def tool_use(name, args, id_="t1"):
    return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=id_, name=name, input=args)], usage=usage())


REPORT = {"stance": "neutral", "summary": "s", "evidence": [], "risks": [], "data_quality": "ok"}


def decider_out(action, signals=()):
    return {"action": action, "confidence": "medium", "reason": "r", "invalidation": "i", "new_signals": list(signals)}


class Script:
    """Answers by role (read from the system prompt) from a queue of responses."""

    def __init__(self, **by_role):
        self.by_role = {k: list(v) for k, v in by_role.items()}
        self.calls = []
        self.lock = threading.Lock()

    def create(self, timeout_s, **kw):
        role = next(r for r in self.by_role if _ROLE_HINT[r] in kw["system"])
        with self.lock:
            self.calls.append((role, kw))
            nxt = self.by_role[role].pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


_ROLE_HINT = {"analyst_news": "news and events", "analyst_price": "price action", "analyst_market": "market structure",
              "decider": "only one who decides", "veto": "check one trade decision"}


def fake_json(url):
    if "/klines" in url:
        rows = [[NOW_MS - (30 - i) * HOUR, "100", "101", "99", str(100 + i), "5", NOW_MS - (29 - i) * HOUR - 1] for i in range(30)]
        return rows + [[NOW_MS, "130", "131", "129", "130", "1", NOW_MS + HOUR - 1]]   # still open: dropped
    if "/depth" in url:
        return {"bids": [["99.9", "2"], ["99", "3"]], "asks": [["100.1", "1"], ["101", "4"]]}
    if "premiumIndex" in url:
        return {"markPrice": "100", "indexPrice": "100.1", "lastFundingRate": "0.0001", "nextFundingTime": NOW_MS + HOUR}
    if "/openInterest?" in url:
        return {"openInterest": "110"}
    if "openInterestHist" in url:
        return [{"sumOpenInterest": "100"}]
    if "ticker/24hr" in url:
        return {"lastPrice": "100", "priceChangePercent": "1.0", "highPrice": "101", "lowPrice": "99", "volume": "5",
                "quoteVolume": "500", "count": 9}
    raise AssertionError(url)


def trigger():
    return Trigger("sig", "time_check", {"at_ms": str(NOW_MS)}, NOW.isoformat(), "test")


def test_market_tools_return_closed_candles_and_summaries():
    c = market.candles(fake_json, "1h", 24, NOW_MS)
    assert len(c["candles"]) == 24 and c["candles"][-1][4] == "129" and c["indicators"]["sma20"] == "119.50"
    b = market.order_book(fake_json, 100)
    assert b["spread_bps"] == "20.00" and b["bid_btc_within_1pct"] == "5" and b["ask_btc_within_1pct"] == "5"
    assert market.derivatives(fake_json)["open_interest_change_24h_pct"] == "10.00"


def test_cost_counts_cache_and_web_searches_at_list_price():
    u = NS(input_tokens=1_000_000, output_tokens=100_000, cache_creation_input_tokens=0, cache_read_input_tokens=1_000_000,
           server_tool_use=NS(web_search_requests=3))
    assert call_cost(SONNET, u) == (Decimal("3.230000"), 2_000_000, 100_000, 3)          # 2 + 1 + 0.20 + 0.03
    assert call_cost(OPUS, usage(1000, 1000))[0] == Decimal("0.024000")


def test_a_hold_runs_five_roles_less_the_veto_and_keeps_valid_signals_only():
    good = {"condition": "price_close_below", "params": {"level": 95000}, "expires_in_hours": 24, "why": "w"}
    bad = {"condition": "rsi_below", "params": {"value": 150}, "expires_in_hours": 24, "why": "w"}
    s = Script(analyst_news=[text(REPORT)], analyst_price=[text(REPORT)], analyst_market=[text(REPORT)],
               decider=[text(decider_out("HOLD", [good, bad]))], veto=[])
    d = decide("agent-v1/test", "flat", trigger(), [], s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), clock)
    assert d.action == "HOLD" and not d.vetoed and [r.role for r in d.runs][-1] == "decider"
    assert [x["condition"] for x in d.new_signals] == ["price_close_below"] and d.new_signals[0]["expires_at_ms"] == NOW_MS + 24 * HOUR
    assert d.rejected_signals[0]["problems"] == ["RSI_OUT_OF_RANGE"]
    decider_prompt = next(kw for role, kw in s.calls if role == "decider")["messages"][0]["content"]
    assert '"analyst_market"' in decider_prompt and "Book position: flat" in decider_prompt


def test_a_buy_goes_to_the_veto_and_a_veto_or_a_failed_veto_means_hold():
    for veto, action in ((text({"verdict": "PASS", "reason_code": "SUPPORTED", "reason": "ok"}), "BUY"),
                         (text({"verdict": "VETO", "reason_code": "CHASING_MOVE", "reason": "no"}), "HOLD"),
                         (TimeoutError(), "HOLD")):
        s = Script(analyst_news=[text(REPORT)], analyst_price=[text(REPORT)], analyst_market=[text(REPORT)],
                   decider=[text(decider_out("BUY"))], veto=[veto])
        d = decide("b", "flat", trigger(), [], s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), clock)
        assert d.action == action and d.runs[-1].role == "veto"


def test_a_buy_while_long_is_not_allowed_and_is_not_sent_to_the_veto():
    s = Script(analyst_news=[text(REPORT)], analyst_price=[text(REPORT)], analyst_market=[text(REPORT)],
               decider=[text(decider_out("BUY"))], veto=[])
    d = decide("b", "long", trigger(), [], s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), clock)
    assert d.action == "HOLD" and d.runs[-1].role == "decider"


def test_the_tool_loop_logs_every_call_and_refuses_tools_outside_the_role():
    from trading.agent.roles import ANALYST_PRICE
    s = Script(analyst_price=[tool_use("get_candles", {"interval": "1h", "limit": 24}),
                              tool_use("get_order_book", {"limit": 100}, "t2"), text(REPORT)])
    r = run_role(ANALYST_PRICE, "go", s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), "d", clock)
    assert r.outcome == "OK" and r.output == REPORT and r.cost_usd == Decimal("0.012000")   # 3 calls x (1000 x $2 + 200 x $10) / 1M
    first, second = r.tool_calls
    assert first.tool == "get_candles" and first.response_sha256 and first.error is None
    assert second.response is None and "not allowed" in second.error
    last_msgs = s.calls[-1][1]["messages"]
    assert last_msgs[-1]["content"][0]["is_error"] is True


def test_web_search_results_are_logged_and_pause_turn_resumes():
    from trading.agent.roles import ANALYST_NEWS
    paused = NS(stop_reason="pause_turn", usage=usage(searches=1), content=[
        NS(type="server_tool_use", id="s1", name="web_search", input={"query": "bitcoin etf"}),
        NS(type="web_search_tool_result", tool_use_id="s1",
           content=[NS(url="https://x.example/a", title="A", page_age="1 day")])])
    s = Script(analyst_news=[paused, text(REPORT)])
    r = run_role(ANALYST_NEWS, "go", s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), "d", clock)
    assert r.outcome == "OK" and r.web_searches == 1 and len(s.calls) == 2
    assert r.tool_calls[0].request == {"query": "bitcoin etf"} and r.tool_calls[0].response[0]["url"] == "https://x.example/a"


def test_no_call_is_made_once_the_budget_cannot_cover_the_reserve():
    s = Script(decider=[text(decider_out("HOLD"))])
    r = run_role(DECIDER, "go", s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25), spent_today=Decimal("0.95")), "d", clock)
    assert r.outcome == "BUDGET_BLOCKED" and s.calls == []


def test_an_answer_without_the_required_fields_is_schema_invalid():
    s = Script(decider=[text({"action": "BUY"})])
    r = run_role(DECIDER, "go", s, Tools(fake_json, clock), Budget(Decimal(1), Decimal(25)), "d", clock)
    assert r.outcome == "SCHEMA_INVALID" and r.output is None


def test_the_answer_is_the_text_after_the_last_search_even_when_citations_split_it():
    from trading.agent.runner import _answer
    content = [NS(type="text", text="I will search."), NS(type="server_tool_use", id="s", name="web_search", input={}),
               NS(type="web_search_tool_result", tool_use_id="s", content=[]),
               NS(type="text", text='{"stance": "bullish", '), NS(type="text", text='"summary": "x"}')]
    assert _answer(content) == {"stance": "bullish", "summary": "x"}
