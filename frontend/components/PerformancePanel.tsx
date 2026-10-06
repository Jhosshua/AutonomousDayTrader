"use client";

import { useId, useMemo } from "react";

export type PerformancePeriod = "day" | "week" | "month" | "all";

export interface PerformanceChartPoint {
  label: string;
  value: number;
}

export interface PerformanceDayFact {
  dateLabel: string;
  dollarChange: number;
  percentChange: number;
}

export type PerformancePanelStatus =
  | { kind: "ready" }
  | { kind: "loading"; message?: string }
  | { kind: "error"; message: string }
  | { kind: "reconnecting"; message?: string };

export type PerformanceHistoryState =
  | { kind: "complete" }
  | { kind: "partial"; message: string };

export interface PerformancePanelProps {
  period: PerformancePeriod;
  onPeriodChange: (period: PerformancePeriod) => void;
  headline?: string;
  equity: number | null;
  dollarChange: number | null;
  percentChange: number | null;
  comparisonBasis: string;
  finishedTradeResult: number | null;
  openHoldingsResult: number | null;
  tradeCount?: number;
  chartLabel: string;
  chartPoints: PerformanceChartPoint[];
  bestDay: PerformanceDayFact | null;
  worstDay: PerformanceDayFact | null;
  status: PerformancePanelStatus;
  historyState: PerformanceHistoryState;
  className?: string;
}

const PERIODS: ReadonlyArray<{ value: PerformancePeriod; label: string }> = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "all", label: "All" },
];

