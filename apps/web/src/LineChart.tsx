import { useEffect, useRef, useState } from "react";

export type Series = { key: string; name: string; color: string; dash?: string; points: { t: string; v: number; label: string }[] };

function useWidth<T extends HTMLElement>(fallback = 640) {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function ticks(lo: number, hi: number, n = 4) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

/**
 * Multi-series daily line chart with one y axis, a hover crosshair and a tooltip listing
 * every series on the hovered day. Values are display floats; labels are pre-formatted strings.
 */
export function LineChart({ series, height = 200, yTick, zeroLine = false, title }: {
  series: Series[]; height?: number; yTick: (v: number) => string; zeroLine?: boolean; title: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const days = [...new Set(series.flatMap((s) => s.points.map((p) => p.t)))].sort();
  if (days.length === 0) return <p className="text-sm text-[var(--text-muted)]">ยังไม่มีข้อมูลรายวัน</p>;

  const vals = series.flatMap((s) => s.points.map((p) => p.v));
  let lo = Math.min(...vals, ...(zeroLine ? [0] : []));
  let hi = Math.max(...vals, ...(zeroLine ? [0] : []));
  if (lo === hi) { lo -= Math.abs(lo) * 0.01 || 1; hi += Math.abs(hi) * 0.01 || 1; }
  const pad = (hi - lo) * 0.08;
  const yt = ticks(lo - pad, hi + pad);
  const y0 = Math.min(yt[0], lo), y1 = Math.max(yt[yt.length - 1], hi);
  const m = { l: 64, r: 12, t: 8, b: 22 };
  const iw = width - m.l - m.r, ih = height - m.t - m.b;
  const x = (i: number) => m.l + (days.length === 1 ? iw / 2 : (i / (days.length - 1)) * iw);
  const y = (v: number) => m.t + (1 - (v - y0) / (y1 - y0)) * ih;
  const idx = new Map(days.map((d, i) => [d, i]));
  const xLabels = days.length === 1 ? [0] : [0, Math.floor((days.length - 1) / 2), days.length - 1];

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * width;
    const i = days.length === 1 ? 0 : Math.round(((px - m.l) / iw) * (days.length - 1));
    setHover(Math.min(days.length - 1, Math.max(0, i)));
  };
  const hoverDay = hover === null ? null : days[hover];

  return (
    <figure ref={ref} className="relative m-0">
      <svg role="img" aria-label={title} width={width} height={height} className="block touch-pan-y select-none"
        onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {yt.map((v) => (
          <g key={v}>
            <line x1={m.l} x2={width - m.r} y1={y(v)} y2={y(v)} stroke="var(--grid)" />
            <text x={m.l - 6} y={y(v)} dy="0.32em" textAnchor="end" fontSize="11" fill="var(--text-muted)">{yTick(v)}</text>
          </g>
        ))}
        {zeroLine && <line x1={m.l} x2={width - m.r} y1={y(0)} y2={y(0)} stroke="var(--text-muted)" />}
        {xLabels.map((i, k) => (
          <text key={k} x={x(i)} y={height - 6} fontSize="11" fill="var(--text-muted)"
            textAnchor={k === 0 && xLabels.length > 1 ? "start" : k === xLabels.length - 1 && xLabels.length > 1 ? "end" : "middle"}>
            {days[i]}
          </text>
        ))}
        {series.map((s) => {
          const pts = s.points.map((p) => `${x(idx.get(p.t)!)},${y(p.v)}`);
          return s.points.length === 1
            ? <circle key={s.key} cx={x(idx.get(s.points[0].t)!)} cy={y(s.points[0].v)} r={4} fill={s.color} />
            : <polyline key={s.key} points={pts.join(" ")} fill="none" stroke={s.color} strokeWidth={2}
                strokeDasharray={s.dash} strokeLinejoin="round" strokeLinecap="round" />;
        })}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={m.t} y2={m.t + ih} stroke="var(--text-muted)" strokeDasharray="2 3" />
            {series.map((s) => {
              const p = s.points.find((q) => q.t === hoverDay);
              return p && <circle key={s.key} cx={x(hover)} cy={y(p.v)} r={4} fill={s.color} stroke="var(--surface-1)" strokeWidth={2} />;
            })}
          </g>
        )}
      </svg>
      {hover !== null && (
        <div role="tooltip" className="pointer-events-none absolute top-1 z-10 rounded-md border border-[var(--border)] bg-[var(--surface-1)] px-2 py-1 text-xs shadow"
          style={x(hover) > width / 2 ? { right: width - x(hover) + 10 } : { left: x(hover) + 10 }}>
          <div className="mb-0.5 text-[var(--text-secondary)]">{hoverDay} (UTC)</div>
          {series.map((s) => {
            const p = s.points.find((q) => q.t === hoverDay);
            return p && (
              <div key={s.key} className="flex items-center gap-1.5 whitespace-nowrap">
                <span className="inline-block h-0.5 w-3" style={{ background: s.color }} />
                <span className="text-[var(--text-secondary)]">{s.name}</span>
                <span className="ml-auto pl-2 font-medium">{p.label}</span>
              </div>
            );
          })}
        </div>
      )}
    </figure>
  );
}

export function Legend({ series }: { series: Pick<Series, "key" | "name" | "color" | "dash">[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
      {series.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <svg width="18" height="6" aria-hidden><line x1="1" x2="17" y1="3" y2="3" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} /></svg>
          {s.name}
        </li>
      ))}
    </ul>
  );
}
