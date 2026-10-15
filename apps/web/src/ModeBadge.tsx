// §22.3: the mode must be unmistakable on every screen.
const styles: Record<string, string> = {
  SHADOW: "bg-slate-200 text-slate-900",
  BACKTEST: "bg-slate-200 text-slate-900",
  HISTORICAL_REPLAY: "bg-slate-200 text-slate-900",
  TESTNET: "bg-amber-200 text-amber-950",
  LIVE: "bg-red-600 text-white",
};

export function ModeBadge({ mode }: { mode: string }) {
  return (
    <span data-testid="mode-badge" className={`rounded px-2 py-0.5 text-sm font-semibold ${styles[mode] ?? "bg-red-600 text-white"}`}>
      {mode}
    </span>
  );
}
