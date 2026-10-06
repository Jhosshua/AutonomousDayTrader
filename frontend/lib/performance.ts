import type { RecoveredSessionSummary, TradeRecord } from "../types/trading.ts";

export type PerformancePeriod = "day" | "week" | "month" | "all";
export type PerformanceDirection = "up" | "down" | "flat" | "unknown";
export type Completeness = "complete" | "partial" | "unavailable";

export interface SignedChange {
  signedDollars: number | null;
  percent: number | null;
  direction: PerformanceDirection;
}

export interface SeparateResult {
  signedDollars: number | null;
  percent: number | null;
  direction: PerformanceDirection;
}

export interface PerformanceDay {
  date: string;
  openingEquity: number | null;
  currentEquity: number | null;
  accountChange: SignedChange;
  finishedTradeResult: SeparateResult;
  tradeCount: number;
  detailedTradeCount: number;
  aggregateOnly: boolean;
  source: "session" | "trades" | "live";
  detailsComplete: boolean;
}

export interface PerformanceChartPoint {
  at: string;
  value: number;
  source: "finished-trade" | "session-close" | "live";
  tradeId?: string;
}

export interface PerformanceAggregationInput {
  period: PerformancePeriod;
  /** Eastern calendar date in YYYY-MM-DD form. */
  today: string;
  currentEquity: unknown;
  dailyOpeningEquity?: unknown;
  openHoldingResult?: {
    dollars: unknown;
    percent?: unknown;
  } | null;
  sessions: readonly RecoveredSessionSummary[];
  trades: readonly TradeRecord[];
  /** True when the client stopped loading trade pages before next_cursor became null. */
  tradesTruncated?: boolean;
  /** Oldest date represented by loaded detailed trade rows. */
  oldestLoadedDate?: string | null;
  /** False when the caller knows durable session history is incomplete. */
  historyComplete?: boolean;
  /** Timestamp for the current live equity point. */
  asOf?: string;
}

export interface PerformanceAggregation {
  period: PerformancePeriod;
  periodLabel: "Day" | "Week" | "Month" | "Since recorded history";
  comparisonLabel: string;
  rangeStart: string;
  rangeEnd: string;
  openingEquity: number | null;
  currentEquity: number | null;
  accountChange: SignedChange;
  finishedTradeResult: SeparateResult;
  openHoldingResult: SeparateResult;
  tradeCount: number;
  bestDay: PerformanceDay | null;
  worstDay: PerformanceDay | null;
  days: PerformanceDay[];
  chart: {
    kind: "cumulative-finished-trade-result" | "session-closing-equity";
    points: PerformanceChartPoint[];
  };
  completeness: {
    account: Completeness;
    sessionCoverage: Completeness;
    tradeDetails: Completeness;
    partialLabels: string[];
  };
}