const MONEY = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const NUMBER = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function finite(value: number | null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatMoney(value: number | null): string {
  const safe = finite(value);
  return safe === null ? "Unavailable" : MONEY.format(safe);
}

function formatSignedMoney(value: number | null): string {
  const safe = finite(value);
  if (safe === null) return "Unavailable";
  if (safe === 0) return MONEY.format(0);
  return `${safe > 0 ? "+" : "−"}${MONEY.format(Math.abs(safe))}`;
}

function formatSignedPercent(value: number | null): string {
  const safe = finite(value);
  if (safe === null) return "Unavailable";
  if (safe === 0) return "0.00%";
  return `${safe > 0 ? "+" : "−"}${NUMBER.format(Math.abs(safe))}%`;
}

function resultTone(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "bg-ground text-muted";
  if (value > 0) return "bg-gainbg text-gain";
  if (value < 0) return "bg-lossbg text-loss";
  return "bg-ground text-muted";
}

function textTone(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "text-muted";
  if (value > 0) return "text-gain";
  if (value < 0) return "text-loss";
  return "text-muted";
}

interface ChartGeometry {
  areaPath: string;
  linePath: string;
  points: Array<PerformanceChartPoint & { x: number; y: number }>;
  gridLines: number[];
}

function makeChartGeometry(chartPoints: PerformanceChartPoint[]): ChartGeometry {
  const width = 720;
  const height = 240;
  const horizontalPadding = 12;
  const verticalPadding = 18;
  const points = chartPoints
    .filter((point) => Number.isFinite(point.value))
    .map((point) => ({ ...point }));

  if (points.length === 0) {
    return { areaPath: "", linePath: "", points: [], gridLines: [18, 120, 222] };
  }

  let minimum = points[0].value;
  let maximum = points[0].value;
  for (const point of points) {
    minimum = Math.min(minimum, point.value);
    maximum = Math.max(maximum, point.value);
  }

  const rawSpan = maximum - minimum;
  const minimumSpan = Math.max(Math.abs(maximum) * 0.02, 1);
  const span = Math.max(rawSpan, minimumSpan);
  const lowerBound = minimum - span * 0.1;
  const upperBound = maximum + span * 0.1;
  const chartSpan = upperBound - lowerBound;

  const plotted = points.map((point, index) => {
    const x =
      points.length === 1
        ? width / 2
        : horizontalPadding +
          (index / (points.length - 1)) * (width - horizontalPadding * 2);
    const y =
      height -
      verticalPadding -
      ((point.value - lowerBound) / chartSpan) *
        (height - verticalPadding * 2);
    return { ...point, x, y };
  });

  const linePath = plotted
    .map(
      (point, index) =>
        `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`,
    )
    .join(" ");
  const first = plotted[0];
  const last = plotted[plotted.length - 1];
  const areaPath =
    plotted.length > 1
      ? `${linePath} L${last.x.toFixed(2)},${(
          height - verticalPadding
        ).toFixed(2)} L${first.x.toFixed(2)},${(
          height - verticalPadding
        ).toFixed(2)} Z`
      : "";

  return {
    areaPath,
    linePath,
    points: plotted,
    gridLines: [verticalPadding, height / 2, height - verticalPadding],
  };
}

function DayFact({
  label,
  fact,
  loading,
}: {
  label: "Best day" | "Worst day";
  fact: PerformanceDayFact | null;
  loading: boolean;
}) {
  return (
    <div className="min-w-0 rounded-xl border border-line bg-white p-4">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">
        {label}
      </dt>
      {loading ? (
        <dd className="mt-3">
          <LoadingValue />
          <span className="sr-only">Loading {label.toLowerCase()}</span>
        </dd>
      ) : fact ? (
        <dd className="mt-2 flex min-w-0 flex-col gap-1">
          <span className="truncate text-sm font-semibold text-ink">
            {fact.dateLabel}
          </span>
          <span
            className={`text-lg font-bold tabular-nums ${textTone(fact.dollarChange)}`}
          >
            {formatSignedMoney(fact.dollarChange)}
          </span>
          <span
            className={`text-sm font-semibold tabular-nums ${textTone(fact.percentChange)}`}
          >
            {formatSignedPercent(fact.percentChange)}
          </span>
        </dd>
      ) : (
        <dd className="mt-2 text-sm text-muted">Not enough history</dd>
      )}
    </div>
  );
}

function LoadingValue({ wide = false }: { wide?: boolean }) {
  return (
    <span
      className={`block h-8 rounded-lg bg-line ${wide ? "w-44 max-w-full" : "w-28 max-w-full"}`}
      aria-hidden="true"
    />
  );
}

export default function PerformancePanel({
  period,
  onPeriodChange,
  headline = "Account performance",
  equity,
  dollarChange,
  percentChange,
  comparisonBasis,
  finishedTradeResult,
  openHoldingsResult,
  tradeCount,
  chartLabel,
  chartPoints,
  bestDay,
  worstDay,
  status,
  historyState,
  className = "",
}: PerformancePanelProps) {
  const id = useId();
  const isLoading = status.kind === "loading";
  const geometry = useMemo(() => makeChartGeometry(chartPoints), [chartPoints]);
  const firstPoint = geometry.points[0];
  const middlePoint = geometry.points[Math.floor((geometry.points.length - 1) / 2)];
  const lastPoint = geometry.points[geometry.points.length - 1];
  const periodLabel = PERIODS.find((item) => item.value === period)?.label ?? "All";

  const textSummary = [
    `${periodLabel} performance.`,
    `Equity ${formatMoney(equity)}.`,
    `Account change ${formatSignedMoney(dollarChange)}, ${formatSignedPercent(percentChange)}, compared with ${comparisonBasis}.`,
    `Finished trade result ${formatSignedMoney(finishedTradeResult)}.`,
    `Open holdings result since entry ${formatSignedMoney(openHoldingsResult)}.`,
    bestDay
      ? `Best day ${bestDay.dateLabel}, ${formatSignedMoney(bestDay.dollarChange)}, ${formatSignedPercent(bestDay.percentChange)}.`
      : "Best day is not available.",
    worstDay
      ? `Worst day ${worstDay.dateLabel}, ${formatSignedMoney(worstDay.dollarChange)}, ${formatSignedPercent(worstDay.percentChange)}.`
      : "Worst day is not available.",
  ].join(" ");

  return (
    <section
      className={`min-w-0 overflow-hidden rounded-xl border border-line bg-white text-ink ${className}`}
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-summary`}
      aria-busy={isLoading}
      data-testid="performance-panel"
    >
      <div className="flex min-w-0 flex-col gap-4 border-b border-line p-4 sm:p-5">
        <div className="flex min-w-0 flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-darkcard">
              Performance
            </p>
            <h2
              id={`${id}-title`}
              className="mt-1 text-2xl font-bold tracking-tight text-ink"
            >
              {headline}
            </h2>
          </div>

          <div
            className="grid min-w-0 grid-cols-4 rounded-xl border border-line bg-ground p-1 lg:w-[24rem]"
            aria-label="Performance period"
            role="group"
          >
            {PERIODS.map((item) => {
              const selected = item.value === period;
              return (
                <button
                  key={item.value}
                  type="button"
                  onClick={() => onPeriodChange(item.value)}
                  aria-pressed={selected}
                  className={`min-h-[44px] min-w-0 rounded-lg px-2 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard ${
                    selected
                      ? "bg-darkcard text-white"
                      : "text-muted hover:bg-white hover:text-ink"
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
        <p className="sr-only" aria-live="polite">
          {periodLabel} selected. Account change {formatSignedMoney(dollarChange)}, {formatSignedPercent(percentChange)}.
        </p>

        {status.kind === "loading" && (
          <div
            className="rounded-xl border border-line bg-ground px-4 py-3 text-sm text-muted"
            role="status"
          >
            {status.message ?? "Loading performance"}
          </div>
        )}
        {status.kind === "error" && (
          <div
            className="rounded-xl border border-loss bg-lossbg px-4 py-3 text-sm font-semibold text-loss"
            role="alert"
          >
            {status.message}
          </div>
        )}
        {status.kind === "reconnecting" && (
          <div
            className="rounded-xl border border-warn bg-warnbg px-4 py-3 text-sm font-semibold text-warn"
            role="status"
          >
            {status.message ?? "Reconnecting to live performance"}
          </div>
        )}
        {historyState.kind === "partial" && (
          <div
            className="rounded-xl border border-warn bg-warnbg px-4 py-3 text-sm text-warn"
            role="status"
          >
            <span className="font-semibold">Partial history.</span>{" "}
            {historyState.message}
          </div>
        )}
      </div>

      <div className="grid min-w-0 gap-5 p-4 sm:p-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(18rem,0.55fr)]">
        <div className="min-w-0">
          <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <div className="min-w-0 rounded-xl bg-ground p-4 sm:p-5">
              <p className="text-sm font-medium text-muted">Current equity</p>
              {isLoading ? (
                <div className="mt-3">
                  <LoadingValue wide />
                </div>
              ) : (
                <p className="mt-1 break-words text-3xl font-bold tracking-tight tabular-nums text-ink sm:text-4xl">
                  {formatMoney(equity)}
                </p>
              )}
            </div>

            <div
              className={`min-w-0 rounded-xl p-4 sm:p-5 ${resultTone(dollarChange)}`}
            >
              <p className="text-sm font-medium">Account change</p>
              {isLoading ? (
                <div className="mt-3">
                  <LoadingValue />
                </div>
              ) : (
                <>
                  <p className="mt-1 break-words text-2xl font-bold tabular-nums">
                    {formatSignedMoney(dollarChange)}
                  </p>
                  <p className="mt-1 text-lg font-bold tabular-nums">
                    {formatSignedPercent(percentChange)}
                  </p>
                </>
              )}
              <p className="mt-2 text-xs leading-5">
                Compared with {comparisonBasis}
              </p>
            </div>
          </div>

          <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2">
            <div className="min-w-0 rounded-xl border border-line p-4">
              <p className="text-sm font-medium text-muted">
                Finished trade result
              </p>
              {isLoading ? (
                <div className="mt-3">
                  <LoadingValue />
                </div>
              ) : (
                <p
                  className={`mt-1 break-words text-xl font-bold tabular-nums ${textTone(finishedTradeResult)}`}
                >
                  {formatSignedMoney(finishedTradeResult)}
                </p>
              )}
            </div>

            <div className="min-w-0 rounded-xl border border-line p-4">
              <p className="text-sm font-medium text-muted">
                Open holdings result
              </p>
              {isLoading ? (
                <div className="mt-3">
                  <LoadingValue />
                </div>
              ) : (
                <p
                  className={`mt-1 break-words text-xl font-bold tabular-nums ${textTone(openHoldingsResult)}`}
                >
                  {formatSignedMoney(openHoldingsResult)}
                </p>
              )}
              <p className="mt-1 text-xs leading-5 text-muted">
                Since each holding was entered
              </p>
            </div>
          </div>
          {tradeCount !== undefined && (
            <p className="mt-3 text-sm text-muted">
              <span className="font-semibold tabular-nums text-ink">{tradeCount}</span>{" "}
              finished {tradeCount === 1 ? "trade" : "trades"} in this period
            </p>
          )}

          <figure
            className="mt-5 min-w-0 rounded-xl border border-line p-3 sm:p-4"
            aria-labelledby={`${id}-chart-title`}
            aria-describedby={`${id}-chart-description`}
          >
            <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
              <h3 id={`${id}-chart-title`} className="text-sm font-semibold text-ink">
                {chartLabel}
              </h3>
              <p className="text-xs text-muted">{periodLabel} view</p>
            </div>

            <p id={`${id}-chart-description`} className="sr-only">
              {geometry.points.length === 0
                ? "No chart points are available."
                : `${geometry.points.length} chart points from ${firstPoint.label}, ${formatMoney(firstPoint.value)}, to ${lastPoint.label}, ${formatMoney(lastPoint.value)}.`}
            </p>

            {isLoading ? (
              <div
                className="mt-4 h-52 rounded-xl bg-ground"
                aria-hidden="true"
              />
            ) : geometry.points.length === 0 ? (
              <div className="mt-4 flex h-52 items-center justify-center rounded-xl bg-ground px-4 text-center text-sm text-muted">
                No performance history is available for this period.
              </div>
            ) : (
              <>
                <svg
                  className="mt-4 h-52 w-full text-darkcard"
                  viewBox="0 0 720 240"
                  role="img"
                  aria-labelledby={`${id}-chart-title ${id}-chart-description`}
                  preserveAspectRatio="none"
                >
                  {geometry.gridLines.map((y) => (
                    <line
                      key={y}
                      x1="0"
                      x2="720"
                      y1={y}
                      y2={y}
                      className="text-line"
                      stroke="currentColor"
                      strokeWidth="1"
                      vectorEffect="non-scaling-stroke"
                    />
                  ))}
                  {geometry.areaPath && (
                    <path
                      d={geometry.areaPath}
                      fill="currentColor"
                      fillOpacity="0.1"
                    />
                  )}
                  <path
                    d={geometry.linePath}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    vectorEffect="non-scaling-stroke"
                  />
                  {geometry.points.length === 1 && (
                    <circle
                      cx={geometry.points[0].x}
                      cy={geometry.points[0].y}
                      r="5"
                      fill="currentColor"
                      vectorEffect="non-scaling-stroke"
                    />
                  )}
                  {geometry.points.length > 1 && (
                    <circle
                      cx={lastPoint.x}
                      cy={lastPoint.y}
                      r="5"
                      fill="white"
                      stroke="currentColor"
                      strokeWidth="3"
                      vectorEffect="non-scaling-stroke"
                    />
                  )}
                </svg>

                <div
                  className="mt-2 flex min-w-0 justify-between gap-2 text-xs text-muted"
                  aria-hidden="true"
                >
                  <span className="min-w-0 truncate">{firstPoint.label}</span>
                  {geometry.points.length > 2 && (
                    <span className="min-w-0 truncate text-center">
                      {middlePoint.label}
                    </span>
                  )}
                  {geometry.points.length > 1 && (
                    <span className="min-w-0 truncate text-right">
                      {lastPoint.label}
                    </span>
                  )}
                </div>

                <ol className="sr-only">
                  {geometry.points.map((point, index) => (
                    <li key={`${point.label}-${index}`}>
                      {point.label}, {formatMoney(point.value)}
                    </li>
                  ))}
                </ol>
              </>
            )}

            <figcaption
              id={`${id}-summary`}
              className="sr-only"
            >
              {isLoading ? "Performance summary is loading." : textSummary}
            </figcaption>
          </figure>
        </div>

        <aside className="min-w-0 rounded-xl bg-ground p-4 sm:p-5">
          <h3 className="text-lg font-bold text-ink">
            Daily range
          </h3>
          <p className="mt-1 text-sm leading-6 text-muted">
            Best and worst account changes in the selected period.
          </p>
          <dl className="mt-4 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-1">
            <DayFact label="Best day" fact={bestDay} loading={isLoading} />
            <DayFact label="Worst day" fact={worstDay} loading={isLoading} />
          </dl>
        </aside>
      </div>
    </section>
  );
}
