"use client";

import { AuditRecord } from "@/types/trading";

interface ExecutionLogProps {
  records: AuditRecord[];
  maxItems?: number;
}

/** Pro-only (rendered by the parent only when "Show pro words" is on): the old technical
 * execution audit log, now a collapsed section at the bottom of the page, light theme. */
export default function ExecutionLog({ records, maxItems = 20 }: ExecutionLogProps) {
  const displayRecords = records.slice(0, maxItems);

  return (
    <details className="rounded-3xl border border-line bg-white p-4">
      <summary className="cursor-pointer select-none text-sm font-semibold text-ink">
        Execution audit log ({records.length} events)
      </summary>
      {displayRecords.length === 0 ? (
        <div className="py-4 text-center text-xs text-muted">No execution events recorded yet.</div>
      ) : (
        <div className="mt-3 max-h-64 space-y-1.5 overflow-y-auto pr-1">
          {displayRecords.map((item, idx) => (
            <div key={`${item.id}-${idx}`} className="rounded-xl border border-line/60 bg-[#FAF8F2] px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5">
                  {item.symbol && <span className="font-bold text-ink">{item.symbol}</span>}
                  <span className="rounded-md bg-white px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-muted">
                    {item.type}
                  </span>
                </div>
                <span className="tabular-nums text-[10px] text-muted">{item.timestamp}</span>
              </div>
              <p className="mt-0.5 break-words leading-snug text-[#3E3A57]">{item.message}</p>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}