const DAY_MS = 24 * 60 * 60 * 1000;

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function validDateKey(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function shiftDate(dateKey: string, days: number): string {
  const parsed = new Date(`${dateKey}T00:00:00.000Z`);
  return new Date(parsed.getTime() + days * DAY_MS).toISOString().slice(0, 10);
}

function direction(value: number | null): PerformanceDirection {
  if (value === null) return "unknown";
  if (value > 0) return "up";
  if (value < 0) return "down";
  return "flat";
}

function percentChange(opening: number | null, current: number | null): number | null {
  if (opening === null || current === null || opening === 0) return null;
  return money(((current - opening) / opening) * 100);
}

function signedChange(opening: number | null, current: number | null): SignedChange {
  const signedDollars = opening === null || current === null ? null : money(current - opening);
  return {
    signedDollars,
    percent: percentChange(opening, current),
    direction: direction(signedDollars),
  };
}

function separateResult(dollars: unknown, percent: unknown = null): SeparateResult {
  const signedDollars = finite(dollars);
  return {
    signedDollars: signedDollars === null ? null : money(signedDollars),
    percent: finite(percent),
    direction: direction(signedDollars),
  };
}

function periodLabel(period: PerformancePeriod): PerformanceAggregation["periodLabel"] {
  if (period === "day") return "Day";
  if (period === "week") return "Week";
  if (period === "month") return "Month";
  return "Since recorded history";
}

function periodStart(period: PerformancePeriod, today: string, allDates: readonly string[]): string {
  if (period === "day") return today;
  if (period === "week") return shiftDate(today, -6);
  if (period === "month") return shiftDate(today, -29);
  return allDates[0] ?? today;
}

function inRange(dateKey: string, start: string, end: string): boolean {
  return dateKey >= start && dateKey <= end;
}

function normalizeTradeRows(trades: readonly TradeRecord[]): {
  rows: TradeRecord[];
  invalidDateCount: number;
  duplicateCount: number;
} {
  const rows: TradeRecord[] = [];
  const seen = new Set<string>();
  let invalidDateCount = 0;
  let duplicateCount = 0;
  for (const trade of trades) {
    if (!validDateKey(trade?.session_date)) {
      invalidDateCount += 1;
      continue;
    }
    const identity = typeof trade.trade_id === "string" && trade.trade_id
      ? trade.trade_id
      : `${trade.session_date}|${trade.closed_at}|${trade.symbol}|${trade.strategy_id}`;
    if (seen.has(identity)) {
      duplicateCount += 1;
      continue;
    }
    seen.add(identity);
    rows.push(trade);
  }
  return { rows, invalidDateCount, duplicateCount };
}

function normalizeSessions(sessions: readonly RecoveredSessionSummary[]): {
  byDate: Map<string, RecoveredSessionSummary>;
  invalidDateCount: number;
  duplicateCount: number;
} {
  const byDate = new Map<string, RecoveredSessionSummary>();
  let invalidDateCount = 0;
  let duplicateCount = 0;
  for (const session of sessions) {
    if (!validDateKey(session?.session_date)) {
      invalidDateCount += 1;
      continue;
    }
    if (byDate.has(session.session_date)) {
      duplicateCount += 1;
      continue;
    }
    byDate.set(session.session_date, session);
  }
  return { byDate, invalidDateCount, duplicateCount };
}

function tradeRowsByDate(trades: readonly TradeRecord[]): Map<string, TradeRecord[]> {
  const byDate = new Map<string, TradeRecord[]>();
  for (const trade of trades) {
    const rows = byDate.get(trade.session_date) ?? [];
    rows.push(trade);
    byDate.set(trade.session_date, rows);
  }
  return byDate;
}

function validTradePnls(rows: readonly TradeRecord[]): number[] {
  return rows.map((row) => finite(row.realized_pnl)).filter((value): value is number => value !== null);
}

function safeTradeCount(value: unknown, fallback: number): number {
  const parsed = finite(value);
  return parsed === null || parsed < 0 ? fallback : Math.floor(parsed);
}

function buildDay(
  date: string,
  session: RecoveredSessionSummary | undefined,
  trades: readonly TradeRecord[],
  input: PerformanceAggregationInput,
  detailedRowsComplete: boolean,
): PerformanceDay {
  const tradePnls = validTradePnls(trades);
  const detailedResult = money(tradePnls.reduce((sum, value) => sum + value, 0));
  const detailedCount = trades.length;

  if (session) {
    const openingEquity = finite(session.opening_equity);
    const currentEquity = finite(session.closing_equity);
    const tradeCount = safeTradeCount(session.trades_count, detailedCount);
    const aggregateOnly = !!session.aggregate_only;
    const detailsComplete = !aggregateOnly
      && detailedRowsComplete
      && detailedCount === tradeCount
      && tradePnls.length === detailedCount;
    const explicitFinishedResult = finite(session.finished_trade_result);
    const finishedTradeResult = explicitFinishedResult !== null
      ? separateResult(explicitFinishedResult)
      : aggregateOnly
        ? separateResult(session.realized_pnl)
        : separateResult(detailsComplete ? detailedResult : null);
    return {
      date,
      openingEquity,
      currentEquity,
      accountChange: signedChange(openingEquity, currentEquity),
      finishedTradeResult,
      tradeCount,
      detailedTradeCount: detailedCount,
      aggregateOnly,
      source: "session",
      detailsComplete,
    };
  }

  if (date === input.today) {
    const openingEquity = finite(input.dailyOpeningEquity);
    const currentEquity = finite(input.currentEquity);
    return {
      date,
      openingEquity,
      currentEquity,
      accountChange: signedChange(openingEquity, currentEquity),
      finishedTradeResult: separateResult(
        detailedRowsComplete && tradePnls.length === detailedCount ? detailedResult : null,
      ),
      tradeCount: detailedCount,
      detailedTradeCount: detailedCount,
      aggregateOnly: false,
      source: "live",
      detailsComplete: detailedRowsComplete && tradePnls.length === detailedCount,
    };
  }

  return {
    date,
    openingEquity: null,
    currentEquity: null,
    accountChange: signedChange(null, null),
    finishedTradeResult: separateResult(
      detailedRowsComplete && tradePnls.length === detailedCount ? detailedResult : null,
    ),
    tradeCount: detailedCount,
    detailedTradeCount: detailedCount,
    aggregateOnly: false,
    source: "trades",
    detailsComplete: detailedRowsComplete && tradePnls.length === detailedCount,
  };
}

function rankDays(days: readonly PerformanceDay[], best: boolean): PerformanceDay | null {
  const ranked = days.filter((day) => day.accountChange.percent !== null);
  ranked.sort((left, right) => {
    const percentDifference = (left.accountChange.percent ?? 0) - (right.accountChange.percent ?? 0);
    if (percentDifference !== 0) return best ? -percentDifference : percentDifference;
    const dollarDifference = (left.accountChange.signedDollars ?? 0) - (right.accountChange.signedDollars ?? 0);
    if (dollarDifference !== 0) return best ? -dollarDifference : dollarDifference;
    return left.date.localeCompare(right.date);
  });
  return ranked[0] ?? null;
}

function dayChart(trades: readonly TradeRecord[], today: string): {
  points: PerformanceChartPoint[];
  invalidCloseCount: number;
  invalidPnlCount: number;
} {
  const candidates: Array<{ trade: TradeRecord; time: number; pnl: number }> = [];
  let invalidCloseCount = 0;
  let invalidPnlCount = 0;
  for (const trade of trades) {
    if (trade.session_date !== today) continue;
    const time = Date.parse(trade.closed_at);
    const pnl = finite(trade.realized_pnl);
    if (!Number.isFinite(time)) {
      invalidCloseCount += 1;
      continue;
    }
    if (pnl === null) {
      invalidPnlCount += 1;
      continue;
    }
    candidates.push({ trade, time, pnl });
  }
  candidates.sort((left, right) => left.time - right.time || left.trade.trade_id.localeCompare(right.trade.trade_id));
  let cumulative = 0;
  return {
    points: candidates.map(({ trade, pnl }) => {
      cumulative = money(cumulative + pnl);
      return {
        at: trade.closed_at,
        value: cumulative,
        source: "finished-trade" as const,
        tradeId: trade.trade_id,
      };
    }),
    invalidCloseCount,
    invalidPnlCount,
  };
}

function equityChart(
  sessions: ReadonlyMap<string, RecoveredSessionSummary>,
  start: string,
  end: string,
  currentEquity: number | null,
  asOf: string,
): PerformanceChartPoint[] {
  const points: PerformanceChartPoint[] = [];
  for (const date of [...sessions.keys()].sort()) {
    if (!inRange(date, start, end)) continue;
    const closingEquity = finite(sessions.get(date)?.closing_equity);
    if (closingEquity === null) continue;
    points.push({ at: date, value: money(closingEquity), source: "session-close" });
  }
  if (currentEquity !== null) {
    points.push({ at: asOf, value: money(currentEquity), source: "live" });
  }
  return points;
}

function addLabel(labels: string[], condition: boolean, label: string): void {
  if (condition && !labels.includes(label)) labels.push(label);
}

export function aggregatePerformance(input: PerformanceAggregationInput): PerformanceAggregation {
  if (!validDateKey(input.today)) {
    throw new Error("today must be a valid YYYY-MM-DD Eastern date key");
  }

  const normalizedTrades = normalizeTradeRows(input.trades ?? []);
  const normalizedSessions = normalizeSessions(input.sessions ?? []);
  const byTradeDate = tradeRowsByDate(normalizedTrades.rows);
  const allDates = [...new Set([...normalizedSessions.byDate.keys(), ...byTradeDate.keys()])].sort();
  const rangeStart = periodStart(input.period, input.today, allDates);
  const rangeEnd = input.today;
  const oldestLoadedDate = validDateKey(input.oldestLoadedDate) ? input.oldestLoadedDate : null;
  const detailedRowsComplete = !input.tradesTruncated
    || (oldestLoadedDate !== null && oldestLoadedDate <= rangeStart);

  const dates = new Set<string>();
  for (const date of normalizedSessions.byDate.keys()) if (inRange(date, rangeStart, rangeEnd)) dates.add(date);
  for (const date of byTradeDate.keys()) if (inRange(date, rangeStart, rangeEnd)) dates.add(date);
  if (input.period === "day") dates.add(input.today);
  if (finite(input.dailyOpeningEquity) !== null) dates.add(input.today);

  const days = [...dates]
    .sort()
    .map((date) => buildDay(
      date,
      normalizedSessions.byDate.get(date),
      byTradeDate.get(date) ?? [],
      input,
      detailedRowsComplete,
    ));

  const openingSession = [...normalizedSessions.byDate.entries()]
    .filter(([date, session]) => inRange(date, rangeStart, rangeEnd) && finite(session.opening_equity) !== null)
    .sort(([left], [right]) => left.localeCompare(right))[0];
  const openingEquity = input.period === "day"
    ? finite(input.dailyOpeningEquity) ?? finite(normalizedSessions.byDate.get(input.today)?.opening_equity)
    : finite(openingSession?.[1].opening_equity);
  const currentEquity = finite(input.currentEquity);
  const accountChange = signedChange(openingEquity, currentEquity);

  const finishedValues = days.map((day) => day.finishedTradeResult.signedDollars);
  const finishedTradeResult = finishedValues.some((value) => value === null)
    ? separateResult(null)
    : separateResult(money((finishedValues as number[]).reduce((sum, value) => sum + value, 0)));
  const tradeCount = days.reduce((sum, day) => sum + day.tradeCount, 0);
  const openHoldingResult = separateResult(
    input.openHoldingResult?.dollars,
    input.openHoldingResult?.percent,
  );

  const labels: string[] = [];
  const missingSessionDates = days.filter((day) => day.source === "trades").length;
  const aggregateOnlyDates = days.filter((day) => day.aggregateOnly).length;
  const incompleteDetailDates = days.filter((day) => !day.detailsComplete).length;
  const incompleteNonaggregateDates = days.filter(
    (day) => day.source === "session" && !day.aggregateOnly && !day.detailsComplete && day.tradeCount > 0,
  ).length;
  addLabel(labels, accountChange.signedDollars === null, "Account performance unavailable because opening or current equity is missing.");
  addLabel(labels, missingSessionDates > 0, "Some days lack session equity summaries.");
  addLabel(labels, aggregateOnlyDates > 0, "Daily total only. Individual trade details are unavailable.");
  addLabel(
    labels,
    incompleteNonaggregateDates > 0,
    "Finished trade totals are unavailable where detailed trade rows are incomplete.",
  );
  addLabel(labels, !detailedRowsComplete, "Only the most recent trades are loaded for this period.");
  addLabel(labels, input.historyComplete === false, "Recorded session history is incomplete.");
  addLabel(labels, normalizedTrades.invalidDateCount > 0, "Trades with invalid session dates were ignored.");
  addLabel(labels, normalizedSessions.invalidDateCount > 0, "Sessions with invalid dates were ignored.");
  addLabel(labels, normalizedTrades.duplicateCount > 0, "Duplicate trade rows were ignored.");
  addLabel(labels, normalizedSessions.duplicateCount > 0, "Duplicate session summaries were ignored.");
  addLabel(labels, finishedTradeResult.signedDollars === null, "Some finished trade results are unavailable.");

  const asOfTime = typeof input.asOf === "string" && Number.isFinite(Date.parse(input.asOf))
    ? input.asOf
    : input.today;
  const dayChartResult = dayChart(normalizedTrades.rows, input.today);
  addLabel(labels, dayChartResult.invalidCloseCount > 0, "Day chart excludes trades with invalid close times.");
  addLabel(labels, dayChartResult.invalidPnlCount > 0, "Day chart excludes trades with invalid results.");

  const accountCompleteness: Completeness = accountChange.signedDollars === null
    ? "unavailable"
    : missingSessionDates > 0 || input.historyComplete === false
      ? "partial"
      : "complete";
  const sessionCoverage: Completeness = normalizedSessions.byDate.size === 0
    ? "unavailable"
    : missingSessionDates > 0 || input.historyComplete === false || normalizedSessions.invalidDateCount > 0
      ? "partial"
      : "complete";
  const tradeDetails: Completeness = tradeCount === 0
    ? "complete"
    : !detailedRowsComplete || aggregateOnlyDates > 0 || incompleteDetailDates > 0
      ? "partial"
      : "complete";

  return {
    period: input.period,
    periodLabel: periodLabel(input.period),
    comparisonLabel: input.period === "all"
      ? "the first recorded opening equity"
      : `opening equity on ${openingSession?.[0] ?? input.today}`,
    rangeStart,
    rangeEnd,
    openingEquity,
    currentEquity,
    accountChange,
    finishedTradeResult,
    openHoldingResult,
    tradeCount,
    bestDay: rankDays(days, true),
    worstDay: rankDays(days, false),
    days,
    chart: input.period === "day"
      ? { kind: "cumulative-finished-trade-result", points: dayChartResult.points }
      : {
          kind: "session-closing-equity",
          points: equityChart(normalizedSessions.byDate, rangeStart, rangeEnd, currentEquity, asOfTime),
        },
    completeness: {
      account: accountCompleteness,
      sessionCoverage,
      tradeDetails,
      partialLabels: labels,
    },
  };
}
