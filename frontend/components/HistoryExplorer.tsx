"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";

export type HistoryPeriod = "Day" | "Week" | "Month" | "All";
export type HistoryBreakdown = "Days" | "Plans" | "Trades";
export type HistoryCompleteness = "complete" | "partial";
export type HistoryTradeSide = "LONG" | "SHORT";

export interface HistorySummary {
  currentEquity: number;
  baselineEquity: number;
  baselineLabel: string;
  changeDollars: number;
  changePercent: number;
  completeness: HistoryCompleteness;
  completenessNote?: string;
  finishedTradeResult?: number;
  finishedTradeCount?: number;
}

export interface HistoryRowBase {
  id: string;
  resultDollars: number;
  resultPercent: number;
  baselineValue: number;
  baselineLabel: string;
  completeness: HistoryCompleteness;
  completenessNote?: string;
}

export interface HistoryDetailLine {
  label: string;
  value: string;
  resultDollars?: number;
}

export interface HistoryDayRow extends HistoryRowBase {
  kind: "day";
  dateLabel: string;
  openingEquity?: number;
  closingEquity?: number;
  finishedTradeResult?: number;
  tradeCount?: number;
  wonCount?: number;
  lostCount?: number;
  dailyTotalOnly?: boolean;
  details?: HistoryDetailLine[];
}

export interface HistoryPlanRow extends HistoryRowBase {
  kind: "plan";
  planName: string;
  loadedTradeCount: number;
  totalTradeCount?: number;
  dateRangeLabel?: string;
  details?: HistoryDetailLine[];
}

export interface HistoryTradeRow extends HistoryRowBase {
  kind: "trade";
  symbol: string;
  companyName?: string;
  planName: string;
  dateLabel: string;
  openedAt: string;
  closedAt: string;
  side: HistoryTradeSide;
  shares: number;
  entryPrice: number;
  exitPrice: number;
  exitReason: string;
}

export type HistoryRow = HistoryDayRow | HistoryPlanRow | HistoryTradeRow;

export interface HistoryRowsByBreakdown {
  Days: HistoryDayRow[];
  Plans: HistoryPlanRow[];
  Trades: HistoryTradeRow[];
}

export interface HistoryPeriodData {
  summary: HistorySummary;
  rows: HistoryRowsByBreakdown;
}

export interface HistoryExplorerProps {
  data: Partial<Record<HistoryPeriod, HistoryPeriodData>>;
  period?: HistoryPeriod;
  breakdown?: HistoryBreakdown;
  initialPeriod?: HistoryPeriod;
  initialBreakdown?: HistoryBreakdown;
  onPeriodChange?: (period: HistoryPeriod) => void;
  onBreakdownChange?: (breakdown: HistoryBreakdown) => void;
  title?: string;
  emptyMessage?: string;
  className?: string;
}

const PERIODS: HistoryPeriod[] = ["Day", "Week", "Month", "All"];
const BREAKDOWNS: HistoryBreakdown[] = ["Days", "Plans", "Trades"];

const moneyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const countFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

function finite(value: number): number | null {
  return Number.isFinite(value) ? value : null;
}

function formatMoney(value: number): string {
  const safe = finite(value);
  return safe === null ? "Unavailable" : moneyFormatter.format(safe);
}

function formatSignedMoney(value: number): string {
  const safe = finite(value);
  if (safe === null) return "Unavailable";
  if (safe === 0) return moneyFormatter.format(0);
  return `${safe > 0 ? "+" : "−"}${moneyFormatter.format(Math.abs(safe))}`;
}

function formatSignedPercent(value: number): string {
  const safe = finite(value);
  if (safe === null) return "Unavailable";
  if (safe === 0) return "0.00%";
  return `${safe > 0 ? "+" : "−"}${Math.abs(safe).toFixed(2)}%`;
}

function formatCount(value: number): string {
  const safe = finite(value);
  return safe === null ? "Unavailable" : countFormatter.format(Math.max(0, safe));
}

function resultTone(value: number): string {
  if (!Number.isFinite(value) || value === 0) return "text-muted";
  return value > 0 ? "text-gain" : "text-loss";
}

function CompletenessBadge({
  completeness,
  partialLabel = "Partial",
}: {
  completeness: HistoryCompleteness;
  partialLabel?: string;
}) {
  const partial = completeness === "partial";
  return (
    <span
      className={`inline-flex min-h-6 items-center rounded-full px-2.5 text-xs font-semibold ${
        partial ? "bg-warnbg text-warn" : "bg-gainbg text-gain"
      }`}
    >
      {partial ? partialLabel : "Complete"}
    </span>
  );
}

