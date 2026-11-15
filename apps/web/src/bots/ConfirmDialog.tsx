import { useEffect, useRef, type ReactNode } from "react";

/** A modal question in the page's own style, in place of window.confirm (platform plan F3).
 * Escape or the cancel button closes it; the cancel button gets focus first so Enter never confirms by accident. */
export function ConfirmDialog({ title, children, confirmLabel, danger, onConfirm, onCancel, busy }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onCancel]);
  return <div className="dialog-backdrop">
    <div role="dialog" aria-modal="true" aria-labelledby="confirm-title" className="dialog-panel">
      <h2 id="confirm-title" className="text-lg font-semibold">{title}</h2>
      <div className="mt-2 space-y-2 text-sm">{children}</div>
      <div className="mt-5 flex flex-wrap justify-between gap-3">
        <button ref={cancel} type="button" className="action" onClick={onCancel} disabled={busy}>ยกเลิก</button>
        <button type="button" className={`action ${danger ? "danger-action" : "primary-action"}`} onClick={onConfirm} disabled={busy}>{busy ? "กำลังทำ…" : confirmLabel}</button>
      </div>
    </div>
  </div>;
}
