"use client";

import { useState } from "react";
import { ChevronDown, Moon } from "lucide-react";
import { ROW_GRID, NightPart, HandoffBand } from "./StrategyCard";
import {
  OvernightRowInputs,
  OvnStep,
  StepStatus,
  axisPct,
  formatSignedMoney,
  isWithinSession,
  joinNames,
  overnightChip,
  overnightSteps,
  showHandoffBand,
  strategyTheme,
} from "@/lib/plain";

interface OvernightPlaybookRowProps {
  inputs: OvernightRowInputs;
  /** account.overnight_realized_today: the same number the Safety card shows */
  realizedToday: number | null;
  sizeNote: string | null;
  /** a day playbook's plain name, for "Ride the Trend's NVDA trade was closed early" */
  nameOf: (strategyId: string) => string;
}

/** Same layout as StrategyCard's columns. */
const UNDER_ROW = "col-span-4 col-start-1 lg:col-span-3 lg:col-start-2";

const CHIP_FG: Record<string, string> = { sage: "#0B5A3C", lavender: "#4A2AB5", grey: "#5B6283", amber: "#8A4B00" };

const STEP_LOOK: Record<StepStatus, { bg: string; fg: string; border: string; word: string }> = {
  done: { bg: "#F1F3FA", fg: "#0E1330", border: "#DFE3F0", word: "Done" },
  now: { bg: "#C6F432", fg: "#0E1330", border: "#C6F432", word: "Now" },
  next: { bg: "#FFFFFF", fg: "#2A3150", border: "#DDE2F2", word: "Next" },
  skipped: { bg: "#F6F8FE", fg: "#5B6283", border: "#DDE2F2", word: "Skipped" },
  problem: { bg: "#FFF4DB", fg: "#8A4B00", border: "#F0D79A", word: "Not done" },
};

