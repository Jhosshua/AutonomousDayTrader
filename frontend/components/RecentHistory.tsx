"use client";

import type { PerformanceDay } from "@/lib/performance";
import { formatMoney, formatSignedMoney, historyDateLabel } from "@/lib/plain";

interface RecentHistoryProps {
  days: PerformanceDay[];
  onOpenHistory: () => void;
}

function signedPercent(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "Unavailable";
  if (value === 0) return "0.00%";
  return `${value > 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}%`;
}

function tone(value: number | null): string {
  if (value === null || value === 0) return "text-muted";
  return value > 0 ? "text-gain" : "text-loss";
}

export default function RecentHistory({ days, onOpenHistory }: RecentHistoryProps) {
  const visible = [...days].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-white" data-testid="recent-history">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted">Recent history</p>
          <h2 className="text-lg font-bold text-ink">Five trading days</h2>
        </div>
        <button
          type="button"
          onClick={onOpenHistory}
          className="min-h-[44px] rounded-lg border border-line px-3 text-sm font-semibold text-ink hover:bg-ground"
        >
          Open History
        </button>
      </div>

      {visible.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted">No recorded trading days yet.</p>
      ) : (
        <ul>
          {visible.map((day) => (
            <li key={day.date} className="border-b border-line last:border-b-0">
              <button
                type="button"
                onClick={onOpenHistory}
                className="grid min-h-[56px] w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2 text-left hover:bg-ground focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-darkcard"
              >
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-ink">{historyDateLabel(day.date)}</span>
                  <span className="block text-xs text-muted">
                    {day.tradeCount} finished {day.tradeCount === 1 ? "trade" : "trades"}
                    {day.aggregateOnly ? " · daily total only" : ""}
                  </span>
                </span>
                <span className={`text-right ${tone(day.accountChange.signedDollars)}`}>
                  <span className="block text-sm font-bold tabular-nums">
                    {day.accountChange.signedDollars === null
                      ? "Unavailable"
                      : formatSignedMoney(day.accountChange.signedDollars)}
                  </span>
                  <span className="block text-xs font-semibold tabular-nums">
                    {signedPercent(day.accountChange.percent)}
                  </span>
                  {day.openingEquity !== null && (
                    <span className="sr-only">Opening equity {formatMoney(day.openingEquity)}</span>
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
