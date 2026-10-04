"use client";

import { StrategyState } from "@/types/trading";
import { StrategyLedgerAgg, sessionPct } from "@/lib/plain";
import StrategyCard, { ROW_GRID } from "./StrategyCard";

interface StrategyTableProps {
  strategies: StrategyState[];
  ledgerByStrategy: Record<string, StrategyLedgerAgg>;
  showPro: boolean;
}

/** The registered playbooks as one compact table: one row each, click a row for how it works. */
export default function StrategyTable({ strategies, ledgerByStrategy, showPro }: StrategyTableProps) {
  return (
    <section className="overflow-hidden rounded-[22px] border border-line bg-white" data-testid="strategy-table">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-4 pb-2 pt-3">
        <h2 className="font-display text-lg font-semibold text-ink">The {strategies.length} ways it trades</h2>
        <div className="text-xs text-muted"><span>Colored bar = hours it may trade. Dark line = now.</span> <span>Tap a row for how it works.</span></div>
      </div>
      <div className={`${ROW_GRID} bg-[#F6F8FE] px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted`} aria-hidden="true">
        <span className="hidden lg:block">Playbook</span>
        <span className="hidden lg:block">Status</span>
        <span className="relative col-span-3 col-start-2 block h-4 font-medium normal-case tracking-normal lg:col-span-1 lg:col-start-3">
          <span className="absolute left-0">9:30</span>
          <span className="absolute -translate-x-1/2" style={{ left: `${sessionPct(12 * 60)}%` }}>noon</span>
          <span className="absolute right-0">4 PM</span>
        </span>
        <span className="hidden text-right lg:col-start-4 lg:block">Result</span>
      </div>
      {strategies.map((s) => (
        <StrategyCard key={s.id} strategy={s} ledgerAgg={ledgerByStrategy[s.id]} showPro={showPro} />
      ))}
    </section>
  );
}
