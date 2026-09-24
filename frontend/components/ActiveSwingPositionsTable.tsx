"use client";

import { useState } from "react";
import { SwingPosition } from "@/types/trading";
import { useActionButton } from "@/hooks/useActionButton";
import { companyName, formatMoney, formatSignedMoney } from "@/lib/plain";

interface ActiveSwingPositionsTableProps {
  positions?: SwingPosition[];
  onExitNextOpen: (symbol: string) => boolean;
  onExitImmediate: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

function Row({
  pos,
  onExitNextOpen,
  onExitImmediate,
  onTightenStop,
}: {
  pos: SwingPosition;
  onExitNextOpen: (symbol: string) => boolean;
  onExitImmediate: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}) {
  const sym = pos.symbol;
  const won = (pos.unrealized_pnl ?? 0) >= 0;
  const holdingDays = pos.holding_days ?? 0;
  const maxHoldingDays = pos.max_holding_days ?? 5;
  const stopLoss = pos.stop_loss ?? pos.stop_loss_price ?? null;
  const isStagedExit = pos.staged_exit_at_open ?? false;

  const [showRaise, setShowRaise] = useState(false);
  const [raiseInput, setRaiseInput] = useState("");

  const nextOpenButton = useActionButton({
    send: () => onExitNextOpen(sym),
    isDone: () => pos.staged_exit_at_open === true,
    requireConfirm: false,
  });
  const sellNowButton = useActionButton({
    send: () => onExitImmediate(sym),
    isDone: () => false, // row unmounts once the position is gone - that's the completion signal
    requireConfirm: true,
  });
  const raiseButton = useActionButton({
    send: () => {
      const val = parseFloat(raiseInput);
      if (!Number.isFinite(val) || val <= 0) return false;
      return onTightenStop(sym, val);
    },
    isDone: () => {
      const val = parseFloat(raiseInput);
      return stopLoss != null && Number.isFinite(val) && Math.abs(stopLoss - val) < 0.005;
    },
    requireConfirm: false,
  });

  const nextOpenLabel = isStagedExit
    ? "Sells at next open"
    : nextOpenButton.phase === "sending"
    ? "Staging…"
    : "Sell at next open";

  return (
    <div className="rise flex flex-col gap-3 rounded-2xl border border-line bg-white p-4 sm:p-5" data-testid={`active-swing-row-${sym}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-base sm:text-lg font-semibold text-ink">
            {companyName(sym)} <span className="text-xs font-medium text-muted">{sym}</span>
          </div>
          <div className="text-sm text-muted">
            Day {holdingDays} of {maxHoldingDays} &middot; bought at {formatMoney(pos.entry_price)}
          </div>
        </div>
        <div
          className="rounded-full px-3 py-1.5 text-sm font-bold tabular-nums"
          style={won ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#F6E3DA", color: "#8F4424" }}
        >
          {formatSignedMoney(pos.unrealized_pnl ?? 0)}
        </div>
      </div>

      <div className="text-sm text-muted">Safety exit: {stopLoss != null ? formatMoney(stopLoss) : "No safety exit set"}</div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={nextOpenButton.trigger}
          disabled={isStagedExit || nextOpenButton.phase === "sending"}
          data-testid={`btn-exit-open-${sym}`}
          className="min-h-[44px] flex-1 min-w-[150px] rounded-xl border px-4 text-sm font-semibold disabled:opacity-50"
          style={{ borderColor: "#D9DCEB", background: "#EEEFF7", color: "#3E4478" }}
        >
          {nextOpenLabel}
        </button>
        <button
          type="button"
          onClick={() => setShowRaise((v) => !v)}
          data-testid={`btn-tighten-stop-${sym}`}
          className="min-h-[44px] flex-1 min-w-[150px] rounded-xl border px-4 text-sm font-semibold"
          style={{ borderColor: "#EFE4D2", background: "#FFFFFF", color: "#3E3A57" }}
        >
          Raise safety exit
        </button>
        <button
          type="button"
          onClick={sellNowButton.trigger}
          disabled={sellNowButton.phase === "sending"}
          data-testid={`btn-emergency-exit-${sym}`}
          className="min-h-[44px] flex-1 min-w-[150px] rounded-xl px-4 text-sm font-semibold text-white disabled:opacity-60"
          style={{ background: "#A9553A" }}
        >
          {sellNowButton.phase === "confirm" ? "Tap again to confirm" : sellNowButton.phase === "sending" ? "Selling…" : "Sell now"}
        </button>
      </div>

      {showRaise && (
        <div className="flex flex-wrap items-center gap-2 rounded-2xl border p-3" style={{ borderColor: "#EFE4D2", background: "#FAF8F2" }}>
          <span className="text-sm text-muted">New safety exit price:</span>
          <input
            type="number"
            step="0.10"
            value={raiseInput}
            onChange={(e) => setRaiseInput(e.target.value)}
            placeholder={stopLoss != null ? formatMoney(stopLoss + 1) : "0.00"}
            className="min-h-[40px] w-28 rounded-xl border px-2.5 text-sm tabular-nums"
            style={{ borderColor: "#EFE4D2" }}
            data-testid={`input-raise-stop-${sym}`}
          />
          <button
            type="button"
            onClick={() => {
              raiseButton.trigger();
            }}
            data-testid={`btn-confirm-raise-${sym}`}
            className="min-h-[40px] rounded-xl px-3 text-sm font-semibold text-white"
            style={{ background: "#5E9A7A" }}
          >
            {raiseButton.phase === "sending" ? "Saving…" : raiseButton.phase === "done" ? "Saved" : raiseButton.phase === "failed" ? "Didn't go through" : "Save"}
          </button>
          <button type="button" onClick={() => setShowRaise(false)} className="min-h-[40px] rounded-xl px-3 text-sm text-muted">
            Close
          </button>
        </div>
      )}
    </div>
  );
}

/** F1/F12: swing (multi-day) holdings only shown here, on the Slow tab. */
export default function ActiveSwingPositionsTable({
  positions = [],
  onExitNextOpen,
  onExitImmediate,
  onTightenStop,
}: ActiveSwingPositionsTableProps) {
  if (positions.length === 0) {
    return (
      <section className="flex flex-col gap-3" data-testid="active-swing-positions">
        <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink">Holding now</h2>
        <div className="rounded-2xl border border-line bg-white p-6 text-center text-sm text-muted">
          Nothing held right now.
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-3" data-testid="active-swing-positions">
      <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink">Holding now</h2>
      <div className="flex flex-col gap-3">
        {positions.map((pos) => (
          <Row key={pos.symbol} pos={pos} onExitNextOpen={onExitNextOpen} onExitImmediate={onExitImmediate} onTightenStop={onTightenStop} />
        ))}
      </div>
    </section>
  );
}
