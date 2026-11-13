"""R13: ports of 8 open-source MQL5 EAs (logiccrafterdz/argus, MIT) and a bar/minute simulator.

Plan: docs/research/r13-open-source-ea-screen-plan.md. Each port follows the EA's intended logic on
closed bars with its default inputs. Deviations from the literal MQL5 code are listed per class
under "Port notes". Indices: ``b`` is the last closed bar of the EA's timeframe; the decision is
filled at the open of bar ``b + 1``. MQL5 shift ``s`` maps to index ``b + 1 - s``.

Licence of the ported EAs: MIT, Copyright 2026 LogicCrafterDz (docs/third_party/argus-LICENSE.txt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

PIP = 0.01   # 2-digit BTCUSD symbol: 1 point = 1 pip = 0.01 USD (plan, "Port rules")
POINT = 0.01


# ---------------------------------------------------------------- indicators (MT5 definitions)

def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(alpha=2.0 / (n + 1), adjust=False).mean().to_numpy(copy=True)


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def true_range(h, l, c) -> np.ndarray:  # noqa: E741
    pc = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h, pc) - np.fmin(l, pc)
    tr[0] = h[0] - l[0]
    return tr


def atr(h, l, c, n: int) -> np.ndarray:  # noqa: E741
    """MT5 iATR: simple average of the true range."""
    return sma(true_range(h, l, c), n)


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    """MT5 iRSI: Wilder smoothing, seeded with the simple mean of the first n changes."""
    d = np.diff(c, prepend=np.nan)
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    au, ad = up[1:n + 1].mean(), dn[1:n + 1].mean()
    for i in range(n, len(c)):
        if i > n:
            au = (au * (n - 1) + up[i]) / n
            ad = (ad * (n - 1) + dn[i]) / n
        out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def adx(h, l, c, n: int):  # noqa: E741
    """MT5 iADX (ADX.mq5): directional movement / TR, each smoothed by an EMA(n); returns ADX, +DI, -DI."""
    ph, pl, pc = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]], np.r_[np.nan, c[:-1]]
    pdm, ndm = h - ph, pl - l
    pdm, ndm = np.where(pdm < 0, 0.0, pdm), np.where(ndm < 0, 0.0, ndm)
    keep_p = pdm > ndm
    keep_n = ndm > pdm
    pdm, ndm = np.where(keep_p, pdm, 0.0), np.where(keep_n, ndm, 0.0)
    tr = np.fmax(h, pc) - np.fmin(l, pc)
    with np.errstate(invalid="ignore", divide="ignore"):
        p_raw = np.where(tr > 0, 100.0 * pdm / tr, 0.0)
        n_raw = np.where(tr > 0, 100.0 * ndm / tr, 0.0)
    p_raw[0] = n_raw[0] = 0.0
    pdi, mdi = ema(p_raw, n), ema(n_raw, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(pdi + mdi > 0, 100.0 * np.abs(pdi - mdi) / (pdi + mdi), 0.0)
    out = ema(dx, n)
    out[: 2 * n] = np.nan   # warm-up
    return out, pdi, mdi


def bands(c: np.ndarray, n: int, k: float):
    s = pd.Series(c)
    mid = s.rolling(n, min_periods=n).mean().to_numpy()
    sd = s.rolling(n, min_periods=n).std(ddof=0).to_numpy()
    return mid, mid + k * sd, mid - k * sd


def rolling_max(x, n):
    return pd.Series(x).rolling(n, min_periods=n).max().to_numpy()


def rolling_min(x, n):
    return pd.Series(x).rolling(n, min_periods=n).min().to_numpy()


def swing_flags(h, l, r: int):  # noqa: E741
    """Argus IsSwingHigh/IsSwingLow at index k: older bars (k-j) may tie, newer bars (k+j) must be
    strictly below/above. Uses bars up to k+r, so the flag is known once bar k+r has closed."""
    n = len(h)
    sh = np.zeros(n, bool)
    sl = np.zeros(n, bool)
    for k in range(r, n - r):
        sh[k] = all(h[k - j] <= h[k] for j in range(1, r + 1)) and all(h[k + j] < h[k] for j in range(1, r + 1))
        sl[k] = all(l[k - j] >= l[k] for j in range(1, r + 1)) and all(l[k + j] > l[k] for j in range(1, r + 1))
    return sh, sl


def last_true_index(flags: np.ndarray) -> np.ndarray:
    """last[k] = largest i <= k with flags[i], else -1."""
    idx = np.where(flags, np.arange(len(flags)), -1)
    return np.maximum.accumulate(idx)


def htf_forming_ema(times: pd.DatetimeIndex, daily_close: pd.Series, n: int) -> np.ndarray:
    """EMA(n) of D1 closes through the previous UTC day, for each bar time (the forming day's value
    is then prev + a * (price - prev), computed by the caller with the current price)."""
    e = pd.Series(ema(daily_close.to_numpy(), n), index=daily_close.index)
    prev = e.shift(1)
    day = times.floor("1D")
    return prev.reindex(day).to_numpy()


def forming(prev: float, price: float, n: int) -> float:
    a = 2.0 / (n + 1)
    return prev + a * (price - prev)


# ---------------------------------------------------------------- EA ports

@dataclass
class Entry:
    side: int                 # +1 long, -1 short
    sl: float
    tp_r: float | None = None  # TP = entry_ref ± R × (distance to SL)
    tp_price: float | None = None


@dataclass
class Manage:
    exit: bool = False
    new_sl: float | None = None
    drop_tp: bool = False


@dataclass
class Bars:
    t: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741
    c: np.ndarray
    d1_close: pd.Series = field(default=None)


class EA:
    name = ""
    tf = "1h"
    min_bars = 210

    def __init__(self, bars: Bars):
        self.B = bars

    def manage(self, b: int, side: int, sl: float) -> Manage:   # noqa: ARG002
        return Manage()

    def entry(self, b: int, price: float) -> Entry | None:      # price = open of bar b+1 (ask/bid)
        return None

    def on_filled(self, b: int) -> None:
        pass

    def on_flat_bar(self, b: int) -> None:   # called on new bars while flat, before entry()
        pass


class E1Donchian(EA):
    """DonchianBreakoutEA.mq5 (H4). Port notes: ADX "rising" = ADX(bar1) > ADX(bar2) on closed bars
    (literal code: same). The trailing modify passes tp=0, which removes the 2.5R target on the first
    trailing move; kept as written."""
    name, tf = "E1_Donchian_Breakout", "4h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.e50, self.e200 = ema(B.c, 50), ema(B.c, 200)
        self.adx, _, _ = adx(B.h, B.l, B.c, 14)
        self.hi20 = rolling_max(B.h, 20)
        self.lo20 = rolling_min(B.l, 20)

    def manage(self, b, side, sl):
        new = self.lo20[b] if side > 0 else self.hi20[b]
        if side > 0 and new > sl + POINT:
            return Manage(new_sl=new, drop_tp=True)
        if side < 0 and new < sl - POINT:
            return Manage(new_sl=new, drop_tp=True)
        return Manage()

    def entry(self, b, price):
        if b < self.min_bars or not (self.adx[b] >= 20 and self.adx[b] > self.adx[b - 1]):
            return None
        c = self.B.c[b]
        up, dn = self.hi20[b - 1], self.lo20[b - 1]     # shift 2..21
        if c > up and c > self.e50[b] and c > self.e200[b]:
            return Entry(+1, self.lo20[b] - 2 * POINT, tp_r=2.5)
        if c < dn and c < self.e50[b] and c < self.e200[b]:
            return Entry(-1, self.hi20[b] + 2 * POINT, tp_r=2.5)
        return None


class E2SuperTrend(EA):
    """SuperTrendEMA_EA.mq5 (H1). SuperTrend state is rebuilt each bar from 50 bars back with zero
    seeds, as the EA does. Port notes: the chop filter reads ATR at shift 0 (forming bar) in MQL5;
    we use bar 1."""
    name, tf = "E2_SuperTrend_EMA", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.e200 = ema(B.c, 200)
        self.atr10 = atr(B.h, B.l, B.c, 10)
        self.atr_sma = sma(self.atr10, 50)
        n = len(B.c)
        self.trend = np.zeros(n, int)
        self.val = np.full(n, np.nan)
        mid = (B.h + B.l) / 2
        for b in range(61, n):
            up = lo = 0.0
            tr = 0
            v = np.nan
            for i in range(b - 50, b + 1):
                a = self.atr10[i]
                bu, bl = mid[i] + 3.0 * a, mid[i] - 3.0 * a
                cp = B.c[i - 1]
                nu = bu if (bu < up or cp > up) else up
                nl = bl if (bl > lo or cp < lo) else lo
                if B.c[i] > nu:
                    tr = 1
                elif B.c[i] < nl:
                    tr = -1
                v = nl if tr == 1 else nu
                up, lo = nu, nl
            self.trend[b], self.val[b] = tr, v

    def manage(self, b, side, sl):
        if side > 0 and self.trend[b] == 1 and self.val[b] > sl + POINT:
            return Manage(new_sl=self.val[b])
        if side < 0 and self.trend[b] == -1 and self.val[b] < sl - POINT:
            return Manage(new_sl=self.val[b])
        return Manage()

    def entry(self, b, price):
        if b < self.min_bars or not np.isfinite(self.atr_sma[b]):
            return None
        if not self.atr10[b] > self.atr_sma[b] * 0.8:
            return None
        c = self.B.c[b]
        if self.trend[b - 1] == -1 and self.trend[b] == 1 and c > self.e200[b]:
            return Entry(+1, self.val[b] - 5 * POINT)
        if self.trend[b - 1] == 1 and self.trend[b] == -1 and c < self.e200[b]:
            return Entry(-1, self.val[b] + 5 * POINT)
        return None


class E3ADX(EA):
    """ADXTrendStrengthEA.mq5 (H1). Port notes: CADXUtils reads a non-series buffer whose index 0 is
    the oldest value, so the literal IsRising/IsFalling compare the reverse way and include the forming
    bar; we use the intended "ADX(bar1) > ADX(bar2)" and "ADX fell on each of the last 2 closed bars"."""
    name, tf = "E3_ADX_TrendStrength", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.adx, self.pdi, self.mdi = adx(B.h, B.l, B.c, 14)
        self.e200 = ema(B.c, 200)
        self.atr14 = atr(B.h, B.l, B.c, 14)

    def manage(self, b, side, sl):
        a = self.adx
        return Manage(exit=bool(a[b] < a[b - 1] < a[b - 2]))

    def entry(self, b, price):
        a = self.adx
        if b < self.min_bars or not (a[b] >= 25 and a[b] > a[b - 1]):
            return None
        d = 1.5 * self.atr14[b]
        c = self.B.c[b]
        if c > self.e200[b] and self.pdi[b] > self.mdi[b]:
            return Entry(+1, price - d, tp_r=2.5)
        if c < self.e200[b] and self.mdi[b] > self.pdi[b]:
            return Entry(-1, price + d, tp_r=2.5)
        return None


class E4TrendPullback(EA):
    """TrendPullbackEA.mq5 (H1)."""
    name, tf = "E4_TrendPullback", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.e50, self.e200 = ema(B.c, 50), ema(B.c, 200)

    def _structure(self, b, up: bool) -> bool:
        B = self.B
        x = B.l if up else B.h
        for s in range(2, 30):               # shift 2..29
            k = b + 1 - s
            if up and x[k] < x[k + 1] and x[k] < x[k - 1]:
                return x[b] > x[k]
            if not up and x[k] > x[k + 1] and x[k] > x[k - 1]:
                return x[b] < x[k]
        return False

    def entry(self, b, price):
        if b < self.min_bars:
            return None
        B, f, s = self.B, self.e50, self.e200
        if f[b] > s[b] and s[b] > s[b - 1] and self._structure(b, True):
            if B.l[b - 1] <= f[b - 1] and B.c[b] > B.h[b - 1]:
                return Entry(+1, B.l[b] - 5 * PIP, tp_r=2)
        if f[b] < s[b] and s[b] < s[b - 1] and self._structure(b, False):
            if B.h[b - 1] >= f[b - 1] and B.c[b] < B.l[b - 1]:
                return Entry(-1, B.h[b] + 5 * PIP, tp_r=2)
        return None


class E5Squeeze(EA):
    """VolatilitySqueezeEA.mq5 (H1). Port notes: Keltner bands are read at shift 0 in MQL5 while BB
    is read at shift 1; we read both at bar 1. The squeeze counter only moves on new bars while flat
    (the EA returns early when a position is open)."""
    name, tf = "E5_Volatility_Squeeze", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        _, self.bu, self.bl = bands(B.c, 20, 2.0)
        e20, a20 = ema(B.c, 20), atr(B.h, B.l, B.c, 20)
        self.ku, self.kl = e20 + 1.5 * a20, e20 - 1.5 * a20
        self.e100 = ema(B.c, 100)
        rng, body = B.h - B.l, np.abs(B.c - B.o)
        self.avg_rng = pd.Series(rng).shift(1).rolling(20).mean().to_numpy()
        self.avg_body = pd.Series(body).shift(1).rolling(20).mean().to_numpy()
        self.rng, self.body = rng, body
        self.counter = 0

    def on_flat_bar(self, b):
        if self.bu[b] < self.ku[b] and self.bl[b] > self.kl[b]:
            self.counter += 1
        else:
            self.counter = 0

    def entry(self, b, price):
        if b < self.min_bars or self.counter < 5:
            return None
        if not (self.rng[b] > self.avg_rng[b] * 1.5 and self.body[b] > self.avg_body[b] * 1.5):
            return None
        B = self.B
        c, o = B.c[b], B.o[b]
        if c > self.bu[b] and o <= self.bu[b] and c > self.e100[b]:
            return Entry(+1, B.l[b] - 5 * POINT, tp_r=2)
        if c < self.bl[b] and o >= self.bl[b] and c < self.e100[b]:
            return Entry(-1, B.h[b] + 5 * POINT, tp_r=2)
        return None

    def on_filled(self, b):
        self.counter = 0


class E6BollingerMR(EA):
    """BollingerMeanReversionEA.mq5 (H1). Session mask applies only with the orchestrator (not used)."""
    name, tf = "E6_Bollinger_MeanReversion", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.mid, self.up, self.lo = bands(B.c, 20, 2.0)
        self.rsi = rsi(B.c, 14)
        self.state, self.limit, self.count = 0, 0.0, 0     # 0 idle, +1 outside upper, -1 outside lower

    def manage(self, b, side, sl):
        self.state = 0
        return Manage()

    def entry(self, b, price):
        if b < self.min_bars:
            return None
        B = self.B
        if self.state == 0:
            if B.h[b] > self.up[b]:
                self.state, self.limit, self.count = 1, B.h[b], 0
            elif B.l[b] < self.lo[b]:
                self.state, self.limit, self.count = -1, B.l[b], 0
            return None
        self.count += 1
        if self.count > 15:
            self.state = 0
            return None
        if self.state == 1:
            self.limit = max(self.limit, B.h[b])
            if B.c[b] < self.up[b]:
                self.state = 0
                if self.rsi[b] > 70:
                    return Entry(-1, self.limit + POINT, tp_price=self.mid[b])
        else:
            self.limit = min(self.limit, B.l[b])
            if B.c[b] > self.lo[b]:
                self.state = 0
                if self.rsi[b] < 30:
                    return Entry(+1, self.limit - POINT, tp_price=self.mid[b])
        return None


class E7HiddenDivergence(EA):
    """HiddenDivergenceEA.mq5 (H1). Bias = current price vs the forming D1 EMA200."""
    name, tf = "E7_Hidden_Divergence", "1h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.rsi = rsi(B.c, 14)
        self.sh, self.sl = swing_flags(B.h, B.l, 3)
        self.last_sh, self.last_sl = last_true_index(self.sh), last_true_index(self.sl)
        self.d1_prev = htf_forming_ema(B.t + pd.Timedelta(hours=1), B.d1_close, 200)

    def _two(self, last, b):
        k1 = last[b - 3]                          # newest swing needs 3 closed bars after it
        if k1 < 0 or k1 < b + 1 - 59:
            return None
        k2 = last[k1 - 1] if k1 > 0 else -1
        if k2 < 0 or k2 < b + 1 - 59:
            return None
        return k1, k2

    def entry(self, b, price):
        if b < self.min_bars or not np.isfinite(self.d1_prev[b]):
            return None
        e = forming(self.d1_prev[b], price, 200)
        B = self.B
        if price > e:
            s = self._two(self.last_sl, b)
            if s and (b + 1 - s[0]) <= 6:
                p1, p2 = B.l[s[0]], B.l[s[1]]
                if abs(p1 - p2) >= 10 * PIP and p1 > p2 and self.rsi[s[0]] < self.rsi[s[1]]:
                    return Entry(+1, p1 - 2 * POINT, tp_r=2)
        elif price < e:
            s = self._two(self.last_sh, b)
            if s and (b + 1 - s[0]) <= 6:
                p1, p2 = B.h[s[0]], B.h[s[1]]
                if abs(p1 - p2) >= 10 * PIP and p1 < p2 and self.rsi[s[0]] > self.rsi[s[1]]:
                    return Entry(-1, p1 + 2 * POINT, tp_r=2)
        return None


class E8SmartSwing(EA):
    """SmartSwingBiasEA.mq5 (H4). Port notes: FindLatestSwingLeg starts at shift 2 in MQL5, where the
    radius-3 check reads the forming bar and shift -1 (returns 0); we only accept swings with 3 closed
    bars on each side. Trades per day are counted per UTC day (MQL5: broker day)."""
    name, tf = "E8_Smart_Swing_Bias", "4h"

    def __init__(self, bars):
        super().__init__(bars)
        B = bars
        self.sh, self.sl = swing_flags(B.h, B.l, 3)
        self.last_sh, self.last_sl = last_true_index(self.sh), last_true_index(self.sl)
        t_next = B.t + pd.Timedelta(hours=4)
        self.d200 = htf_forming_ema(t_next, B.d1_close, 200)
        self.d50 = htf_forming_ema(t_next, B.d1_close, 50)
        self.day = t_next.floor("1D")
        self.trades_day, self.trades_today = None, 0

    def entry(self, b, price):
        if self.day[b] != self.trades_day:
            self.trades_day, self.trades_today = self.day[b], 0
        if b < self.min_bars or self.trades_today >= 1 or not np.isfinite(self.d200[b]):
            return None
        e200, e50 = forming(self.d200[b], price, 200), forming(self.d50[b], price, 50)
        bias = 1 if (price > e200 and e50 > e200) else -1 if (price < e200 and e50 < e200) else 0
        if bias == 0:
            return None
        kh, kl = self.last_sh[b - 3], self.last_sl[b - 3]
        if kh < 0 or kl < 0 or kh < b + 1 - 299 or kl < b + 1 - 299:
            return None
        B = self.B
        hi, lo = B.h[kh], B.l[kl]
        rng = hi - lo
        if rng / PIP < 30:
            return None
        o, c, h, l = B.o[b], B.c[b], B.h[b], B.l[b]  # noqa: E741
        if bias == 1 and kh > kl:                 # high more recent than low
            top, bot = lo + rng * 0.5, lo + rng * 0.25
            if bot <= price <= top and c > o and (c - l) > (h - c) * 2:
                return Entry(+1, lo - 2 * POINT, tp_r=2)
        if bias == -1 and kl > kh:
            bot, top = lo + rng * 0.5, lo + rng * 0.75
            if bot <= price <= top and c < o and (h - c) > (c - l) * 2:
                return Entry(-1, hi + 2 * POINT, tp_r=2)
        return None

    def on_filled(self, b):
        self.trades_today += 1


EAS = [E1Donchian, E2SuperTrend, E3ADX, E4TrendPullback, E5Squeeze, E6BollingerMR, E7HiddenDivergence, E8SmartSwing]


# ---------------------------------------------------------------- simulator

@dataclass
class Costs:
    fee: float
    slip: float = 0.0002
    stop_slip: float = 0.0005


SPOT = Costs(fee=0.0010)
PERP = Costs(fee=0.0005)


def make_bars(h1: pd.DataFrame, tf: str, d1_close: pd.Series) -> Bars:
    if tf == "1h":
        x = h1
    else:
        g = h1.resample(tf, origin="epoch", label="left", closed="left")
        x = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                          "close": g["close"].last()})
    return Bars(x.index, *(x[k].to_numpy(float) for k in ("open", "high", "low", "close")), d1_close=d1_close)


def simulate(ea_cls, h1: pd.DataFrame, m1: pd.DataFrame, funding: pd.Series | None, *, start, end,
             market: str, sizing: str, costs: Costs, equity0: float = 10_000.0):
    """Run one EA. market 'spot' (long/flat, shorts ignored) or 'perp' (long/short, funding).
    sizing 'native' (1% risk, notional ≤ 1× equity) or 'full' (1× equity). Returns daily equity, trades."""
    d1_close = h1["close"].resample("1D").last()
    bars = make_bars(h1, ea_cls.tf, d1_close)
    ea = ea_cls(bars)
    hours_per_bar = 4 if ea_cls.tf == "4h" else 1
    ho, hh, hl, hc = (h1[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    h_t = h1.index
    mo, mh, ml = (m1[k].to_numpy(float) for k in ("open", "high", "low"))
    m0 = m1.index[0]
    fund = {}
    if funding is not None and market == "perp":
        f = funding.copy()
        f.index = f.index.floor("1h")
        fund = f.groupby(level=0).sum().to_dict()

    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    cash, qty, side = equity0, 0.0, 0
    entry_px = sl = tp = None
    trades, marks = [], {}
    open_trade: dict | None = None

    def equity_at(px):
        if market == "spot":
            return cash + qty * px
        return cash + (qty * (px - entry_px) if side else 0.0)

    def close_pos(px_raw, t, reason, slip):
        nonlocal cash, qty, side, entry_px, sl, tp, open_trade
        px = px_raw * (1 - slip * side)
        if market == "spot":
            proceeds = qty * px * (1 - costs.fee)
            cash += proceeds
        else:
            cash += qty * (px - entry_px) - abs(qty) * px * costs.fee
        open_trade.update(exit_time=t, exit_px=px, reason=reason, equity_after=cash)
        trades.append(open_trade)
        qty, side, entry_px, sl, tp, open_trade = 0.0, 0, None, None, None, None

    def open_pos(sd, px_raw, t, stop, tp_r, tp_price):
        nonlocal cash, qty, side, entry_px, sl, tp, open_trade
        eq = cash
        dist = abs(px_raw - stop)
        if dist <= 0:
            return False
        notional = eq if sizing == "full" else min(eq * 0.01 / dist * px_raw, eq)
        px = px_raw * (1 + costs.slip * sd)
        if market == "spot":
            qty = notional * (1 - costs.fee) / px
            cash -= notional
        else:
            qty = sd * notional / px
            cash -= notional * costs.fee
        side, entry_px, sl = sd, px, stop
        tp = tp_price if tp_price is not None else (px_raw + sd * tp_r * dist if tp_r else None)
        open_trade = {"entry_time": t, "side": sd, "entry_px": px, "sl0": stop, "tp0": tp, "notional": notional,
                      "equity_before": eq}
        return True

    first_bar = int(np.searchsorted(bars.t, start))
    last_bar = int(np.searchsorted(bars.t, end))
    for j in range(first_bar, last_bar):
        b = j - 1
        t_open = bars.t[j]
        px_open = bars.o[j]
        if not np.isfinite(px_open):
            continue
        if side:
            m = ea.manage(b, side, sl)
            if m.exit:
                close_pos(px_open, t_open, "signal_exit", costs.slip)
            else:
                if m.new_sl is not None:
                    sl = m.new_sl
                if m.drop_tp:
                    tp = None
        if not side:
            ea.on_flat_bar(b)
            e = ea.entry(b, px_open)
            if e is not None:
                valid = (e.side > 0 and e.sl < px_open) or (e.side < 0 and e.sl > px_open)
                if e.tp_price is not None:
                    valid &= (e.side > 0 and e.tp_price > px_open) or (e.side < 0 and e.tp_price < px_open)
                if valid and (market == "perp" or e.side > 0):
                    if open_pos(e.side, px_open, t_open, e.sl, e.tp_r, e.tp_price):
                        ea.on_filled(b)
        # walk the hours of bar j: stops/targets on 1m, funding, then mark at the hour close
        h_first = int(np.searchsorted(h_t, t_open))
        for hi in range(h_first, h_first + hours_per_bar):
            if hi >= len(h_t):
                break
            th = h_t[hi]
            if side and market == "perp" and th in fund:
                cash -= qty * ho[hi] * fund[th]
            if side:
                mi = int((th - m0) / pd.Timedelta(minutes=1))
                lo_, hi_, op_ = ml[mi:mi + 60], mh[mi:mi + 60], mo[mi:mi + 60]
                if len(op_) == 0 or not np.isfinite(op_).all():
                    lo_, hi_, op_ = np.array([hl[hi]]), np.array([hh[hi]]), np.array([ho[hi]])
                if side > 0:
                    hit_sl = np.flatnonzero(lo_ <= sl)
                    hit_tp = np.flatnonzero(hi_ >= tp) if tp is not None else np.array([], int)
                else:
                    hit_sl = np.flatnonzero(hi_ >= sl)
                    hit_tp = np.flatnonzero(lo_ <= tp) if tp is not None else np.array([], int)
                k_sl = hit_sl[0] if len(hit_sl) else 10**9
                k_tp = hit_tp[0] if len(hit_tp) else 10**9
                if k_sl <= k_tp and k_sl < 10**9:
                    o_ = op_[k_sl]
                    fill = min(sl, o_) if side > 0 else max(sl, o_)
                    close_pos(fill, th + pd.Timedelta(minutes=int(k_sl)), "stop", costs.stop_slip)
                elif k_tp < 10**9:
                    o_ = op_[k_tp]
                    fill = max(tp, o_) if side > 0 else min(tp, o_)
                    close_pos(fill, th + pd.Timedelta(minutes=int(k_tp)), "target", costs.slip)
            marks[th] = equity_at(hc[hi])
    if side:
        close_pos(hc[min(int(np.searchsorted(h_t, end)) - 1, len(hc) - 1)], end, "window_end", costs.slip)
        marks[max(marks)] = cash
    eq_h = pd.Series(marks).sort_index()
    daily = eq_h[eq_h.index.hour == 23]
    daily.index = daily.index.floor("1D")
    daily = pd.concat([pd.Series([equity0], index=[daily.index[0] - pd.Timedelta(days=1)]), daily])
    return daily, pd.DataFrame(trades), eq_h
