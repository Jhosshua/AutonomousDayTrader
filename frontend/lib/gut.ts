import type { Position, RecoveredSessionSummary, StrategyState, TradeRecord } from "../types/trading";
import type { AttentionItem } from "./plain";
import { dedupeTrades, etDateKey, etMinutesOfDay, etParts, formatSignedMoney, isOvernightPosition, strategyTheme } from "./plain.ts";

export interface ResultDay { date: string; label: string; pnl: number | null; trades: number }
const cents = (n: number) => Math.round(n * 100) / 100;

/** Calendar arithmetic on ET date keys avoids DST changing the seven-date window. */
export function tradingDates(today: string): string[] {
  const end = new Date(`${today}T12:00:00Z`);
  return Array.from({ length: 7 }, (_, i) => new Date(end.getTime() - (6 - i) * 86400000))
    .filter(d => d.getUTCDay() !== 0 && d.getUTCDay() !== 6).map(d => d.toISOString().slice(0, 10));
}

export function groupBySessionDate(items: TradeRecord[], dates: string[] = []): ResultDay[] {
  const grouped = new Map<string, ResultDay>(dates.map(date => [date, { date, label: dayLabel(date), pnl: 0, trades: 0 }]));
  for (const t of dedupeTrades(items)) {
    const day = grouped.get(t.session_date) ?? { date: t.session_date, label: dayLabel(t.session_date), pnl: 0, trades: 0 };
    day.pnl = cents((day.pnl ?? 0) + t.realized_pnl);
    day.trades++;
    grouped.set(day.date, day);
  }
  return [...grouped.values()].sort((a, b) => a.date.localeCompare(b.date));
}

function dayLabel(date: string) {
  const d = new Date(`${date}T12:00:00Z`);
  return `${d.toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short" })} ${d.getUTCDate()}`;
}

export function resultsDays(items: TradeRecord[], sessions: RecoveredSessionSummary[], today: string): ResultDay[] {
  const grouped = new Map(groupBySessionDate(items).map(d => [d.date, d]));
  const byDate = new Map(sessions.map(s => [s.session_date, s]));
  return tradingDates(today).map(date => {
    const rows = grouped.get(date);
    const session = byDate.get(date);
    return {
      date, label: dayLabel(date),
      pnl: date === today ? rows?.pnl ?? 0 : session ? session.finished_trade_result ?? null : rows?.pnl ?? 0,
      trades: date === today ? rows?.trades ?? 0 : session?.trades_count ?? rows?.trades ?? 0,
    };
  });
}

export function weekResultByStrategy(items: TradeRecord[], recovered: RecoveredSessionSummary[] = []): Record<string, number> {
  const out: Record<string, number> = {};
  for (const t of dedupeTrades(items)) out[t.strategy_id] = cents((out[t.strategy_id] ?? 0) + t.realized_pnl);
  for (const s of new Map(recovered.map(s => [s.session_date, s])).values()) {
    for (const [id, agg] of Object.entries(s.strategies ?? {})) out[id] = cents((out[id] ?? 0) + agg.realized_pnl);
  }
  out.overnight = cents(["overnight_nvda", "overnight_iren", "overnight_hut"].reduce((n, id) => n + (out[id] ?? 0), 0));
  return out;
}

export function freshnessLabel(ageSec: number, connectionState: string): string {
  if (!Number.isFinite(ageSec)) return "Connecting";
  const age = Math.max(0, Math.floor(ageSec));
  const time = age < 60 ? `${age} s` : `${Math.floor(age / 60)} min`;
  return age > 60 || connectionState !== "live" ? `stale ${time}` : `Running · updated ${time} ago`;
}

export function isMarketOpen(marketStatus: string, tradingDay: boolean | undefined): boolean {
  return ["OPEN", "FLATTENING"].includes(marketStatus) && tradingDay !== false;
}

export function overnightPriceIsStale(p: Pick<Position, "overnight" | "strategy_id">, etMin: number, tradingDay: boolean): boolean {
  return isOvernightPosition(p) && (etMin >= 960 || etMin < 570 || !tradingDay);
}

export function playbooksInitiallyOpen(strategies: StrategyState[], dayPositions: number): boolean {
  return dayPositions > 0 || strategies.some(s => ["CAN_TRADE", "LIMITED", "BLOCKED", "MANAGING", "NO_TRADE_TODAY"].includes(s.window?.state ?? ""));
}

export function playbookSummaryLine(strategies: StrategyState[], ledger7d: Record<string, number>, overnight: number): string {
  const done = strategies.filter(s => s.window?.state === "DONE_FOR_DAY").length;
  const active = strategies.filter(s => ["CAN_TRADE", "LIMITED", "MANAGING"].includes(s.window?.state ?? "")).length;
  const result = strategies.find(s => (ledger7d[s.id] ?? 0) !== 0);
  return [done ? `${done} done for today` : active ? `${active} watching or trading` : "Day playbooks resting",
    result ? `${strategyTheme(result.id, result.name).name.replace(" Morning Plan", "")} ${formatSignedMoney(ledger7d[result.id])} in 7 days` : null,
    overnight ? `Overnight holding ${overnight}` : "No overnight holds"].filter(Boolean).join(" · ");
}

/** Only day positions are accepted by FLATTEN_POSITION. Never offer that handler for an overnight or swing alarm. */
export function firstAlarmAction(attention: AttentionItem[], dayPositions: Position[]): { label: string; symbol: string } | null {
  const key = attention[0]?.key;
  if (!key?.startsWith("unprotected:") && !key?.startsWith("orb-orphan:")) return null;
  const symbol = key.slice(key.indexOf(":") + 1);
  const p = dayPositions.find(p => p.symbol === symbol);
  return p && !isOvernightPosition(p) && !p.strategy_id?.startsWith("swing") ? { label: `${p.side === "SHORT" ? "Close" : "Sell"} ${symbol} now`, symbol } : null;
}

export function lockCountdown(until: string | undefined, now: Date, buysPlanned: boolean): string | null {
  const remaining = until ? Date.parse(until) - now.getTime() : NaN;
  if (!buysPlanned || !Number.isFinite(remaining) || remaining <= 0 || remaining > 20 * 60000) return null;
  return `Overnight buy locks in ${Math.ceil(remaining / 60000)} min`;
}

export function clockLine(now: Date, marketOpen: boolean, tradingDay: boolean, nextChange?: string | null): string {
  const date = now.toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric" });
  const time = now.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const minutes = etMinutesOfDay(now);
  let suffix = "";
  if (marketOpen) {
    const left = Math.max(0, 960 - minutes);
    suffix = `closes in ${Math.floor(left / 60)}h ${left % 60}m`;
  } else if (tradingDay && minutes < 570) {
    const left = 570 - minutes;
    suffix = `opens in ${Math.floor(left / 60)}h ${left % 60}m`;
  } else if (nextChange && Date.parse(nextChange) > now.getTime()) {
    suffix = `next change ${new Date(nextChange).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  } else suffix = "market closed";
  return `${date} · ${time} · ${suffix}`;
}

export function tradingDayOf(strategies: StrategyState[], now: Date): boolean {
  return strategies.find(s => typeof s.window?.trading_day === "boolean")?.window?.trading_day ?? ![0, 6].includes(etParts(now).weekday);
}

export { etDateKey };
