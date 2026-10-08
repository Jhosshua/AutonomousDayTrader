"use client";
import { type ComponentProps } from "react";
import StrategyTable from "@/components/StrategyTable";
import { playbooksInitiallyOpen, playbookSummaryLine } from "@/lib/gut";
import { strategyTheme } from "@/lib/plain";

/** Home keeps the first-frame disclosure state, including across History navigation. */
export default function PlaybookPanel({ dayCount, overnightCount, weekResults, savedOpen, onOpenChange, ...table }: ComponentProps<typeof StrategyTable> & { dayCount: number; overnightCount: number; weekResults: Record<string, number>; savedOpen: boolean | null; onOpenChange: (open: boolean) => void }) {
  const open = savedOpen ?? playbooksInitiallyOpen(table.strategies, dayCount);
  return <details id="plans" className="gut-card gut-playbooks" data-testid="playbook-panel" open={open} onToggle={e => onOpenChange(e.currentTarget.open)}>
    <summary className="gut-summary"><span className="gut-chevron" aria-hidden="true">›</span><span className="min-w-0 flex-1"><span className="block font-display text-base font-bold">The {table.strategies.length + (table.overnight ? 1 : 0)} playbooks</span><span className="block text-xs text-muted">{playbookSummaryLine(table.strategies, weekResults, overnightCount)}</span></span><span className="flex gap-1" aria-hidden="true">{table.strategies.map(s => <span key={s.id} className="h-1.5 w-1.5 rounded-full" style={{ background: strategyTheme(s.id, s.name).bar }} />)}</span></summary>
    <StrategyTable {...table} weekResults={weekResults} />
  </details>;
}
