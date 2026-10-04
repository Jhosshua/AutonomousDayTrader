// @steered SNARE-2 2026-09-30
"use client";

import { useMemo } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { etMinutesOfDay, etMinutesOfDayFromIso, formatMoney, formatSignedMoney, sessionPct } from "@/lib/plain";

interface TodayTradeLike {
  closed_at: string;
  realized_pnl: number;
}

interface BalanceCardProps {
  equity: number;
  dailyPnl: number;
  todayTrades: TodayTradeLike[];
  loading: boolean;
  /** Overnight holds: "Includes $X in overnight holds at their buy price..." while any is held. */
  overnightNote?: string | null;
  /** All-time line from the full ledger. Null until the ledger has loaded, and on a ledger error (never a guess). */
  history?: { sinceStart: number; lastDay: { pnl: number; label: string } | null } | null;
  /** In the 420 px right-hand column of the Quick trades grid: tighter padding and a slightly narrower number column. */
  narrow?: boolean;
  className?: string;
}

const SESSION_START_MIN = 9 * 60 + 30;

/** F6: the chart is NOT a balance history. It is cumulative realized P&L of today's FINISHED
 * trades, a step line starting at $0. The balance number above it is the live account equity,
 * a separate figure. */
export default function BalanceCard({ equity, dailyPnl, todayTrades, loading, overnightNote, history, narrow = false, className = "" }: BalanceCardProps) {
  const isDown = dailyPnl < 0;
  const isUp = dailyPnl > 0;

  const { path, fillPath, hasTrades, nowPct, finalValue, zeroY } = useMemo(() => {
    const sorted = [...todayTrades].sort(
      (a, b) => new Date(a.closed_at).getTime() - new Date(b.closed_at).getTime()
    );
    const steps: { pct: number; value: number }[] = [{ pct: sessionPct(SESSION_START_MIN), value: 0 }];
    let cumulative = 0;
    for (const t of sorted) {
      const pct = sessionPct(etMinutesOfDayFromIso(t.closed_at));
      steps.push({ pct, value: cumulative });
      cumulative = Math.round((cumulative + t.realized_pnl) * 100) / 100;
      steps.push({ pct, value: cumulative });
    }
    const nowP = sessionPct(etMinutesOfDay());
    const lastPct = steps[steps.length - 1].pct;
    steps.push({ pct: Math.max(nowP, lastPct), value: cumulative });

    const values = steps.map((s) => s.value);
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const span = Math.max(1, max - min);
    const H = 150;
    const padTop = 14;
    const padBottom = 14;
    const yFor = (v: number) => H - padBottom - ((v - min) / span) * (H - padTop - padBottom);
    const xFor = (pct: number) => (pct / 100) * 560;

    const linePath = steps.map((s, i) => `${i === 0 ? "M" : "L"}${xFor(s.pct).toFixed(1)},${yFor(s.value).toFixed(1)}`).join(" ");
    const fill = `${linePath} L${xFor(steps[steps.length - 1].pct).toFixed(1)},${H} L${xFor(steps[0].pct).toFixed(1)},${H} Z`;

    return { path: linePath, fillPath: fill, hasTrades: sorted.length > 0, nowPct: nowP, finalValue: cumulative, zeroY: yFor(0) };
  }, [todayTrades]);

  return (
    <div className={`rise hover-card flex flex-col rounded-[22px] border border-line bg-white ${narrow ? "gap-1.5 p-3 sm:p-4" : "gap-2 p-4 sm:p-5"} ${className}`} data-testid="balance-card">
      <div className={`flex flex-col ${narrow ? "gap-1.5 sm:flex-row sm:items-stretch sm:gap-4" : "gap-3 sm:flex-row sm:items-stretch sm:gap-5"}`}>
      <div className={`flex flex-col gap-2 ${narrow ? "sm:w-[44%] sm:justify-center" : "sm:w-[46%] sm:justify-center"}`}>
      <div className="flex flex-row items-start justify-between gap-3 sm:flex-col sm:gap-2">
        <div className="flex flex-col gap-0.5">
          <div className="text-xs text-muted">Your balance</div>
          <div className="font-display text-3xl font-bold tracking-tight tabular-nums text-ink">
            {formatMoney(equity)}
          </div>
          {overnightNote && <div className="text-xs leading-snug text-muted" data-testid="balance-overnight-note">{overnightNote}</div>}
        </div>
        {!loading && (
          <div
            className="flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
            style={
              isDown
                ? { background: "#FFEFEA", color: "#C2300F" }
                : isUp
                ? { background: "#E9F8F0", color: "#0A7D53" }
                : { background: "#F1F3FB", color: "#5B6283" }
            }
          >
            {isDown ? <ArrowDown className="h-3.5 w-3.5" /> : isUp ? <ArrowUp className="h-3.5 w-3.5" /> : null}
            <span>
              {isDown ? "Down " : isUp ? "Up " : "Flat "}
              {formatMoney(Math.abs(dailyPnl))} today
            </span>
          </div>
        )}
      </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col justify-center gap-1">
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span className="text-muted">Finished trades today</span>
          {hasTrades && (
            <span className="text-sm font-semibold tabular-nums" style={{ color: finalValue < 0 ? "#C2300F" : finalValue > 0 ? "#0A7D53" : "#5B6283" }}>
              {finalValue < 0 ? "-" : finalValue > 0 ? "+" : ""}{formatMoney(Math.abs(finalValue))}
            </span>
          )}
        </div>
        <div className="relative h-[36px] sm:h-[44px]">
          {hasTrades ? (
            <svg width="100%" height="100%" viewBox="0 0 560 150" preserveAspectRatio="none" role="img"
              aria-label={`Chart of today's finished trades: cumulative result ${finalValue >= 0 ? "up" : "down"} ${formatMoney(Math.abs(finalValue))}`}>
              <line x1="0" y1={zeroY} x2="560" y2={zeroY} stroke="#C4CBE3" strokeWidth="1" strokeDasharray="4 5" vectorEffect="non-scaling-stroke" />
              <path className="fadein" d={fillPath} fill="#2B4BFF" fillOpacity="0.12" />
              <path className="draw" d={path} fill="none" stroke="#2B4BFF" strokeWidth="2.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
            </svg>
          ) : (
            <div className="flex h-full items-center justify-center gap-3 text-center">
              <div className="h-px flex-1" style={{ background: "#DDE2F2" }} />
              <span className="text-xs text-muted">No trades yet today</span>
              <div className="h-px flex-1" style={{ background: "#DDE2F2" }} />
            </div>
          )}
        </div>
        <div className="relative flex justify-between text-[11px] text-muted">
          <span>9:30 AM</span>
          {hasTrades && nowPct > 12 && nowPct < 88 && (
            <span className="absolute -translate-x-1/2" style={{ left: `${nowPct}%`, color: "#2B4BFF", fontWeight: 600 }}>Now</span>
          )}
          <span>4:00 PM</span>
        </div>
      </div>
      </div>
      {history && (
        <div className="text-xs leading-snug text-muted" data-testid="balance-history">
          <span className="whitespace-nowrap">Since start <MoneyText value={history.sinceStart} />{history.lastDay && " ·"}</span>
          {history.lastDay && <> Last trading day <MoneyText value={history.lastDay.pnl} /> ({history.lastDay.label})</>}
        </div>
      )}
    </div>
  );
}

function MoneyText({ value }: { value: number }) {
  return (
    <span className="font-semibold tabular-nums" style={{ color: value < 0 ? "#C2300F" : value > 0 ? "#0A7D53" : "#5B6283" }}>
      {formatSignedMoney(value)}
    </span>
  );
}