function Steps({ steps }: { steps: OvnStep[] }) {
  return (
    <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4" data-testid="overnight-steps">
      {steps.map((st, n) => {
        const look = STEP_LOOK[st.status];
        return (
          <li
            key={st.key}
            className="flex flex-col gap-1 rounded-xl border px-3 py-2 text-[13px] leading-snug"
            style={{ background: look.bg, color: look.fg, borderColor: look.border }}
            data-testid={`overnight-step-${st.key}`}
            data-status={st.status}
          >
            <span className="flex items-center gap-2 text-xs font-semibold">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[#0E1330] text-[11px] text-white" aria-hidden="true">{n + 1}</span>
              <span>{st.time}</span>
              <span className="ml-auto">{look.word}</span>
            </span>
            <span>{st.text}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** The 7th playbook row: the overnight holds as one playbook, in the same columns as the day playbooks.
 * Holds and the "No overnight buy tonight" control stay in OvernightHolds above both views (PLAN_2026_10_05 F3). */
export default function OvernightPlaybookRow({ inputs, realizedToday, sizeNote, nameOf }: OvernightPlaybookRowProps) {
  const [open, setOpen] = useState(false);
  const theme = strategyTheme("overnight_nvda", "Overnight");
  const chip = overnightChip(inputs);
  const resting = chip.tone === "grey";
  const band = showHandoffBand(inputs);
  const holding = inputs.holds.length > 0;
  const nightLeft = axisPct(16 * 60, true);
  const min = inputs.etMin;
  const showNowDay = inputs.today?.full_day !== false && isWithinSession(min) && min < 16 * 60;
  const showNowNight = holding && !isWithinSession(min);
  const pnl = realizedToday ?? 0;
  const pnlColor = pnl > 0 ? "#0A7D53" : pnl < 0 ? "#C2300F" : "#5B6283";
  const syms = joinNames(inputs.enabled);
  const steps = overnightSteps(inputs, nameOf);
  const note = [sizeNote ? `${sizeNote.charAt(0).toUpperCase()}${sizeNote.slice(1)}.` : null, holding && pnl !== 0 ? "Today's result is from this morning's sale." : null]
    .filter(Boolean)
    .join(" ");

  return (
    <article className="border-t border-line" data-testid="strategy-row-overnight">
      <h3>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={open ? "strategy-details-overnight" : undefined}
          data-testid="strategy-row-toggle"
          className={`${ROW_GRID} min-h-[52px] w-full items-center gap-y-1 px-4 py-2 text-left transition-colors hover:bg-[#F6F8FE] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[#2B4BFF]`}
        >
          <span className="col-span-2 col-start-1 row-start-1 flex min-w-0 items-center gap-2.5 lg:col-span-1">
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg" style={{ background: theme.bar, opacity: resting ? 0.6 : 1 }} aria-hidden="true">
              <Moon className="h-4 w-4 text-white" strokeWidth={2} />
            </span>
            <span className="flex min-w-0 flex-col">
              <span className="font-display text-[15px] font-semibold leading-tight" style={{ color: theme.ink }} data-testid="strategy-name">Overnight</span>
              {syms && <span className="text-xs text-muted" data-testid="overnight-stocks">{syms}</span>}
            </span>
          </span>

          <span
            className="col-start-1 row-start-2 inline-flex items-center gap-1.5 justify-self-start rounded-full px-2.5 py-1 text-xs font-semibold lg:col-start-2 lg:row-start-1"
            style={{ background: chip.tone === "sage" && !resting ? "#E9F8F0" : theme.tint, color: CHIP_FG[chip.tone] }}
            data-testid="window-badge"
          >
            <span
              className={chip.breathing ? "breathe inline-block h-2 w-2 rounded-full" : "inline-block h-2 w-2 rounded-full"}
              style={{ background: resting ? "#8A91B0" : CHIP_FG[chip.tone] }}
            />
            {chip.label}
          </span>

          <span className="relative col-span-3 col-start-2 row-start-2 block h-2 rounded-full lg:col-span-1 lg:col-start-3 lg:row-start-1" style={{ background: theme.track }} data-testid="strategy-window">
            <NightPart />
            {/* the sale at the 9:30 AM open */}
            <span className="absolute inset-y-0 left-0 block w-[2%] rounded-l-full" style={{ background: theme.bar, opacity: resting ? 0.4 : 1 }} data-testid="overnight-sale-tick" />
            {/* the hold, from the close to the next open */}
            <span className="absolute inset-y-0 right-0 block rounded-r-full" style={{ left: `${nightLeft}%`, background: theme.bar, opacity: resting ? 0.4 : 1 }} data-testid="overnight-night-bar" />
            {band && <HandoffBand />}
            {showNowDay && <span className="breathe absolute -top-1 block h-4 w-[3px] rounded-sm" style={{ left: `${axisPct(min, true)}%`, background: "#0E1330" }} />}
            {showNowNight && <span className="breathe absolute -top-1 block h-4 w-[3px] rounded-sm" style={{ left: `${(nightLeft + 100) / 2}%`, background: "#C6F432", boxShadow: "0 0 0 1px #0E1330" }} />}
          </span>

          <span className={`${UNDER_ROW} row-start-3 text-[13px] leading-snug text-[#2A3150] lg:row-start-2`} data-testid="strategy-decisions">
            {note}
          </span>

          <span className="col-start-3 row-start-1 text-right text-sm font-bold tabular-nums lg:col-start-4" style={{ color: pnlColor }} data-testid="strategy-pnl">
            {pnl !== 0 ? formatSignedMoney(pnl) : "$0"}
          </span>

          <ChevronDown className="col-start-4 row-start-1 h-4 w-4 text-muted transition-transform lg:col-start-5" style={{ transform: open ? "rotate(180deg)" : undefined }} aria-hidden="true" />
        </button>
      </h3>

      {open && (
        <div id="strategy-details-overnight" className="flex flex-col gap-3 px-4 pb-4 pt-1 lg:pl-[54px]" data-testid="strategy-details">
          <div className="flex flex-col gap-1">
            <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: theme.ink }}>How it works</div>
            <p className="text-sm leading-relaxed text-[#2A3150]">
              {syms ? `Buys ${syms} at the 4:00 PM close and sells at the next 9:30 AM open. No stop.` : "No stock is switched on."} From 3:45 PM until the sale, the day playbooks don&apos;t trade these stocks, and a day trade in one of them is closed at 3:46 PM.
            </p>
            <a href="#overnight-holds" className="self-start text-sm font-semibold text-[#2B4BFF] underline-offset-2 hover:underline" data-testid="overnight-holds-link">
              Stop tonight&apos;s buy with the button in the Overnight holds box at the top
            </a>
          </div>
          {steps ? (
            <div className="flex flex-col gap-2">
              <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: theme.ink }}>Tonight, step by step</div>
              <Steps steps={steps} />
            </div>
          ) : (
            <p className="text-sm text-[#2A3150]" data-testid="overnight-steps-none">No night is planned or open right now.</p>
          )}
        </div>
      )}
    </article>
  );
}