function ResultPair({
  dollars,
  percent,
  prominent = false,
}: {
  dollars: number;
  percent: number;
  prominent?: boolean;
}) {
  const tone = resultTone(dollars);
  return (
    <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 ${tone}`}>
      <span className={`${prominent ? "text-3xl sm:text-4xl" : "text-lg"} font-bold tabular-nums`}>
        {formatSignedMoney(dollars)}
      </span>
      <span className={`${prominent ? "text-lg" : "text-sm"} font-semibold tabular-nums`}>
        {formatSignedPercent(percent)}
      </span>
    </div>
  );
}

function Baseline({
  label,
  value,
}: {
  label: string;
  value: number;
}) {
  return (
    <p className="text-xs leading-5 text-muted">
      Compared with {label} at <span className="font-semibold tabular-nums text-ink">{formatMoney(value)}</span>
    </p>
  );
}

function DetailCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-[#F6F8FE] p-3">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-ink">{value}</dd>
    </div>
  );
}

function DayRow({ row }: { row: HistoryDayRow }) {
  const dailyTotalOnly = row.dailyTotalOnly === true;
  return (
    <li className="rounded-xl border border-line bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-ink">{row.dateLabel}</h3>
            <CompletenessBadge completeness={row.completeness} />
            {dailyTotalOnly && (
              <span className="inline-flex min-h-6 items-center rounded-full bg-[#F1F3FB] px-2.5 text-xs font-semibold text-muted">
                Daily total only
              </span>
            )}
          </div>
          <div className="mt-2">
            <ResultPair dollars={row.resultDollars} percent={row.resultPercent} />
          </div>
          <div className="mt-1">
            <Baseline label={row.baselineLabel} value={row.baselineValue} />
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-5 gap-y-2 text-sm sm:text-right">
          {row.openingEquity !== undefined && (
            <div>
              <dt className="text-xs text-muted">Opening equity</dt>
              <dd className="font-semibold tabular-nums text-ink">{formatMoney(row.openingEquity)}</dd>
            </div>
          )}
          {row.closingEquity !== undefined && (
            <div>
              <dt className="text-xs text-muted">Closing equity</dt>
              <dd className="font-semibold tabular-nums text-ink">{formatMoney(row.closingEquity)}</dd>
            </div>
          )}
          {row.finishedTradeResult !== undefined && (
            <div>
              <dt className="text-xs text-muted">Finished trades</dt>
              <dd className={`font-semibold tabular-nums ${resultTone(row.finishedTradeResult)}`}>
                {formatSignedMoney(row.finishedTradeResult)}
              </dd>
            </div>
          )}
          {!dailyTotalOnly && row.tradeCount !== undefined && (
            <div>
              <dt className="text-xs text-muted">Trades</dt>
              <dd className="font-semibold text-ink">
                {formatCount(row.tradeCount)}
                {row.wonCount !== undefined && row.lostCount !== undefined
                  ? `, ${formatCount(row.wonCount)} won, ${formatCount(row.lostCount)} lost`
                  : ""}
              </dd>
            </div>
          )}
        </dl>
      </div>

      {dailyTotalOnly && (
        <p className="mt-3 rounded-lg bg-[#F1F3FB] px-3 py-2 text-xs leading-5 text-muted">
          Individual trades are unavailable for this day. No trade details are inferred from the daily total.
        </p>
      )}
      {!dailyTotalOnly && row.completenessNote && (
        <p className="mt-3 text-xs leading-5 text-muted">{row.completenessNote}</p>
      )}
      {!dailyTotalOnly && row.details && row.details.length > 0 && (
        <details className="mt-3 border-t border-line pt-1">
          <summary className="flex min-h-[44px] cursor-pointer items-center text-sm font-semibold text-darkcard">
            Open day details
          </summary>
          <ul className="pb-1">
            {row.details.map((detail, index) => (
              <li key={`${detail.label}-${index}`} className="flex items-start justify-between gap-4 border-t border-line py-2 text-sm first:border-t-0">
                <span className="text-ink">{detail.label}</span>
                <span className={`text-right font-semibold tabular-nums ${detail.resultDollars === undefined ? "text-muted" : resultTone(detail.resultDollars)}`}>
                  {detail.value}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function PlanRow({ row }: { row: HistoryPlanRow }) {
  const partial = row.completeness === "partial";
  return (
    <li className="rounded-xl border border-line bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-ink">{row.planName}</h3>
            <CompletenessBadge
              completeness={row.completeness}
              partialLabel="Loaded trades only"
            />
          </div>
          {row.dateRangeLabel && <p className="mt-1 text-xs text-muted">{row.dateRangeLabel}</p>}
          <div className="mt-2">
            <ResultPair dollars={row.resultDollars} percent={row.resultPercent} />
          </div>
          <div className="mt-1">
            <Baseline label={row.baselineLabel} value={row.baselineValue} />
          </div>
        </div>

        <div className="sm:text-right">
          <p className="text-xs text-muted">{partial ? "Loaded trades" : "Trades"}</p>
          <p className="font-semibold tabular-nums text-ink">{formatCount(row.loadedTradeCount)}</p>
          {!partial && row.totalTradeCount !== undefined && row.totalTradeCount !== row.loadedTradeCount && (
            <p className="mt-1 text-xs text-muted">{formatCount(row.totalTradeCount)} total</p>
          )}
        </div>
      </div>

      {partial && (
        <p className="mt-3 rounded-lg bg-warnbg px-3 py-2 text-xs leading-5 text-warn">
          Results and counts cover loaded trades only
          {row.totalTradeCount !== undefined ? `, ${formatCount(row.totalTradeCount)} total trades are recorded.` : "."}
        </p>
      )}
      {!partial && row.completenessNote && (
        <p className="mt-3 text-xs leading-5 text-muted">{row.completenessNote}</p>
      )}
      {row.details && row.details.length > 0 && (
        <details className="mt-3 border-t border-line pt-1">
          <summary className="flex min-h-[44px] cursor-pointer items-center text-sm font-semibold text-darkcard">
            Open plan details
          </summary>
          <ul className="pb-1">
            {row.details.map((detail, index) => (
              <li key={`${detail.label}-${index}`} className="flex items-start justify-between gap-4 border-t border-line py-2 text-sm first:border-t-0">
                <span className="text-ink">{detail.label}</span>
                <span className={`text-right font-semibold tabular-nums ${detail.resultDollars === undefined ? "text-muted" : resultTone(detail.resultDollars)}`}>
                  {detail.value}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function TradeRow({
  row,
  onOpen,
}: {
  row: HistoryTradeRow;
  onOpen: (row: HistoryTradeRow, trigger: HTMLButtonElement) => void;
}) {
  const direction = row.side === "LONG" ? "Long" : "Short";
  return (
    <li>
      <button
        type="button"
        onClick={(event: ReactMouseEvent<HTMLButtonElement>) => onOpen(row, event.currentTarget)}
        className="grid min-h-[44px] w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-xl border border-line bg-white px-4 py-3 text-left transition-colors hover:bg-[#F6F8FE] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard"
        aria-label={`Open details for ${row.symbol} trade on ${row.dateLabel}`}
      >
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-ink">
              {row.companyName ? `${row.companyName} ` : ""}
              <span className="text-sm text-muted">{row.symbol}</span>
            </span>
            <CompletenessBadge completeness={row.completeness} />
          </span>
          <span className="mt-1 block text-xs leading-5 text-muted">
            {row.dateLabel}, {row.planName}, {direction}, {formatCount(row.shares)} shares
          </span>
          <span className="mt-1 block text-xs leading-5 text-muted">
            Baseline, {row.baselineLabel} at {formatMoney(row.baselineValue)}
          </span>
        </span>

        <span className={`text-right ${resultTone(row.resultDollars)}`}>
          <span className="block text-base font-bold tabular-nums">{formatSignedMoney(row.resultDollars)}</span>
          <span className="block text-xs font-semibold tabular-nums">{formatSignedPercent(row.resultPercent)}</span>
        </span>
      </button>
    </li>
  );
}

function TradeDialog({
  trade,
  onClosed,
  titleId,
  summaryId,
}: {
  trade: HistoryTradeRow;
  onClosed: () => void;
  titleId: string;
  summaryId: string;
}) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const close = () => dialogRef.current?.close();

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-describedby={summaryId}
      onClose={onClosed}
      onClick={(event) => {
        if (event.target === dialogRef.current) close();
      }}
      className="m-auto w-[min(34rem,calc(100vw-24px))] rounded-xl border border-line bg-white p-0 text-ink shadow-2xl backdrop:bg-[#0E1330]/55"
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-darkcard">Trade detail</p>
            <h2 id={titleId} className="mt-1 break-words text-xl font-bold text-ink">
              {trade.companyName ? `${trade.companyName} ` : ""}
              <span className="text-muted">{trade.symbol}</span>
            </h2>
            <p id={summaryId} className="mt-1 text-sm text-muted">
              {trade.dateLabel}, {trade.planName}
            </p>
          </div>
          <button
            type="button"
            autoFocus
            onClick={close}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-line bg-white text-xl text-ink transition-colors hover:bg-[#F1F3FB] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard"
            aria-label="Close trade details"
          >
            ×
          </button>
        </div>

        <div className="mt-5 rounded-xl border border-line bg-[#F6F8FE] p-4">
          <ResultPair dollars={trade.resultDollars} percent={trade.resultPercent} prominent />
          <div className="mt-2">
            <Baseline label={trade.baselineLabel} value={trade.baselineValue} />
          </div>
          <div className="mt-3">
            <CompletenessBadge completeness={trade.completeness} />
          </div>
          {trade.completenessNote && <p className="mt-2 text-xs leading-5 text-muted">{trade.completenessNote}</p>}
        </div>

        <dl className="mt-4 grid grid-cols-2 gap-2">
          <DetailCell label="Direction" value={trade.side === "LONG" ? "Long" : "Short"} />
          <DetailCell label="Shares" value={formatCount(trade.shares)} />
          <DetailCell label="Entry price" value={formatMoney(trade.entryPrice)} />
          <DetailCell label="Exit price" value={formatMoney(trade.exitPrice)} />
          <DetailCell label="Opened" value={trade.openedAt} />
          <DetailCell label="Closed" value={trade.closedAt} />
          <div className="col-span-2">
            <DetailCell label="Exit reason" value={trade.exitReason} />
          </div>
        </dl>
      </div>
    </dialog>
  );
}

export default function HistoryExplorer({
  data,
  period,
  breakdown,
  initialPeriod = "Day",
  initialBreakdown = "Days",
  onPeriodChange,
  onBreakdownChange,
  title = "History",
  emptyMessage = "No history is available for this selection.",
  className = "",
}: HistoryExplorerProps) {
  const [internalPeriod, setInternalPeriod] = useState<HistoryPeriod>(initialPeriod);
  const [internalBreakdown, setInternalBreakdown] = useState<HistoryBreakdown>(initialBreakdown);
  const [selectedTrade, setSelectedTrade] = useState<HistoryTradeRow | null>(null);
  const returnFocusRef = useRef<HTMLButtonElement | null>(null);
  const id = useId();
  const headingId = `${id}-heading`;
  const dialogTitleId = `${id}-trade-title`;
  const dialogSummaryId = `${id}-trade-summary`;

  const activePeriod = period ?? internalPeriod;
  const activeBreakdown = breakdown ?? internalBreakdown;
  const selectedData = data[activePeriod];
  const rows = selectedData?.rows[activeBreakdown] ?? [];

  const choosePeriod = (next: HistoryPeriod) => {
    if (period === undefined) setInternalPeriod(next);
    onPeriodChange?.(next);
  };

  const chooseBreakdown = (next: HistoryBreakdown) => {
    if (breakdown === undefined) setInternalBreakdown(next);
    onBreakdownChange?.(next);
  };

  const openTrade = (trade: HistoryTradeRow, trigger: HTMLButtonElement) => {
    returnFocusRef.current = trigger;
    setSelectedTrade(trade);
  };

  const handleDialogClosed = () => {
    setSelectedTrade(null);
    const target = returnFocusRef.current;
    returnFocusRef.current = null;
    requestAnimationFrame(() => {
      if (target?.isConnected) target.focus();
    });
  };

  return (
    <section
      className={`rounded-xl border border-line bg-[#F6F8FE] p-4 text-ink sm:p-5 ${className}`}
      aria-labelledby={headingId}
      data-testid="history-explorer"
    >
      <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.14em] text-darkcard">Cobalt Ledger</p>
          <h2 id={headingId} className="mt-1 text-2xl font-bold text-ink">
            {title}
          </h2>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <fieldset>
            <legend className="mb-1.5 text-xs font-semibold text-muted">Period</legend>
            <div className="grid grid-cols-4 rounded-xl border border-line bg-white p-1">
              {PERIODS.map((item) => {
                const selected = item === activePeriod;
                return (
                  <button
                    key={item}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => choosePeriod(item)}
                    className={`min-h-[44px] rounded-lg px-3 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard ${
                      selected ? "bg-darkcard text-white" : "text-muted hover:bg-[#F1F3FB] hover:text-ink"
                    }`}
                  >
                    {item}
                  </button>
                );
              })}
            </div>
          </fieldset>

          <fieldset>
            <legend className="mb-1.5 text-xs font-semibold text-muted">Breakdown</legend>
            <div className="grid grid-cols-3 rounded-xl border border-line bg-white p-1">
              {BREAKDOWNS.map((item) => {
                const selected = item === activeBreakdown;
                return (
                  <button
                    key={item}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => chooseBreakdown(item)}
                    className={`min-h-[44px] rounded-lg px-3 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard ${
                      selected ? "bg-darkcard text-white" : "text-muted hover:bg-[#F1F3FB] hover:text-ink"
                    }`}
                  >
                    {item}
                  </button>
                );
              })}
            </div>
          </fieldset>
        </div>
      </div>
      <p className="sr-only" aria-live="polite">
        {activePeriod} period, grouped by {activeBreakdown.toLowerCase()}.
      </p>

      {selectedData ? (
        <>
          <div className="mt-5 rounded-xl border border-line bg-white p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="text-xs font-semibold text-muted">{activePeriod} account change</p>
                <div className="mt-1">
                  <ResultPair
                    dollars={selectedData.summary.changeDollars}
                    percent={selectedData.summary.changePercent}
                    prominent
                  />
                </div>
                <div className="mt-2">
                  <Baseline
                    label={selectedData.summary.baselineLabel}
                    value={selectedData.summary.baselineEquity}
                  />
                </div>
              </div>

              <div className="sm:text-right">
                <CompletenessBadge completeness={selectedData.summary.completeness} />
                <p className="mt-2 text-xs text-muted">Current equity</p>
                <p className="text-lg font-bold tabular-nums text-ink">
                  {formatMoney(selectedData.summary.currentEquity)}
                </p>
              </div>
            </div>

            {(selectedData.summary.finishedTradeResult !== undefined ||
              selectedData.summary.finishedTradeCount !== undefined) && (
              <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 border-t border-line pt-4 text-sm">
                {selectedData.summary.finishedTradeResult !== undefined && (
                  <p>
                    <span className="text-muted">Finished trades </span>
                    <span className={`font-semibold tabular-nums ${resultTone(selectedData.summary.finishedTradeResult)}`}>
                      {formatSignedMoney(selectedData.summary.finishedTradeResult)}
                    </span>
                  </p>
                )}
                {selectedData.summary.finishedTradeCount !== undefined && (
                  <p>
                    <span className="text-muted">Count </span>
                    <span className="font-semibold tabular-nums text-ink">
                      {formatCount(selectedData.summary.finishedTradeCount)}
                    </span>
                  </p>
                )}
              </div>
            )}

            {selectedData.summary.completenessNote && (
              <p className="mt-3 text-xs leading-5 text-muted">{selectedData.summary.completenessNote}</p>
            )}
          </div>

          <div className="mt-5 flex items-baseline justify-between gap-3">
            <h3 className="text-lg font-bold text-ink">{activeBreakdown}</h3>
            <span className="text-xs text-muted">{formatCount(rows.length)} rows</span>
          </div>

          {rows.length > 0 ? (
            <ul className="mt-3 grid gap-3">
              {rows.map((row) => {
                if (row.kind === "day") return <DayRow key={row.id} row={row} />;
                if (row.kind === "plan") return <PlanRow key={row.id} row={row} />;
                return <TradeRow key={row.id} row={row} onOpen={openTrade} />;
              })}
            </ul>
          ) : (
            <p className="mt-3 rounded-xl border border-dashed border-line bg-white px-4 py-8 text-center text-sm text-muted">
              {emptyMessage}
            </p>
          )}
        </>
      ) : (
        <p className="mt-5 rounded-xl border border-dashed border-line bg-white px-4 py-8 text-center text-sm text-muted">
          No {activePeriod.toLowerCase()} data is available.
        </p>
      )}

      {selectedTrade && (
        <TradeDialog
          trade={selectedTrade}
          onClosed={handleDialogClosed}
          titleId={dialogTitleId}
          summaryId={dialogSummaryId}
        />
      )}
    </section>
  );
}
