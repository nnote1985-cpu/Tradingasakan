// The candlestick chart (lightweight-charts 5.2.1, Apache-2.0, © TradingView; see docs/third_party/).
// Loaded with React.lazy so its ~60 KB gzip chunk is fetched only on the chart page (plan §5.4 C4).
import {
  CandlestickSeries, type CandlestickData, createChart, createSeriesMarkers, HistogramSeries, type IChartApi,
  type ISeriesApi, type ISeriesMarkersPluginApi, LineSeries, type MouseEventParams, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import {
  allBars, type ApiBar, lineColor, type BarsResponse, haltBars, type IndicatorsResponse, lineData, markers, type OverlaysResponse,
  planUpdate, type Timeframe, toCandle, toVolume,
} from "./model";

export type CandleChartProps = {
  bars: BarsResponse;
  indicators: IndicatorsResponse | null;
  hidden: Set<string>;
  overlays: OverlaysResponse | null;
  timeframe: Timeframe;
  onMarker: (id: string) => void;
  onHover: (values: Record<string, number | null> | null) => void;
};

function css(name: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function colors() {
  return {
    text: css("--text-secondary", "#52514e"), grid: css("--grid", "#ecebe7"), border: css("--border", "#e4e3df"),
    bg: css("--surface-1", "#fcfcfb"), gain: css("--gain", "#1d7f3f"), loss: css("--loss", "#c53b3a"),
    muted: css("--text-muted", "#6f6e69"), warn: css("--warn-bg", "#fde68a"),
  };
}

const ts = (t: number) => t as UTCTimestamp;

function candle(b: ApiBar): CandlestickData<Time> {
  const c = toCandle(b);
  return { ...c, time: ts(c.time) };
}

export default function CandleChart({ bars, indicators, hidden, overlays, timeframe, onMarker, onHover }: CandleChartProps) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const price = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const halt = useRef<ISeriesApi<"Histogram"> | null>(null);
  const marks = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const shown = useRef<ApiBar[] | null>(null);
  const handlers = useRef({ onMarker, onHover });
  handlers.current = { onMarker, onHover };
  const [ready, setReady] = useState(false);

  // create once; the chart survives data refreshes (plan §5.4 C1)
  useEffect(() => {
    if (!box.current) return;
    const c = colors();
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ch = createChart(box.current, {
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text, fontFamily: "Noto Sans Thai, system-ui, sans-serif", attributionLogo: true },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border, timeVisible: timeframe !== "1d", secondsVisible: false },
      crosshair: { mode: 0 },
      kineticScroll: { mouse: !reduced, touch: !reduced },
      localization: { locale: "th-TH" },
    });
    // up = hollow body, down = filled: the direction reads from the shape as well as the colour (C7)
    const p = ch.addSeries(CandlestickSeries, {
      upColor: "rgba(0,0,0,0)", downColor: c.loss, borderUpColor: c.gain, borderDownColor: c.loss, wickUpColor: c.gain, wickDownColor: c.loss,
    });
    const v = ch.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const h = ch.addSeries(HistogramSeries, { priceScaleId: "halt", lastValueVisible: false, priceLineVisible: false, color: c.warn });
    ch.priceScale("halt").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    chart.current = ch; price.current = p; volume.current = v; halt.current = h;
    marks.current = createSeriesMarkers(p, []);
    ch.subscribeClick((e: MouseEventParams<Time>) => {
      if (typeof e.hoveredObjectId === "string") handlers.current.onMarker(e.hoveredObjectId);
    });
    ch.subscribeCrosshairMove((e: MouseEventParams<Time>) => {
      if (e.time === undefined) return handlers.current.onHover(null);
      const out: Record<string, number | null> = {};
      const k = e.seriesData.get(p) as CandlestickData<Time> | undefined;
      out.close = k?.close ?? null;
      lines.current.forEach((s, key) => {
        const d = e.seriesData.get(s) as { value?: number } | undefined;
        out[key] = d?.value ?? null;
      });
      handlers.current.onHover(out);
    });
    setReady(true);
    return () => {
      ch.remove();
      chart.current = null; price.current = null; lines.current = new Map(); shown.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.applyOptions({ timeScale: { timeVisible: timeframe !== "1d" } });
    shown.current = null;                       // a new timeframe is a new data set
  }, [timeframe]);

  // bars: replace the set when it changed, otherwise update only the last bar(s)
  useEffect(() => {
    if (!ready || !price.current || !volume.current) return;
    const c = colors();
    const next = allBars(bars);
    const plan = planUpdate(shown.current, next);
    const vol = (b: ApiBar) => { const x = toVolume(b); return { time: ts(x.time), value: x.value, color: x.up ? `${c.gain}55` : `${c.loss}55` }; };
    if (plan.kind === "set") {
      price.current.setData(next.map(candle));
      volume.current.setData(next.map(vol));
    } else {
      for (const b of plan.bars) { price.current.update(candle(b)); volume.current.update(vol(b)); }
    }
    shown.current = next;
  }, [bars, ready]);

  // indicator lines: price panel on the candles, lower panel in pane 1
  useEffect(() => {
    const ch = chart.current;
    if (!ready || !ch) return;
    const want = new Map((indicators?.series ?? []).filter((s) => !hidden.has(s.key)).map((s) => [s.key, s]));
    lines.current.forEach((s, key) => { if (!want.has(key)) { ch.removeSeries(s); lines.current.delete(key); } });
    (indicators?.series ?? []).forEach((s, i) => {
      if (!want.has(s.key)) return;
      let line = lines.current.get(s.key);
      if (!line) {
        line = ch.addSeries(LineSeries, {
          color: lineColor(i), lineWidth: s.key.startsWith("band_") && s.panel === "price" ? 1 : 2,
          lineStyle: s.key.endsWith("_upper") || s.key.endsWith("_lower") ? 2 : 0,
          lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false,
          lineType: s.key === "band_state" ? 1 : 0,
        }, s.panel === "lower" ? 1 : 0);
        lines.current.set(s.key, line);
      }
      line.setData(lineData(s).map((p) => ({ ...p, time: ts(p.time) })));
    });
  }, [indicators, hidden, ready]);

  // overlays: markers and the HALT band
  useEffect(() => {
    if (!ready || !marks.current || !halt.current) return;
    const c = colors();
    const m = markers(overlays, timeframe, { buy: c.gain, sell: c.loss, decision: "#2a78d6", none: c.muted });
    marks.current.setMarkers(m.map((x) => ({ ...x, time: ts(x.time) })) as SeriesMarker<Time>[]);
    const now = Date.now() / 1000;
    halt.current.setData(haltBars(overlays, allBars(bars), timeframe, now).map((t) => ({ time: ts(t), value: 1, color: `${c.warn}99` })));
  }, [overlays, bars, timeframe, ready]);

  return <div ref={box} data-testid="candle-chart" className="h-[360px] w-full sm:h-[480px]" />;
}
