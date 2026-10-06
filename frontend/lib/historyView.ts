import type {
  HistoryDayRow,
  HistoryPeriod,
  HistoryPeriodData,
  HistoryPlanRow,
  HistoryTradeRow,
} from "../components/HistoryExplorer.tsx";
import type {
  PerformanceAggregation,
  PerformancePeriod,
} from "./performance.ts";
import type { RecoveredSessionSummary, TradeRecord } from "../types/trading.ts";

export interface HistoryViewInput {
  performance: Record<PerformancePeriod, PerformanceAggregation>;
  sessions: readonly RecoveredSessionSummary[];
  trades: readonly TradeRecord[];
  planName: (strategyId: string) => string;
  companyName: (symbol: string) => string;
  dateLabel: (date: string) => string;
  timeLabel: (timestamp: string) => string;
}

const PERIOD_MAP: Record<PerformancePeriod, HistoryPeriod> = {
  day: "Day",
  week: "Week",
  month: "Month",
  all: "All",
};

function usable(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : Number.NaN;
}

function round(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function signedMoney(value: number): string {
  if (!Number.isFinite(value)) return "Unavailable";
  const absolute = Math.abs(value).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (value === 0) return absolute;
  return `${value > 0 ? "+" : "−"}${absolute}`;
}

function selectedTrades(
  trades: readonly TradeRecord[],
  aggregation: PerformanceAggregation,
): TradeRecord[] {
  const seen = new Set<string>();
  return trades
    .filter(
      (trade) =>
        trade.session_date >= aggregation.rangeStart &&
        trade.session_date <= aggregation.rangeEnd,
    )
    .filter((trade) => {
      const id =
        trade.trade_id ||
        `${trade.session_date}|${trade.closed_at}|${trade.symbol}|${trade.strategy_id}`;
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    })
    .sort(
      (left, right) =>
        right.closed_at.localeCompare(left.closed_at) ||
        right.trade_id.localeCompare(left.trade_id),
    );
}

function selectedSessions(
  sessions: readonly RecoveredSessionSummary[],
  aggregation: PerformanceAggregation,
): Map<string, RecoveredSessionSummary> {
  const selected = new Map<string, RecoveredSessionSummary>();
  for (const session of sessions) {
    if (
      session.session_date >= aggregation.rangeStart &&
      session.session_date <= aggregation.rangeEnd &&
      !selected.has(session.session_date)
    ) {
      selected.set(session.session_date, session);
    }
  }
  return selected;
}

function dayRows(
  aggregation: PerformanceAggregation,
  sessions: ReadonlyMap<string, RecoveredSessionSummary>,
  trades: readonly TradeRecord[],
  input: HistoryViewInput,
): HistoryDayRow[] {
  const tradesByDate = new Map<string, TradeRecord[]>();
  for (const trade of trades) {
    const rows = tradesByDate.get(trade.session_date) ?? [];
    rows.push(trade);
    tradesByDate.set(trade.session_date, rows);
  }

  return [...aggregation.days]
    .sort((left, right) => right.date.localeCompare(left.date))
    .map((day) => {
      const session = sessions.get(day.date);
      const strategyDetails = session?.strategies
        ? Object.entries(session.strategies)
            .sort((left, right) => right[1].realized_pnl - left[1].realized_pnl)
            .map(([strategyId, result]) => ({
              label: `${input.planName(strategyId)}, ${result.trades_count} ${
                result.trades_count === 1 ? "trade" : "trades"
              }`,
              value: signedMoney(result.realized_pnl),
              resultDollars: result.realized_pnl,
            }))
        : [...(tradesByDate.get(day.date) ?? [])]
            .sort((left, right) => right.realized_pnl - left.realized_pnl)
            .map((trade) => ({
              label: `${input.companyName(trade.symbol)} ${trade.symbol}, ${input.planName(
                trade.strategy_id,
              )}`,
              value: signedMoney(trade.realized_pnl),
              resultDollars: trade.realized_pnl,
            }));
      const accountKnown =
        day.accountChange.signedDollars !== null &&
        day.accountChange.percent !== null &&
        day.openingEquity !== null;

      return {
        id: day.date,
        kind: "day",
        dateLabel: input.dateLabel(day.date),
        resultDollars: usable(day.accountChange.signedDollars),
        resultPercent: usable(day.accountChange.percent),
        baselineValue: usable(day.openingEquity),
        baselineLabel: "opening equity",
        completeness: accountKnown ? "complete" : "partial",
        completenessNote: accountKnown
          ? undefined
          : "Opening or closing account equity is unavailable for this day.",
        openingEquity: day.openingEquity ?? undefined,
        closingEquity: day.currentEquity ?? undefined,
        finishedTradeResult: day.finishedTradeResult.signedDollars ?? undefined,
        tradeCount: day.tradeCount,
        wonCount: session?.wins ?? undefined,
        lostCount: session?.losses ?? undefined,
        dailyTotalOnly: day.aggregateOnly,
        details: day.aggregateOnly ? undefined : strategyDetails,
      };
    });
}

interface PlanAccumulator {
  strategyId: string;
  result: number;
  loadedTrades: number;
  totalTrades: number;
  complete: boolean;
  byDate: Map<string, { result: number; trades: number }>;
}

function planRows(
  aggregation: PerformanceAggregation,
  sessions: ReadonlyMap<string, RecoveredSessionSummary>,
  trades: readonly TradeRecord[],
  input: HistoryViewInput,
): HistoryPlanRow[] {
  const plans = new Map<string, PlanAccumulator>();
  const datesWithSessionStrategies = new Set<string>();

  for (const [date, session] of sessions) {
    if (!session.strategies) continue;
    datesWithSessionStrategies.add(date);
    for (const [strategyId, result] of Object.entries(session.strategies)) {
      const current = plans.get(strategyId) ?? {
        strategyId,
        result: 0,
        loadedTrades: 0,
        totalTrades: 0,
        complete: true,
        byDate: new Map(),
      };
      const detailedCount = trades.filter(
        (trade) => trade.session_date === date && trade.strategy_id === strategyId,
      ).length;
      current.result += result.realized_pnl;
      current.loadedTrades += detailedCount;
      current.totalTrades += result.trades_count;
      current.complete =
        current.complete &&
        session.trade_detail_complete === true &&
        detailedCount === result.trades_count;
      current.byDate.set(date, {
        result: result.realized_pnl,
        trades: result.trades_count,
      });
      plans.set(strategyId, current);
    }
  }

  for (const trade of trades) {
    if (datesWithSessionStrategies.has(trade.session_date)) continue;
    const current = plans.get(trade.strategy_id) ?? {
      strategyId: trade.strategy_id,
      result: 0,
      loadedTrades: 0,
      totalTrades: 0,
      complete: true,
      byDate: new Map(),
    };
    current.result += trade.realized_pnl;
    current.loadedTrades += 1;
    current.totalTrades += 1;
    const day = current.byDate.get(trade.session_date) ?? { result: 0, trades: 0 };
    day.result += trade.realized_pnl;
    day.trades += 1;
    current.byDate.set(trade.session_date, day);
    plans.set(trade.strategy_id, current);
  }

  return [...plans.values()]
    .sort((left, right) => right.result - left.result)
    .map((plan) => {
      const opening = usable(aggregation.openingEquity);
      const percent =
        Number.isFinite(opening) && opening !== 0
          ? round((plan.result / Math.abs(opening)) * 100)
          : Number.NaN;
      const complete =
        plan.complete && aggregation.completeness.tradeDetails === "complete";
      return {
        id: plan.strategyId,
        kind: "plan",
        planName: input.planName(plan.strategyId),
        resultDollars: round(plan.result),
        resultPercent: percent,
        baselineValue: opening,
        baselineLabel: "period opening equity",
        completeness: complete ? "complete" : "partial",
        completenessNote: complete
          ? undefined
          : "Plan totals are complete where daily summaries exist. Individual trade rows may be missing.",
        loadedTradeCount: plan.loadedTrades,
        totalTradeCount: plan.totalTrades,
        dateRangeLabel: `${input.dateLabel(aggregation.rangeStart)} to ${input.dateLabel(
          aggregation.rangeEnd,
        )}`,
        details: [...plan.byDate.entries()]
          .sort(([left], [right]) => right.localeCompare(left))
          .map(([date, detail]) => ({
            label: `${input.dateLabel(date)}, ${detail.trades} ${
              detail.trades === 1 ? "trade" : "trades"
            }`,
            value: signedMoney(detail.result),
            resultDollars: detail.result,
          })),
      };
    });
}

function tradeRows(trades: readonly TradeRecord[], input: HistoryViewInput): HistoryTradeRow[] {
  return trades.map((trade) => {
    const baseline = Math.abs(trade.quantity * trade.avg_entry_price);
    const percent =
      Number.isFinite(baseline) && baseline !== 0
        ? round((trade.realized_pnl / baseline) * 100)
        : Number.NaN;
    return {
      id: trade.trade_id,
      kind: "trade",
      symbol: trade.symbol,
      companyName: input.companyName(trade.symbol),
      planName: input.planName(trade.strategy_id),
      dateLabel: input.dateLabel(trade.session_date),
      openedAt: input.timeLabel(trade.opened_at),
      closedAt: input.timeLabel(trade.closed_at),
      side: trade.side === "SHORT" ? "SHORT" : "LONG",
      shares: trade.quantity,
      entryPrice: trade.avg_entry_price,
      exitPrice: trade.avg_exit_price,
      exitReason: trade.exit_reason || "No exit reason recorded",
      resultDollars: trade.realized_pnl,
      resultPercent: percent,
      baselineValue: baseline,
      baselineLabel: "entry value",
      completeness: "complete",
    };
  });
}

export function buildHistoryView(
  input: HistoryViewInput,
): Partial<Record<HistoryPeriod, HistoryPeriodData>> {
  const result: Partial<Record<HistoryPeriod, HistoryPeriodData>> = {};

  for (const period of ["day", "week", "month", "all"] as const) {
    const aggregation = input.performance[period];
    const trades = selectedTrades(input.trades, aggregation);
    const sessions = selectedSessions(input.sessions, aggregation);
    const partial =
      aggregation.completeness.account !== "complete" ||
      aggregation.completeness.sessionCoverage !== "complete" ||
      aggregation.completeness.tradeDetails !== "complete";
    result[PERIOD_MAP[period]] = {
      summary: {
        currentEquity: usable(aggregation.currentEquity),
        baselineEquity: usable(aggregation.openingEquity),
        baselineLabel:
          period === "all"
            ? "first recorded opening equity"
            : `opening equity on ${input.dateLabel(aggregation.rangeStart)}`,
        changeDollars: usable(aggregation.accountChange.signedDollars),
        changePercent: usable(aggregation.accountChange.percent),
        completeness: partial ? "partial" : "complete",
        completenessNote:
          aggregation.completeness.partialLabels.length > 0
            ? aggregation.completeness.partialLabels.join(" ")
            : undefined,
        finishedTradeResult: aggregation.finishedTradeResult.signedDollars ?? undefined,
        finishedTradeCount: aggregation.tradeCount,
      },
      rows: {
        Days: dayRows(aggregation, sessions, trades, input),
        Plans: planRows(aggregation, sessions, trades, input),
        Trades: tradeRows(trades, input),
      },
    };
  }

  return result;
}
