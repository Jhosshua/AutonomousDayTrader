"use client";

import { CircleHelp, Lock, ScanSearch, SlidersHorizontal } from "lucide-react";
import { MarketContext, Position } from "@/types/trading";
import { useActionButton } from "@/hooks/useActionButton";
import {
  adaptiveWhy,
  companyName,
  etTimeLabel,
  formatMoney,
  formatSignedMoney,
  hasNoSafetyExit,
  HoldingKind,
  holdingKind,
  NEUTRAL_THEME,
  orbBoxText,
  stillHeldLine,
  strategyTheme,
  trancheName,
} from "@/lib/plain";

interface HoldingNowProps {
  positions: Position[];
  marketContext?: MarketContext | null;
  onFlattenPosition: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

type Kind = HoldingKind;

const kindOf = holdingKind; // shared with the status strip (plain.ts), which counts "not linked" as needing a look

const BADGE_TEXT: Record<Kind, string> = {
  adaptive: "Sized for the market when it bought",
  fixed: "Fixed plan",
  orb: "Own market check",
  none: "Not linked to a strategy",
};

function Tile({ label, children, testid }: { label: string; children: React.ReactNode; testid?: string }) {
  return (
    <div className="rounded-lg bg-[#F1F3FB] px-2 py-1 text-xs leading-snug text-ink sm:px-2.5 sm:text-[13px]" data-testid={testid}>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function HoldingRow({
  position,
  marketContext,
  onFlattenPosition,
  onTightenStop,
}: {
  position: Position;
  marketContext?: MarketContext | null;
  onFlattenPosition: (symbol: string) => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}) {
  const isLong = position.side === "LONG";
  const won = position.unrealized_pnl >= 0;
  const stop = position.stop_loss ?? null;
  const entry = position.entry_price;
  const market = position.market_price;
  const kind = kindOf(position.strategy_id);
  const theme = kind === "none" ? { ...NEUTRAL_THEME, name: "Manual trade", what: "" } : strategyTheme(position.strategy_id || "", position.strategy_id);
  const Icon = kind === "orb" ? ScanSearch : kind === "adaptive" ? SlidersHorizontal : kind === "fixed" ? Lock : CircleHelp;
  const ctx = position.entry_context ?? null;
  const decided = ctx?.decided_at ? etTimeLabel(ctx.decided_at) : "";
  const trailing = position.runner_policy === "TRAIL_ONLY";
  const t1Done = position.target_1_filled === true;

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
    breakEvenButton.phase === "sending" ? "Moving…" : breakEvenButton.phase === "done" ? "Moved" : breakEvenButton.phase === "failed" ? "Didn't go through" : "Move safety exit to my entry price";

  const why = kind === "adaptive" && ctx ? adaptiveWhy(ctx, position.shares, isLong) : null;
  const movedFrom = position.initial_stop != null && stop != null && Math.abs(position.initial_stop - stop) > 0.004 ? position.initial_stop : null;
  const bracketed = kind === "adaptive" && position.bracket_status != null;

  return (
    <div className="rise flex flex-col overflow-hidden rounded-2xl border border-line bg-white" data-testid={`holding-row-${position.symbol}`}>
      {/* Phone: strategy line, plan, note, buttons. Desktop: the two buttons get their own column on the right. */}
      <div className="grid gap-2 px-3 py-2.5 sm:px-4 lg:grid-cols-[minmax(0,1fr)_190px] lg:gap-x-4">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 lg:col-start-1">
          <div
            className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-lg px-2.5 py-1.5"
            style={{
              background: theme.band,
              color: theme.ink,
              borderBottom: kind === "orb" ? `2px dashed ${theme.ink}` : undefined,
            }}
            data-testid="holding-banner"
            data-kind={kind}
          >
            <Icon className="h-4 w-4 flex-shrink-0" aria-hidden="true" />
            <span className="font-display text-[15px] font-semibold leading-tight" data-testid="holding-strategy">{theme.name}</span>
            <span
              className="rounded-full border bg-white/70 px-2 py-0.5 text-[11px] font-semibold"
              style={{ borderColor: theme.ink, color: theme.ink }}
              data-testid="holding-badge"
            >
              {BADGE_TEXT[kind]}
            </span>
          </div>
          <div className="min-w-0 flex-1 basis-[200px]">
            <div className="text-[15px] font-semibold text-ink">
              {companyName(position.symbol)}
              {companyName(position.symbol) !== position.symbol.toUpperCase() && <> <span className="text-xs font-medium text-muted">{position.symbol}</span></>}
            </div>
            <div className="text-xs text-muted">
              {isLong ? "bet it goes up" : "bet it goes down"} &middot; {position.shares} shares &middot; {isLong ? "bought" : "sold short"} at {formatMoney(entry)}
              {decided && <> &middot; decided at {decided}</>}
            </div>
          </div>
          <div
            className="rounded-full px-3 py-1 text-sm font-bold tabular-nums"
            style={won ? { background: "#E9F8F0", color: "#0A7D53" } : { background: "#FFEFEA", color: "#C2300F" }}
          >
            {formatSignedMoney(position.unrealized_pnl)}
          </div>
        </div>

          <div className="order-last flex flex-wrap gap-2 lg:order-none lg:col-start-2 lg:row-span-3 lg:row-start-1 lg:flex-col lg:justify-center">
            <button
              type="button"
              onClick={sellButton.trigger}
              disabled={sellButton.phase === "sending"}
              data-testid={`btn-sell-now-${position.symbol}`}
              className="min-h-[44px] flex-1 min-w-[120px] rounded-xl px-4 text-sm font-semibold text-white disabled:opacity-60 lg:w-full lg:flex-none"
              style={{ background: sellButton.phase === "confirm" ? "#C2300F" : "#0E1330" }}
            >
              {sellLabel}
            </button>
            <button
              type="button"
              onClick={breakEvenButton.trigger}
              disabled={!breakEvenEnabled || breakEvenButton.phase === "sending"}
              title={position.strategy_id === "orb" ? "ORB moves its own stop at Alpaca" : position.fixed_protection ? "This plan keeps its safety exit fixed" : "Before fees"}
              data-testid={`btn-break-even-${position.symbol}`}
              className="min-h-[44px] flex-1 min-w-[150px] rounded-xl border px-3 text-[13px] font-semibold leading-tight disabled:opacity-40 lg:w-full lg:flex-none"
              style={{ borderColor: "#BFE6D3", color: "#0B5A3C", background: "#E9F8F0" }}
            >
              {position.strategy_id === "orb" ? "ORB moves its own stop" : position.fixed_protection ? "Safety exit stays fixed" : beLabel}
            </button>
          </div>

          <div className="grid grid-cols-3 gap-1.5 sm:gap-2 lg:col-start-1" data-testid="holding-plan">
            <Tile label="Safety exit" testid="holding-tile-stop">
              <div className="font-semibold">{!hasNoSafetyExit(position) && stop != null ? formatMoney(stop) : "No safety exit set"}</div>
              {movedFrom != null && <div className="text-xs text-muted">started at {formatMoney(movedFrom)}</div>}
              {bracketed && !t1Done && <div className="text-xs text-muted">Moves to break-even after the first target, then follows the price.</div>}
              {bracketed && t1Done && <div className="text-xs text-muted">First target sold. It now follows the price.</div>}
            </Tile>
            {kind !== "orb" && !position.tranches && (
              <Tile label="Target" testid="holding-tile-target">
                {position.take_profit_1 == null ? (
                  <div className="font-semibold">Setting up</div>
                ) : trailing && !t1Done ? (
                  <div className="font-semibold">First part sells at {formatMoney(position.take_profit_1)}, the rest follows the price</div>
                ) : trailing ? (
                  <div className="font-semibold">First part sold. The rest follows the price.</div>
                ) : t1Done && position.take_profit_2 != null ? (
                  <div className="font-semibold">First part sold. The rest sells at {formatMoney(position.take_profit_2)}</div>
                ) : (
                  <div className="font-semibold">{formatMoney(position.take_profit_1)}</div>
                )}
              </Tile>
            )}
            {position.exit_due && !position.tranches && (
              <Tile label="Time limit" testid="holding-tile-exit-due">
                <div className="font-semibold">Sells by {etTimeLabel(position.exit_due)} at the latest</div>
              </Tile>
            )}
            {position.tranches && (
              <div className="col-span-2 grid gap-1.5 sm:grid-cols-2 sm:gap-2" data-testid="fixed-tranches">
                {position.tranches.map((t, i, all) => (
                  <div key={t.id} className="rounded-lg bg-[#EEF1FF] px-2 py-1 text-xs leading-snug text-[#1E36B8] sm:px-2.5 sm:text-[13px]">
                    <div className="font-semibold">{trancheName(i, all.length)} · {t.qty - t.closed_qty} shares open</div>
                    <div>Sells at {formatMoney(t.target)} or at {etTimeLabel(t.exit_due)} ET</div>
                    <div>{t.closed_qty === t.qty ? "Finished" : t.protection_confirmed && !t.protection_terminal ? "Protection held at the broker" : "Checking protection and exit orders"}</div>
                  </div>
                ))}
              </div>
            )}
          </div>

        {kind === "adaptive" && (
          <div className="relative rounded-lg px-3 py-1.5 text-[13px] leading-snug lg:col-start-1" style={{ background: theme.tint, color: theme.ink }} data-testid="holding-why">
            {why ? (
              <>
                <p className={why.rows.length > 0 ? "lg:pr-[112px]" : undefined}>
                  <span className="font-semibold">{why.sizeLine}</span>
                  {why.stopLine && <> {why.stopLine}</>}
                  {why.trendLine && <> {why.trendLine}</>}
                  {" "}<span className="text-xs" data-testid="holding-still">{stillHeldLine(marketContext?.vix_regime)}</span>
                </p>
                {why.rows.length > 0 && (
                  <details data-testid="holding-why-details">
                    {/* Desktop: top-right corner of the box, so a closed disclosure takes no line of its own. */}
                    <summary className="flex min-h-[44px] cursor-pointer items-center text-sm font-semibold lg:absolute lg:right-3 lg:top-0">Why this size?</summary>
                    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pb-1 text-sm">
                      {why.rows.map((r) => (
                        <div key={r.label} className="contents">
                          <dt className="font-semibold">{r.label}</dt>
                          <dd>{r.value}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                )}
              </>
            ) : (
              <p data-testid="holding-no-record">This trade started before the robot kept this record.</p>
            )}
          </div>
        )}

        {kind === "fixed" && (
          <div className="rounded-lg px-3 py-1.5 text-[13px] leading-snug lg:col-start-1" style={{ background: theme.tint, color: theme.ink }} data-testid="holding-fixed-note">
            Does the market change this trade? No, on purpose.{" "}
            {position.plan_risk_pct != null ? `It always risks ${position.plan_risk_pct}% of the account, its` : "Its"} exits were set when it bought and never move, and it runs on its own clock.
          </div>
        )}

        {kind === "orb" && (
          <div className="rounded-lg px-3 py-1.5 text-[13px] leading-snug lg:col-start-1" style={{ background: theme.tint, color: theme.ink }} data-testid="holding-orb-note">
            {orbBoxText({
              classification: position.orb_context?.classification ?? null,
              short_frac: position.orb_context?.short_frac ?? null,
              short_bounds: position.orb_context?.short_bounds ?? null,
              risk_usd: position.orb_context?.risk_usd ?? null,
              breakeven_r: position.orb_context?.breakeven_r ?? null,
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** F1: quick-trade holdings only (arm split happens in the caller). Replaces the old
 * ActivePositionTray bottom sheet. Hidden entirely when there are no intraday positions. */
export default function HoldingNow({ positions, marketContext, onFlattenPosition, onTightenStop }: HoldingNowProps) {
  if (positions.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h2 className="font-display text-lg font-semibold text-ink">Holding now</h2>
      <div className="flex flex-col gap-2">
        {positions.map((p) => (
          <HoldingRow key={p.symbol} position={p} marketContext={marketContext} onFlattenPosition={onFlattenPosition} onTightenStop={onTightenStop} />
        ))}
      </div>
    </section>
  );
}
