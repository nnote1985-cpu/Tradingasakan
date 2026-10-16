import { type ReactNode, useEffect, useState } from "react";

export const TABS = [
  { key: "spot", label: "Spot" },
  { key: "futures", label: "Futures" },
  { key: "options", label: "Options" },
  { key: "ai", label: "AI" },
  { key: "strategies", label: "กลยุทธ์" },
] as const;
export type TabKey = (typeof TABS)[number]["key"];

function fromHash(): TabKey {
  const h = window.location.hash.slice(1);
  return TABS.some((t) => t.key === h) ? (h as TabKey) : "spot";
}

/** The selected tab lives in the URL hash, so a reload or a shared link opens the same tab (no router needed). */
export function useHashTab(): [TabKey, (t: TabKey) => void] {
  const [tab, setTab] = useState<TabKey>(fromHash);
  useEffect(() => {
    const on = () => setTab(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return [tab, (t) => { window.location.hash = t; setTab(t); }];
}

export function TabBar({ tab, onSelect }: { tab: TabKey; onSelect: (t: TabKey) => void }) {
  return (
    <div role="tablist" aria-label="ประเภทพอร์ต" className="flex gap-1 overflow-x-auto border-b border-[var(--border)]">
      {TABS.map((t) => {
        const on = t.key === tab;
        return (
          <button
            key={t.key}
            role="tab"
            id={`tab-${t.key}`}
            aria-selected={on}
            aria-controls={`panel-${t.key}`}
            onClick={() => onSelect(t.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap ${on ? "border-[var(--text-primary)] font-medium text-[var(--text-primary)]" : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"}`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export function NotOpen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div data-testid="not-open" className="rounded-xl border border-dashed border-[var(--border)] p-6 text-center">
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-sm text-[var(--text-secondary)]">{children}</p>
    </div>
  );
}
