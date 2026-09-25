"use client";

import { Position } from "@/types/trading";
import { useActionButton } from "@/hooks/useActionButton";
import { companyName, formatMoney, formatSignedMoney, etTimeLabel } from "@/lib/plain";

interface HoldingNowProps {
  positions: Position[];
  onFlattenPosition: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

function HoldingRow({
  position,
  onFlattenPosition,
  onTightenStop,
}: {
  position: Position;
  onFlattenPosition: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}) {
  const isLong = position.side === "LONG";
  const won = position.unrealized_pnl >= 0;
  const stop = position.stop_loss ?? null;
  const entry = position.entry_price;
  const market = position.market_price;

  // F2: enabled only when moving the stop to entry is an improvement AND cannot trigger an
  // immediate exit. Long: current_stop < entry < market. Short: current_stop > entry > market.
  const breakEvenEnabled = !position.fixed_protection && (isLong
    ? (stop == null || stop < entry) && entry < market
    : (stop == null || stop > entry) && entry > market);

  const sellButton = useActionButton({
    send: () => onFlattenPosition(position.symbol),
    isDone: () => false, // the position disappearing from props IS the completion signal
    requireConfirm: true,
  });
  const breakEvenButton = useActionButton({
    send: () => onTightenStop(position.symbol, entry),
    isDone: () => position.stop_loss != null && Math.abs(position.stop_loss - entry) < 0.005,
    requireConfirm: false,
  });

  const sellLabel =
    sellButton.phase === "confirm" ? "Tap again to confirm" : sellButton.phase === "sending" ? "Selling…" : isLong ? "Sell now" : "Close trade";
  const beLabel =
    breakEvenButton.phase === "sending" ? "Moving…" : breakEvenButton.phase === "done" ? "Moved" : breakEvenButton.phase === "failed" ? "Didn't go through" : "Move safety exit to my buy price";

  return (
    <div className="rise flex flex-col gap-3 rounded-2xl border border-line bg-white p-4 sm:p-5" data-testid={`holding-row-${position.symbol}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-base sm:text-lg font-semibold text-ink">
            {companyName(position.symbol)} <span className="text-xs font-medium text-muted">{position.symbol}</span>
          </div>
          <div className="text-sm text-muted">{isLong ? "bet it goes up" : "bet it goes down"} &middot; bought at {formatMoney(entry)}</div>
        </div>
        <div
          className="rounded-full px-3 py-1.5 text-sm font-bold tabular-nums"
          style={won ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#F6E3DA", color: "#8F4424" }}
        >
          {formatSignedMoney(position.unrealized_pnl)}
        </div>
      </div>
      <div className="text-sm text-muted">
        Safety exit: {stop != null ? formatMoney(stop) : "No safety exit set"}
      </div>
      {position.fixed_protection && (
        <div className="text-sm text-muted">
          Fixed Tesla plan · Target: {position.take_profit_1 != null ? formatMoney(position.take_profit_1) : "Setting up"}
          {position.exit_due && <> · Close by {etTimeLabel(position.exit_due)} ET</>}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={sellButton.trigger}
          disabled={sellButton.phase === "sending"}
          data-testid={`btn-sell-now-${position.symbol}`}
          className="min-h-[44px] flex-1 min-w-[140px] rounded-xl px-4 text-sm font-semibold text-white disabled:opacity-60"
          style={{ background: "#A9553A" }}
        >
          {sellLabel}
        </button>
        <button
          type="button"
          onClick={breakEvenButton.trigger}
          disabled={!breakEvenEnabled || breakEvenButton.phase === "sending"}
          title={position.fixed_protection ? "This Tesla plan keeps its safety exit fixed" : "Before fees"}
          data-testid={`btn-break-even-${position.symbol}`}
          className="min-h-[44px] flex-1 min-w-[180px] rounded-xl border px-4 text-sm font-semibold disabled:opacity-40"
          style={{ borderColor: "#D5E2D6", color: "#2F5A45", background: "#EDF3EE" }}
        >
          {position.fixed_protection ? "Safety exit stays fixed" : beLabel}
        </button>
      </div>
    </div>
  );
}

/** F1: quick-trade holdings only (arm split happens in the caller). Replaces the old
 * ActivePositionTray bottom sheet. Hidden entirely when there are no intraday positions. */
export default function HoldingNow({ positions, onFlattenPosition, onTightenStop }: HoldingNowProps) {
  if (positions.length === 0) return null;
  return (
    <section className="flex flex-col gap-3">
      <h2 className="font-display text-xl sm:text-2xl font-semibold text-ink">Holding now</h2>
      <div className="flex flex-col gap-3">
        {positions.map((p) => (
          <HoldingRow key={p.symbol} position={p} onFlattenPosition={onFlattenPosition} onTightenStop={onTightenStop} />
        ))}
      </div>
    </section>
  );
}
