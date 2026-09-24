"use client";

import { useMemo } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import { etMinutesOfDay, etMinutesOfDayFromIso, formatMoney, sessionPct } from "@/lib/plain";

interface TodayTradeLike {
  closed_at: string;
  realized_pnl: number;
}

interface BalanceCardProps {
  equity: number;
  dailyPnl: number;
  todayTrades: TodayTradeLike[];
  loading: boolean;
}

const SESSION_START_MIN = 9 * 60 + 30;

/** F6: the chart is NOT a balance history. It is cumulative realized P&L of today's FINISHED
 * trades, a step line starting at $0. The balance number above it is the live account equity,
 * a separate figure. */
export default function BalanceCard({ equity, dailyPnl, todayTrades, loading }: BalanceCardProps) {
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
    <div className="rise hover-card flex flex-col gap-4 rounded-[28px] border border-line bg-white p-6 sm:p-7">
      <div className="flex flex-col items-start gap-3 sm:flex-row sm:justify-between sm:gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="text-sm text-muted">Your balance</div>
          <div className="font-display text-4xl sm:text-5xl font-medium tracking-tight tabular-nums text-ink">
            {formatMoney(equity)}
          </div>
        </div>
        {!loading && (
          <div
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-semibold"
            style={
              isDown
                ? { background: "#F6E3DA", color: "#8F4424" }
                : isUp
                ? { background: "#E4EFE7", color: "#2F6B4C" }
                : { background: "#F3F1EA", color: "#5D5A73" }
            }
          >
            {isDown ? <ArrowDown className="h-4 w-4" /> : isUp ? <ArrowUp className="h-4 w-4" /> : null}
            <span>
              {isDown ? "Down " : isUp ? "Up " : "Flat "}
              {formatMoney(Math.abs(dailyPnl))} today
            </span>
          </div>
        )}
      </div>

      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="text-muted">Finished trades today</span>
        {hasTrades && (
          <span className="font-semibold tabular-nums" style={{ color: finalValue < 0 ? "#8F4424" : finalValue > 0 ? "#2F6B4C" : "#5D5A73" }}>
            {finalValue < 0 ? "-" : finalValue > 0 ? "+" : ""}{formatMoney(Math.abs(finalValue))}
          </span>
        )}
      </div>
      <div className="relative h-[96px] sm:h-[130px]">
        {hasTrades ? (
          <svg width="100%" height="100%" viewBox="0 0 560 150" preserveAspectRatio="none" role="img"
            aria-label={`Chart of today's finished trades: cumulative result ${finalValue >= 0 ? "up" : "down"} ${formatMoney(Math.abs(finalValue))}`}>
            <defs>
              <linearGradient id="balanceLine" x1="0" y1="0" x2="1" y2="0">
                <stop offset="0" stopColor="#6E9C82" />
                <stop offset="0.5" stopColor="#8189C4" />
                <stop offset="1" stopColor="#C47A88" />
              </linearGradient>
              <linearGradient id="balanceFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="#8189C4" stopOpacity="0.25" />
                <stop offset="1" stopColor="#C47A88" stopOpacity="0" />
              </linearGradient>
            </defs>
            <line x1="0" y1={zeroY} x2="560" y2={zeroY} stroke="#CFC6B3" strokeWidth="1" strokeDasharray="4 5" />
            <path className="fadein" d={fillPath} fill="url(#balanceFill)" />
            <path className="draw" d={path} fill="none" stroke="url(#balanceLine)" strokeWidth="3" strokeLinejoin="round" />
          </svg>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
            <div className="h-px w-full" style={{ background: "#EFE4D2" }} />
            <span className="mt-2 text-sm text-muted">No trades yet today</span>
          </div>
        )}
      </div>
      <div className="relative -mt-2 flex justify-between text-xs text-muted">
        <span>9:30 AM</span>
        {hasTrades && nowPct > 12 && nowPct < 88 && (
          <span className="absolute -translate-x-1/2" style={{ left: `${nowPct}%`, color: "#7A3343", fontWeight: 600 }}>Now</span>
        )}
        <span>4:00 PM</span>
      </div>
    </div>
  );
}
