"use client";

import { Moon } from "lucide-react";
import { StrategyState } from "@/types/trading";
import { OvernightRowInputs, StrategyLedgerAgg, axisPct, handoffNote, showHandoffBand, x6NoteFor } from "@/lib/plain";
import StrategyCard, { ROW_GRID } from "./StrategyCard";
import OvernightPlaybookRow from "./OvernightPlaybookRow";

export interface StrategyTableOvernight {
  inputs: OvernightRowInputs;
  realizedToday: number | null;
  sizeNote: string | null;
  nameOf: (strategyId: string) => string;
}

interface StrategyTableProps {
  strategies: StrategyState[];
  ledgerByStrategy: Record<string, StrategyLedgerAgg>;
  showPro: boolean;
  /** Present only when the backend sends the overnight payload: adds the Overnight row and the night axis. */
  overnight?: StrategyTableOvernight | null;
}

/** The registered playbooks as one compact table: one row each, click a row for how it works. */
export default function StrategyTable({ strategies, ledgerByStrategy, showPro, overnight = null }: StrategyTableProps) {
  const night = overnight != null;
  const total = strategies.length + (night ? 1 : 0);
  const band = night && showHandoffBand(overnight.inputs);
  const note = night ? handoffNote(overnight.inputs) : null;
  return (
    <section className="overflow-hidden rounded-[22px] border border-line bg-white" data-testid="strategy-table">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 px-4 pb-2 pt-3">
        <h2 className="font-display text-lg font-semibold text-ink">
          The {total} ways it trades
          {night && <span className="ml-2 text-xs font-normal text-muted" data-testid="playbook-split">{strategies.length} by day, 1 overnight</span>}
        </h2>
        <div className="text-xs text-muted">
          <span>Colored bar = hours it may trade. Dark line = now.</span>{" "}
          {night && <span data-testid="night-legend">{band ? "Stripes = 3:45 PM handoff. " : ""}Night part not to scale.</span>}{" "}
          <span>Tap a row for how it works.</span>
        </div>
      </div>
      {note && (
        <div className="mx-4 mb-2 flex items-start gap-2.5 rounded-xl border px-3 py-2.5 text-[13px] leading-snug text-ink" style={{ background: "#F1F3FA", borderColor: "#DFE3F0" }} role="status" data-testid="handoff-note">
          <Moon className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden="true" />
          <span>{note}</span>
        </div>
      )}
      <div className={`${ROW_GRID} bg-[#F6F8FE] px-4 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted`} aria-hidden="true">
        <span className="hidden lg:block">Playbook</span>
        <span className="hidden lg:block">Status</span>
        <span className="relative col-span-3 col-start-2 block h-4 font-medium normal-case tracking-normal lg:col-span-1 lg:col-start-3" data-testid="axis-labels">
          <span className="absolute left-0">9:30</span>
          <span className="absolute -translate-x-1/2" style={{ left: `${axisPct(12 * 60, night)}%` }}>noon</span>
          {night ? (
            <>
              <span className="absolute hidden -translate-x-full pr-1 lg:inline" style={{ left: `${axisPct(16 * 60, true)}%` }} data-testid="axis-4pm">4 PM</span>
              <span className="absolute hidden lg:inline" style={{ left: `${axisPct(16 * 60, true) + 1}%` }} data-testid="axis-night">Night</span>
              <Moon className="absolute right-0 top-0.5 h-3 w-3 lg:hidden" aria-label="Night" data-testid="axis-night-icon" />
            </>
          ) : (
            <span className="absolute right-0">4 PM</span>
          )}
        </span>
        <span className="hidden text-right lg:col-start-4 lg:block">Result</span>
      </div>
      {strategies.map((s) => (
        <StrategyCard
          key={s.id}
          strategy={s}
          ledgerAgg={ledgerByStrategy[s.id]}
          showPro={showPro}
          nightAxis={night}
          handoffBand={band}
          x6Note={night ? x6NoteFor(s.id, overnight.inputs) : null}
        />
      ))}
      {night && (
        <OvernightPlaybookRow
          inputs={overnight.inputs}
          realizedToday={overnight.realizedToday}
          sizeNote={overnight.sizeNote}
          nameOf={overnight.nameOf}
        />
      )}
    </section>
  );
}
